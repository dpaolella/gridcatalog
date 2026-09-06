"""Search backend protocol and its two implementations (ADR-0002).

The search index is derived state. It is rebuilt from the graph by
``datahub.projector.reindex`` and never written to as a source of truth.

``InMemorySearchBackend`` is a real BM25 index with prefix expansion and facet
aggregation, not a stub: the list view, the facets and the search-while-typing
behaviour the product is judged on all need to be testable without a container.
``OpenSearchBackend`` is the production path and must agree with it; the parity
suite asserts that.
"""

from __future__ import annotations

import bisect
import math
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from datahub.api.search.document import (
    FACET_FIELDS,
    SORT_FIELDS,
    SearchDocument,
)

_TOKEN = re.compile(r"[a-z0-9]+")

#: Free-text fields and their score multipliers.
FIELD_BOOSTS: dict[str, float] = {
    "title": 6.0,
    "summary": 3.0,
    "keywords": 2.5,
    "publisher": 2.0,
    "description": 1.0,
}

_BM25_K1 = 1.2
_BM25_B = 0.75


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


# ---------------------------------------------------------------------------
# Request and response
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Entitlement:
    """The visibility predicate, compiled into every query (ADR-0006).

    There is no way to construct a search request without one: the parameter is
    required, and an anonymous caller passes ``Entitlement.anonymous()``.
    """

    principal_id: str | None = None
    #: The caller's verified address, matched against allow-list grants made by
    #: email. `AllowlistRepository.entitled_principals` projects ids *and*
    #: addresses into the document precisely so a grant made before its subject
    #: had an account keeps working — but nothing here read the address, so
    #: every email grant was recorded, displayed to the custodian as active, and
    #: matched by nobody.
    email: str | None = None
    custodian_of: frozenset[str] = frozenset()
    #: Set only for the steward UI, which reads the draft graph deliberately.
    include_unconfirmed: bool = False
    #: A steward or admin sees restricted metadata in full.
    is_steward: bool = False

    @classmethod
    def anonymous(cls) -> Entitlement:
        return cls()

    def can_see_existence(self, doc: SearchDocument) -> bool:
        if doc.visibility == "allowlisted-existence":
            return self._entitled(doc)
        return True

    def can_see_full_metadata(self, doc: SearchDocument) -> bool:
        if doc.visibility == "public":
            return True
        return self._entitled(doc)

    def _entitled(self, doc: SearchDocument) -> bool:
        if self.is_steward:
            return True
        if self.principal_id is None:
            return False
        if doc.custodian_id and doc.custodian_id in self.custodian_of:
            return True
        if doc.custodian_id == self.principal_id:
            return True
        if self.principal_id in doc.entitled_principals:
            return True
        # Case-insensitively, because an address is not case-sensitive in the
        # half that matters and a custodian typing it with different casing than
        # the identity provider returned should not silently grant nothing.
        email = self.email
        if not email:
            return False
        return email.lower() in {value.lower() for value in doc.entitled_principals}


@dataclass(frozen=True, slots=True)
class RangeFilter:
    gte: Any = None
    lte: Any = None


@dataclass(frozen=True, slots=True)
class BBoxFilter:
    """Geographic filter. ``intersects`` is the only supported relation: a user
    looking for German data wants a global dataset returned, not excluded."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


@dataclass(frozen=True, slots=True)
class SortSpec:
    field: str
    descending: bool = False


@dataclass(frozen=True, slots=True)
class SearchRequest:
    entitlement: Entitlement
    q: str | None = None
    #: field name (a key of FACET_FIELDS) -> accepted values, OR within a field,
    #: AND across fields.
    filters: dict[str, list[Any]] = field(default_factory=dict)
    ranges: dict[str, RangeFilter] = field(default_factory=dict)
    bbox: BBoxFilter | None = None
    #: Temporal overlap window; a dataset matches if its coverage intersects it.
    temporal: RangeFilter | None = None
    sort: tuple[SortSpec, ...] = ()
    offset: int = 0
    limit: int = 20
    facets: tuple[str, ...] = ()
    #: Prefix-expand the final token, for search-while-typing.
    prefix_last_token: bool = True
    ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        for name in self.filters:
            if name not in FACET_FIELDS:
                raise ValueError(f"unknown filter field: {name}")
        for name in self.facets:
            if name not in FACET_FIELDS:
                raise ValueError(f"unknown facet field: {name}")
        for spec in self.sort:
            if spec.field not in SORT_FIELDS:
                raise ValueError(f"unknown sort field: {spec.field}")


@dataclass(frozen=True, slots=True)
class FacetValue:
    value: Any
    count: int
    label: str | None = None


@dataclass(frozen=True, slots=True)
class Hit:
    document: SearchDocument
    score: float
    #: False when the caller may see that the record exists but not its detail.
    full_metadata: bool = True


@dataclass(frozen=True, slots=True)
class SearchResponse:
    total: int
    hits: list[Hit]
    facets: dict[str, list[FacetValue]] = field(default_factory=dict)
    took_ms: float = 0.0


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class SearchBackend(ABC):
    @abstractmethod
    def index(self, documents: Iterable[SearchDocument]) -> int:
        """Upsert documents. Returns the number written."""

    @abstractmethod
    def delete(self, ids: Iterable[str]) -> int: ...

    @abstractmethod
    def get(self, doc_id: str) -> SearchDocument | None: ...

    @abstractmethod
    def search(self, request: SearchRequest) -> SearchResponse: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def clear(self) -> None:
        """Drop the index. Safe: it is derived state."""

    def refresh(self) -> None:
        """Make recent writes visible. No-op where writes are visible at once."""

    def flush(self) -> None: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Shared filtering logic — identical semantics on both backends
# ---------------------------------------------------------------------------


def extract(doc: SearchDocument, path: str) -> list[Any]:
    """Read a dotted path out of a document, flattening lists.

    ``data_domains.iri`` on a document with two domains yields both IRIs.
    """
    values: list[Any] = [doc]
    for part in path.split("."):
        nxt: list[Any] = []
        for value in values:
            if value is None:
                continue
            if isinstance(value, list):
                for item in value:
                    nxt.append(_attr(item, part))
            else:
                nxt.append(_attr(value, part))
        values = [v for v in _flatten(nxt) if v is not None]
    return values


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _flatten(items: Sequence[Any]) -> Iterable[Any]:
    for item in items:
        if isinstance(item, list):
            yield from item
        else:
            yield item


def matches_filters(doc: SearchDocument, request: SearchRequest) -> bool:
    if request.ids is not None and doc.id not in request.ids:
        return False
    if not request.entitlement.include_unconfirmed and doc.review_state != "confirmed":
        return False
    if not request.entitlement.can_see_existence(doc):
        return False
    for name, accepted in request.filters.items():
        found = extract(doc, FACET_FIELDS[name])
        if not any(_eq(v, a) for v in found for a in accepted):
            return False
    for name, rng in request.ranges.items():
        path = FACET_FIELDS.get(name) or SORT_FIELDS.get(name)
        if path is None:
            return False
        found = [v for v in extract(doc, path) if v is not None]
        if not found:
            return False
        value = found[0]
        if rng.gte is not None and value < rng.gte:
            return False
        if rng.lte is not None and value > rng.lte:
            return False
    if request.bbox is not None and not _bbox_intersects(doc, request.bbox):
        return False
    return not (request.temporal is not None and not _temporal_intersects(doc, request.temporal))


def _eq(value: Any, accepted: Any) -> bool:
    if isinstance(value, bool) or isinstance(accepted, bool):
        return bool(value) == bool(accepted)
    if isinstance(value, int) and isinstance(accepted, str) and accepted.isdigit():
        return value == int(accepted)
    return str(value) == str(accepted)


def _bbox_intersects(doc: SearchDocument, box: BBoxFilter) -> bool:
    bbox = doc.spatial.bbox
    if not bbox or len(bbox) != 4:
        # No declared extent means "not captured", not "does not overlap"
        # (PRD principle 2). Excluding it would hide global datasets.
        return True
    min_lon, min_lat, max_lon, max_lat = bbox
    return not (
        max_lon < box.min_lon
        or min_lon > box.max_lon
        or max_lat < box.min_lat
        or min_lat > box.max_lat
    )


def _temporal_intersects(doc: SearchDocument, window: RangeFilter) -> bool:
    start, end = doc.temporal.start, doc.temporal.end
    if start is None and end is None:
        return True
    if window.gte is not None and end is not None and end < window.gte:
        return False
    return not (window.lte is not None and start is not None and start > window.lte)


def compute_facets(
    docs: Sequence[SearchDocument], names: Sequence[str], limit: int = 50
) -> dict[str, list[FacetValue]]:
    """Facet counts over an already-filtered set.

    Counted post-filter, so a record the caller may not see contributes to no
    count — which is what stops existence leaking through a facet (ADR-0006).
    """
    out: dict[str, list[FacetValue]] = {}
    for name in names:
        path = FACET_FIELDS[name]
        counts: dict[Any, int] = defaultdict(int)
        labels: dict[Any, str] = {}
        for doc in docs:
            seen: set[Any] = set()
            for value in extract(doc, path):
                key = value if isinstance(value, bool | int) else str(value)
                if key in seen:
                    continue
                seen.add(key)
                counts[key] += 1
            if path.endswith(".iri"):
                base = path.rsplit(".", 1)[0]
                for concept in extract(doc, base):
                    iri = _attr(concept, "iri")
                    label = _attr(concept, "label")
                    if iri is not None and label:
                        labels[str(iri)] = str(label)
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:limit]
        out[name] = [FacetValue(v, c, labels.get(v)) for v, c in ordered]
    return out


def sort_key(doc: SearchDocument, specs: Sequence[SortSpec]) -> tuple[Any, ...]:
    key: list[Any] = []
    for spec in specs:
        values = extract(doc, SORT_FIELDS[spec.field])
        value = values[0] if values else None
        key.append(_SortWrapper(value, spec.descending))
    return tuple(key)


class _SortWrapper:
    """Orders mixed and missing values deterministically; missing sorts last."""

    __slots__ = ("descending", "value")

    def __init__(self, value: Any, descending: bool) -> None:
        self.value = value
        self.descending = descending

    def _rank(self) -> tuple[int, Any]:
        if self.value is None:
            return (1, "")
        if isinstance(self.value, datetime):
            return (0, self.value.timestamp())
        if isinstance(self.value, bool):
            return (0, int(self.value))
        if isinstance(self.value, int | float):
            return (0, float(self.value))
        return (0, str(self.value).casefold())

    def __lt__(self, other: _SortWrapper) -> bool:
        a, b = self._rank(), other._rank()
        if a[0] != b[0]:  # missing always last, in both directions
            return a[0] < b[0]
        if type(a[1]) is not type(b[1]):
            a, b = (a[0], str(a[1])), (b[0], str(b[1]))
        return b[1] < a[1] if self.descending else a[1] < b[1]

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _SortWrapper) and self._rank() == other._rank()


# ---------------------------------------------------------------------------
# In-process backend
# ---------------------------------------------------------------------------


class InMemorySearchBackend(SearchBackend):
    """BM25 over a per-field inverted index, with prefix expansion.

    Persists to a JSON-lines file when given a path, so a dev server survives a
    restart without a container. The index is derived state; losing it costs a
    reindex, not data.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        self._docs: dict[str, SearchDocument] = {}
        self._postings: dict[str, dict[str, dict[str, int]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        self._lengths: dict[str, dict[str, int]] = defaultdict(dict)
        self._terms_sorted: list[str] = []
        self._terms_dirty = True
        if self.path and self.path.exists():
            self._load()

    # -- write --

    def index(self, documents: Iterable[SearchDocument]) -> int:
        count = 0
        with self._lock:
            for doc in documents:
                self._remove_postings(doc.id)
                self._docs[doc.id] = doc
                for fname in FIELD_BOOSTS:
                    text = self._field_text(doc, fname)
                    tokens = tokenize(text)
                    self._lengths[fname][doc.id] = len(tokens)
                    freqs: dict[str, int] = defaultdict(int)
                    for token in tokens:
                        freqs[token] += 1
                    for token, freq in freqs.items():
                        self._postings[fname][token][doc.id] = freq
                count += 1
            self._terms_dirty = True
        return count

    @staticmethod
    def _field_text(doc: SearchDocument, name: str) -> str:
        if name == "keywords":
            return " ".join(
                doc.keywords
                + [c.label for c in doc.data_domains]
                + [c.label for c in doc.concepts]
                + [c.label for c in doc.supported_analysis]
                + doc.spatial.place_labels
                + doc.formats
                + ([doc.license_id] if doc.license_id else [])
            )
        if name == "publisher":
            return " ".join(filter(None, [doc.publisher, *doc.creators]))
        return getattr(doc, name, None) or ""

    def _remove_postings(self, doc_id: str) -> None:
        for fname, tokens in self._postings.items():
            empty = [
                t
                for t, posting in tokens.items()
                if posting.pop(doc_id, None) is not None and not posting
            ]
            for token in empty:
                tokens.pop(token, None)
            self._lengths[fname].pop(doc_id, None)

    def delete(self, ids: Iterable[str]) -> int:
        removed = 0
        with self._lock:
            for doc_id in ids:
                if self._docs.pop(doc_id, None) is not None:
                    self._remove_postings(doc_id)
                    removed += 1
            self._terms_dirty = True
        return removed

    def clear(self) -> None:
        with self._lock:
            self._docs.clear()
            self._postings.clear()
            self._lengths.clear()
            self._terms_sorted = []
            self._terms_dirty = False

    # -- read --

    def get(self, doc_id: str) -> SearchDocument | None:
        return self._docs.get(doc_id)

    def count(self) -> int:
        return len(self._docs)

    def all_documents(self) -> list[SearchDocument]:
        return list(self._docs.values())

    def search(self, request: SearchRequest) -> SearchResponse:
        started = time.perf_counter()
        with self._lock:
            candidates = [doc for doc in self._docs.values() if matches_filters(doc, request)]
            scores = self._score(request, candidates)
            if request.q and request.q.strip():
                candidates = [d for d in candidates if scores.get(d.id, 0.0) > 0.0]
            if request.sort:
                candidates.sort(key=lambda d: sort_key(d, request.sort))
            else:
                candidates.sort(key=lambda d: (-scores.get(d.id, 0.0), d.title.casefold()))
            facets = compute_facets(candidates, request.facets)
            total = len(candidates)
            page = candidates[request.offset : request.offset + request.limit]
            hits = [
                Hit(
                    document=doc,
                    score=scores.get(doc.id, 0.0),
                    full_metadata=request.entitlement.can_see_full_metadata(doc),
                )
                for doc in page
            ]
        return SearchResponse(
            total=total,
            hits=hits,
            facets=facets,
            took_ms=(time.perf_counter() - started) * 1000,
        )

    def _score(
        self, request: SearchRequest, candidates: Sequence[SearchDocument]
    ) -> dict[str, float]:
        if not request.q or not request.q.strip():
            return {}
        tokens = tokenize(request.q)
        if not tokens:
            return {}
        allowed = {d.id for d in candidates}
        scores: dict[str, float] = defaultdict(float)
        n_docs = max(len(self._docs), 1)
        for position, token in enumerate(tokens):
            is_last = position == len(tokens) - 1
            expansions = (
                self._expand_prefix(token) if is_last and request.prefix_last_token else [token]
            )
            for expanded in expansions:
                # A prefix expansion should not outscore an exact term match.
                damping = 1.0 if expanded == token else 0.35
                for fname, boost in FIELD_BOOSTS.items():
                    posting = self._postings[fname].get(expanded)
                    if not posting:
                        continue
                    df = len(posting)
                    idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
                    lengths = self._lengths[fname]
                    avg_len = (sum(lengths.values()) / len(lengths)) if lengths else 1.0
                    for doc_id, freq in posting.items():
                        if doc_id not in allowed:
                            continue
                        dl = lengths.get(doc_id, 0)
                        denom = freq + _BM25_K1 * (1 - _BM25_B + _BM25_B * (dl / (avg_len or 1.0)))
                        scores[doc_id] += (
                            boost * damping * idf * (freq * (_BM25_K1 + 1) / (denom or 1.0))
                        )
        return dict(scores)

    def _expand_prefix(self, token: str, cap: int = 40) -> list[str]:
        if self._terms_dirty:
            terms: set[str] = set()
            for tokens in self._postings.values():
                terms.update(tokens)
            self._terms_sorted = sorted(terms)
            self._terms_dirty = False
        start = bisect.bisect_left(self._terms_sorted, token)
        out: list[str] = []
        for term in self._terms_sorted[start : start + cap * 4]:
            if not term.startswith(token):
                break
            out.append(term)
            if len(out) >= cap:
                break
        return out or [token]

    # -- persistence --

    def flush(self) -> None:
        if not self.path:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                for doc in self._docs.values():
                    handle.write(doc.model_dump_json() + "\n")
            tmp.replace(self.path)

    def _load(self) -> None:
        assert self.path is not None
        docs: list[SearchDocument] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    docs.append(SearchDocument.model_validate_json(line))
        self.index(docs)
