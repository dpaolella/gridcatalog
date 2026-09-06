"""Source-native metadata onto the OpenGrid schema (WP-3.5).

PRD §7.3:

> Map source-native metadata onto the OpenGrid JSON-LD schema. Per-adapter
> field mappings live in ``normalizers/mappings/*.yaml`` so they are editable
> without touching code. **Record which fields were populated from source
> versus left empty; this determines the initial completeness level.**

The declarative part is not decoration. Eight sources means eight mappings, and
a mapping expressed as code is a mapping only a Python programmer can fix — but
the person who notices that OEDI moved a field is a data steward reading a
harvest report. So the mapping is data: a path into the payload, an optional
transform from a fixed vocabulary of transforms, and a target term.

**Underscore-prefixed paths are adapter-derived.** A mapping path like
``_bbox_min_lon`` or ``_public`` does not exist in the source's own payload;
the adapter computed it and put it there. The convention marks the boundary:
anything without an underscore is a field the source publishes and a steward
can go and look at, anything with one is a fact this project derived and is
answerable for. Converting an S3 ARN to an https URL needs the region and the
bucket-naming rules, which is adapter knowledge; deciding whether a STAC
catalog's assets are anonymously readable needs to know which catalog it is.
Neither belongs in a field map.

**The rule about absence.** A field the source did not carry is left out. It is
never defaulted, never inferred, never filled with a plausible value. PRD
principle 2 — *absent means "not captured", never "no source"* — is the whole
reason the completeness level is computed from what actually arrived rather
than declared by the adapter: a normaliser that quietly defaulted a licence to
CC-BY would produce a level 1 record that lies, and nothing downstream could
tell.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from datahub.config import Settings, get_settings
from datahub.harvest.adapters.base import HarvestedRecord, slugify
from datahub.harvest.normalizers.classify import classify
from datahub.logging import get_logger
from datahub.namespaces import (
    DATASET_BASE,
    DISTRIBUTION_BASE,
    SCHEME_DATA_DOMAIN,
    SPDX,
)

log = get_logger(__name__)

MAPPING_DIR = Path(__file__).parent / "mappings"
LICENSE_MAP_PATH = Path(__file__).parents[1] / "seed-license-map.yaml"


@dataclass(slots=True)
class NormalizedRecord:
    """A record, plus what the normaliser knows about how it got that way."""

    document: dict[str, Any]
    #: Terms populated from the source payload.
    from_source: set[str] = field(default_factory=set)
    #: Terms the mapping wanted and the payload did not carry. Kept because
    #: "the source has no licence" is a fact a steward needs, and it is
    #: invisible if absence is silent.
    missing: set[str] = field(default_factory=set)
    #: Mapping problems: a transform that could not be applied, a value of the
    #: wrong shape. Not fatal — a partial record beats no record — but never
    #: silent.
    warnings: list[str] = field(default_factory=list)

    @property
    def completeness_level(self) -> int:
        return int(self.document.get("completenessLevel", 1))

    @property
    def dataset_id(self) -> str:
        return str(self.document["id"])


class Mapping:
    """One source's field mapping, loaded from YAML."""

    def __init__(self, name: str, spec: dict[str, Any]) -> None:
        self.name = name
        self.spec = spec
        self.identity: dict[str, Any] = spec.get("identity", {})
        self.fields: dict[str, Any] = spec.get("fields", {})
        self.distributions: dict[str, Any] = spec.get("distributions", {})
        self.defaults: dict[str, Any] = spec.get("defaults", {})
        self.notes: str = spec.get("notes", "")

    @property
    def target_terms(self) -> set[str]:
        return set(self.fields)


@functools.lru_cache(maxsize=16)
def load_mapping(name: str) -> Mapping:
    """Load one adapter's mapping. Cached: it is read per record otherwise."""
    path = MAPPING_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no field mapping for adapter {name!r} at {path}")
    return Mapping(name, yaml.safe_load(path.read_text()))


def mapping_names() -> list[str]:
    return sorted(p.stem for p in MAPPING_DIR.glob("*.yaml"))


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------
#
# A closed vocabulary. A mapping cannot express arbitrary computation, and that
# is the point: an open one becomes a second programming language that only
# looks like configuration, and the person editing it has no way to test it.

_ISO_DATE = re.compile(r"^(\d{4})(-\d{2})?(-\d{2})?")


def _t_text(value: Any) -> Any:
    """Collapse whitespace. Source descriptions arrive with hard-wrapped lines
    and HTML indentation, which turns into ragged text in the UI."""
    if value is None:
        return None
    return " ".join(str(value).split()) or None


def _t_strip_html(value: Any) -> Any:
    """Zenodo and CKAN both put HTML in description fields."""
    if value is None:
        return None
    text = re.sub(r"<br\s*/?>|</p>", " ", str(value), flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    return _t_text(text)


def _t_datetime(value: Any) -> Any:
    """An xsd:dateTime, or nothing.

    A date that cannot be parsed is dropped rather than guessed. A wrong
    ``modified`` timestamp is worse than a missing one: the Currency grade is
    computed from it, so a bad guess becomes a confident quality claim.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text.replace("+00:00", "Z"), fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    match = _ISO_DATE.match(text)
    if match:
        year, month, day = match.group(1), match.group(2) or "-01", match.group(3) or "-01"
        return f"{year}{month}{day}T00:00:00Z"
    return None


def _t_year(value: Any) -> Any:
    match = _ISO_DATE.match(str(value or ""))
    return f"{match.group(1)}-01-01T00:00:00Z" if match else None


def _t_iri(value: Any) -> Any:
    """Only an absolute http(s) IRI. A relative one resolves against whatever
    base happens to be in scope, which for a record loaded from a file is the
    file's own directory — that is how ``"CC BY 4.0"`` once became
    ``file:///home/user/gridcatalog/``."""
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else None


def _t_doi(value: Any) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("http"):
        return text
    return f"https://doi.org/{text.removeprefix('doi:').removeprefix('DOI:').strip()}"


def _t_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return str(value).strip().lower() in ("true", "yes", "1", "open", "public")


def _t_number(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _t_integer(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _t_list(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()] or None
    if isinstance(value, list):
        out = [_t_text(v) for v in value if v is not None]
        return [v for v in out if v] or None
    return [str(value)]


def _t_names(value: Any) -> Any:
    """CKAN tags, Zenodo keywords and STAC keywords are all "a list of things
    that might be strings or might be dicts with a name"."""
    if not isinstance(value, list):
        return _t_list(value)
    names = []
    for item in value:
        if isinstance(item, dict):
            for key in ("name", "display_name", "title", "subject"):
                if item.get(key):
                    names.append(_t_text(item[key]))
                    break
        elif item is not None:
            names.append(_t_text(item))
    return [n for n in names if n] or None


# ---------------------------------------------------------------------------
# Update cadence
# ---------------------------------------------------------------------------
#
# `og:updateCadence` is constrained by SHACL to an ISO 8601 duration or one of
# `irregular`, `on-demand`, `discontinued`, because the Currency grade compares
# elapsed time against it. Sources state it in prose: "Monthly", "Varies by
# dataset", "New data is added as soon as it is available."
#
# Mapping that prose with `transform: [text]` put free text into a
# pattern-constrained field and failed *every* record of a 1,199-record harvest
# — see docs/ingestion-plan.md §3. This transform is the fix, and its whole
# design is in the last rule:
#
#   **Unrecognised input drops the field.** An absent cadence is legal at every
#   completeness level and reads as "not captured". A guessed one is a schedule
#   claim the source never made, and the Currency grade turns it into a
#   confident quality judgement. Dropping is the honest failure.

#: Phrases naming a real interval. Matched longest-first, so "semi-annually"
#: cannot be swallowed by "annually".
_CADENCE_INTERVALS: tuple[tuple[str, str], ...] = (
    ("sub-hourly", "PT1H"),
    ("continuously", "PT1H"),
    ("continuous", "PT1H"),
    ("real-time", "PT1H"),
    ("real time", "PT1H"),
    ("hourly", "PT1H"),
    ("twice daily", "PT12H"),
    ("twice a day", "PT12H"),
    ("daily", "P1D"),
    ("every day", "P1D"),
    ("fortnightly", "P14D"),
    ("bi-weekly", "P14D"),
    ("biweekly", "P14D"),
    ("weekly", "P7D"),
    ("every week", "P7D"),
    ("bi-monthly", "P2M"),
    ("monthly", "P1M"),
    ("every month", "P1M"),
    ("quarterly", "P3M"),
    ("semi-annually", "P6M"),
    ("semiannually", "P6M"),
    ("biannually", "P6M"),
    ("twice a year", "P6M"),
    ("annually", "P1Y"),
    ("annual", "P1Y"),
    ("yearly", "P1Y"),
    ("every year", "P1Y"),
    ("biennially", "P2Y"),
)

#: Phrases meaning "updated, but to no schedule".
_CADENCE_IRREGULAR: tuple[str, ...] = (
    "varies",
    "periodically",
    "periodic",
    "occasionally",
    "occasional",
    "irregular",
    "sporadic",
    "intermittent",
    "as new data",
    "as data become",
    "as data becomes",
    "when new data",
    "as available",
    "as soon as",
    "as they become",
    "ongoing",
    "from time to time",
)

#: Phrases meaning "updated when somebody asks". Kept apart from `irregular`
#: because they are different claims and the Currency grade treats them so:
#: an on-demand dataset is not stale for not having changed.
_CADENCE_ON_DEMAND: tuple[str, ...] = (
    "as needed",
    "as required",
    "as necessary",
    "on demand",
    "on-demand",
    "on request",
    "need-to-update",
    "when required",
    "when needed",
)

#: Explicit negations of updating. Tested *before* the intervals, because
#: "not currently being updated" contains "update" and several of these
#: sentences also name the interval the dataset used to run at.
_CADENCE_NOT_UPDATED: tuple[str, ...] = (
    "not updated",
    "no longer updated",
    "no longer being updated",
    "not currently being updated",
    "no further update",
    "no updates",
    "no plans to update",
    "will not be updated",
    "never",
)

#: Other ways of saying "finished". Tested *after* the intervals, because a
#: stated interval is the stronger signal: "Updated monthly with complete
#: records" is monthly, and reading it as a closed archive would mis-grade
#: Currency on a live dataset. Bare "complete" and "final" are deliberately
#: absent — on their own they are too weak to carry the claim.
_CADENCE_CLOSED: tuple[str, ...] = (
    "static",
    "one-time",
    "one time",
    "single release",
    "discontinued",
    "historical archive",
    "fixed dataset",
    "final release",
    "no longer",
)

#: Already-valid values pass through untouched, so a source that speaks ISO
#: 8601 is not round-tripped through the phrase table.
_ISO_DURATION = re.compile(r"^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?!$)(\d+H)?(\d+M)?(\d+S)?)?$")
_CADENCE_ENUM = frozenset({"irregular", "on-demand", "discontinued"})

#: Values that say nothing at all. Distinguished from unrecognised prose only
#: for the log: both drop the field.
_CADENCE_EMPTY = frozenset({"", "n/a", "na", "none", "null", "-", "--", "unknown", "tbd", "?"})

#: The EU frequency authority, which is what DCAT-AP sources put in
#: `dct:accrualPeriodicity` — an IRI ending in a token, not prose. Matched
#: explicitly rather than left to the phrase scan: `IRREG` does not contain
#: "irregular", `CONT` does not contain "continuous", and `NEVER` matching
#: "never" by luck is not a thing to rely on across a vocabulary that adds
#: terms. Unlisted tokens fall through to the phrase scan and then to None.
_EU_FREQUENCY: dict[str, str] = {
    "HOURLY": "PT1H",
    "BIHOURLY": "PT2H",
    "TRIHOURLY": "PT3H",
    "DAILY": "P1D",
    "DAILY_2": "PT12H",
    "CONT": "PT1H",
    "UPDATE_CONT": "PT1H",
    "WEEKLY": "P7D",
    "WEEKLY_2": "P3D",
    "WEEKLY_3": "P2D",
    "BIWEEKLY": "P14D",
    "MONTHLY": "P1M",
    "MONTHLY_2": "P14D",
    "MONTHLY_3": "P10D",
    "BIMONTHLY": "P2M",
    "QUARTERLY": "P3M",
    "ANNUAL_2": "P6M",
    "ANNUAL": "P1Y",
    "ANNUAL_3": "P4M",
    "BIENNIAL": "P2Y",
    "TRIENNIAL": "P3Y",
    "DECENNIAL": "P10Y",
    "QUADRENNIAL": "P4Y",
    "QUINQUENNIAL": "P5Y",
    "IRREG": "irregular",
    "OTHER": "irregular",
    "NEVER": "discontinued",
    "AS_NEEDED": "on-demand",
    "NOT_PLANNED": "discontinued",
}


def _t_cadence(value: Any) -> Any:
    """Free-text update frequency to a value SHACL accepts, or nothing.

    Order matters, in both directions, and neither is arbitrary.

    Explicit negations go **first**: "not currently being updated" contains
    "update", and some of these sentences also name the interval the dataset
    used to run at, so an interval scan would read a closed archive as live.

    Vaguer "finished" wording goes **last**, after the intervals: "Updated
    monthly with complete records" is monthly, and reading it as closed would
    mis-grade Currency on a live dataset. Within every category the longest
    phrase wins, so "semi-annually" is not swallowed by "annually".
    """
    if value is None:
        return None
    raw = " ".join(str(value).split()).strip()
    if not raw:
        return None
    if raw in _CADENCE_ENUM or _ISO_DURATION.match(raw):
        return raw
    if raw.startswith(("http://", "https://")):
        token = raw.rstrip("/").rsplit("/", 1)[-1].upper()
        if mapped := _EU_FREQUENCY.get(token):
            return mapped
    text = raw.lower()
    if text in _CADENCE_EMPTY:
        return None
    for phrase in sorted(_CADENCE_NOT_UPDATED, key=len, reverse=True):
        if phrase in text:
            return "discontinued"
    for phrase, duration in sorted(_CADENCE_INTERVALS, key=lambda pair: -len(pair[0])):
        if phrase in text:
            return duration
    for phrase in sorted(_CADENCE_CLOSED, key=len, reverse=True):
        if phrase in text:
            return "discontinued"
    for phrase in sorted(_CADENCE_ON_DEMAND, key=len, reverse=True):
        if phrase in text:
            return "on-demand"
    for phrase in sorted(_CADENCE_IRREGULAR, key=len, reverse=True):
        if phrase in text:
            return "irregular"
    return None


def _t_first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "text": _t_text,
    "strip_html": _t_strip_html,
    "datetime": _t_datetime,
    "year": _t_year,
    "iri": _t_iri,
    "doi": _t_doi,
    "boolean": _t_bool,
    "number": _t_number,
    "integer": _t_integer,
    "list": _t_list,
    "names": _t_names,
    "first": _t_first,
    "cadence": _t_cadence,
}


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve(payload: Any, path: str) -> Any:
    """Read a dotted path out of a nested payload.

    ``a.b`` walks dicts. ``a[]`` maps the rest of the path over a list, which
    is how ``resources[].url`` reaches every CKAN resource URL. ``a|b`` takes
    the first path that yields something, which is how one mapping copes with a
    source that renamed a field and left the old one in place for compatibility.
    """
    for alternative in path.split("|"):
        value = _resolve_one(payload, alternative.strip())
        if value not in (None, [], {}, ""):
            return value
    return None


def _resolve_one(payload: Any, path: str) -> Any:
    segments = path.split(".")
    current = payload
    for index, segment in enumerate(segments):
        if current is None:
            return None
        if segment.endswith("[]"):
            key = segment[:-2]
            items = current.get(key) if isinstance(current, dict) else current
            if not isinstance(items, list):
                return None
            rest = ".".join(segments[index + 1 :])
            if not rest:
                return items
            # Map the remainder over the list and flatten one level, so
            # `resources[].url` yields every URL rather than a list of lists.
            out: list[Any] = []
            for item in items:
                value = _resolve_one(item, rest)
                if isinstance(value, list):
                    out.extend(v for v in value if v is not None)
                elif value is not None:
                    out.append(value)
            return out or None
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list):
            current = [item.get(segment) for item in current if isinstance(item, dict)] or None
        else:
            return None
    return current


# ---------------------------------------------------------------------------
# The normaliser
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Licence strings
# ---------------------------------------------------------------------------
#
# `seed-license-map.yaml` is matched by exact string, which is right for the
# curated seed inventory — a file somebody wrote — and wrong for harvested
# prose. All three of these are CC-BY-4.0 and all three missed a map that
# already contains it:
#
#   [Creative Commons BY 4.0](https://creativecommons.org/licenses/by/4.0/)
#   Creative Commons Attribution 4.0 International License
#   DE Africa makes this data available under the Creative Commons Attribute
#   4.0 license https://creativecommons.org/licenses/by/4.0/.
#
# This normalises far enough for the existing map to find them, and no further.
#
# **Unambiguous only.** Every pattern below names one licence *and* one
# version. A string saying "Creative Commons" with no version stays unresolved,
# because CC-BY and CC-BY-NC-SA are not the same permission and a reader who
# sees the wrong one has been actively misled (PRD §7.4). Anything unmatched is
# returned untouched and fails exactly as it does today.

_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

#: Ordered. The first match wins, so the more specific licence — the one with
#: an extra clause such as NC or SA — must be tested before the plain one, or
#: `by-nc-sa/4.0` resolves as `CC-BY-4.0` and grants a permission nobody gave.
_LICENCE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"creativecommons\.org/publicdomain/zero/1\.0", "CC0-1.0"),
    (r"creativecommons\.org/publicdomain/mark/1\.0", "CC-PDDC"),
    (r"creativecommons\.org/licenses/by-nc-sa/4\.0", "CC-BY-NC-SA-4.0"),
    (r"creativecommons\.org/licenses/by-nc-nd/4\.0", "CC-BY-NC-ND-4.0"),
    (r"creativecommons\.org/licenses/by-sa/4\.0", "CC-BY-SA-4.0"),
    (r"creativecommons\.org/licenses/by-nc/4\.0", "CC-BY-NC-4.0"),
    (r"creativecommons\.org/licenses/by-nd/4\.0", "CC-BY-ND-4.0"),
    (r"creativecommons\.org/licenses/by/4\.0", "CC-BY-4.0"),
    (r"creativecommons\.org/licenses/by-sa/3\.0", "CC-BY-SA-3.0"),
    (r"creativecommons\.org/licenses/by/3\.0", "CC-BY-3.0"),
    (r"opendatacommons\.org/licenses/odbl", "ODbL-1.0"),
    (r"opendatacommons\.org/licenses/by", "ODC-By-1.0"),
    (r"opendatacommons\.org/licenses/pddl", "PDDL-1.0"),
    (r"\bcc[\s\-]?by[\s\-]?nc[\s\-]?sa[\s\-]?4(\.0)?\b", "CC-BY-NC-SA-4.0"),
    (r"\bcc[\s\-]?by[\s\-]?nc[\s\-]?nd[\s\-]?4(\.0)?\b", "CC-BY-NC-ND-4.0"),
    (r"\bcc[\s\-]?by[\s\-]?sa[\s\-]?4(\.0)?\b", "CC-BY-SA-4.0"),
    (r"\bcc[\s\-]?by[\s\-]?nc[\s\-]?4(\.0)?\b", "CC-BY-NC-4.0"),
    (r"\bcc[\s\-]?by[\s\-]?nd[\s\-]?4(\.0)?\b", "CC-BY-ND-4.0"),
    (r"\bcc[\s\-]?by[\s\-]?4(\.0)?\b", "CC-BY-4.0"),
    (r"\bcc[\s\-]?by[\s\-]?sa[\s\-]?3(\.0)?\b", "CC-BY-SA-3.0"),
    (r"\bcc[\s\-]?by[\s\-]?3(\.0)?\b", "CC-BY-3.0"),
    (r"\bcc[\s\-]?zero\b|\bcc0\b", "CC0-1.0"),
    (
        r"creative commons attribution[\s\-]non[\s\-]?commercial[\s\-]share[\s\-]?alike 4\.0",
        "CC-BY-NC-SA-4.0",
    ),
    (r"creative commons attribution[\s\-]share[\s\-]?alike 4\.0", "CC-BY-SA-4.0"),
    (r"creative commons attribution[\s\-]non[\s\-]?commercial 4\.0", "CC-BY-NC-4.0"),
    (r"creative commons attribut\w* 4\.0", "CC-BY-4.0"),
    (r"creative commons attribut\w* 3\.0", "CC-BY-3.0"),
    (r"open database licen[cs]e", "ODbL-1.0"),
    (r"\bapache[\s\-]?(licen[cs]e[\s,]*)?(version[\s]*)?2(\.0)?\b", "Apache-2.0"),
    (r"\bmit licen[cs]e\b", "MIT"),
    (r"\bbsd[\s\-]?3[\s\-]?clause\b", "BSD-3-Clause"),
    (r"\bgpl[\s\-]?v?3(\.0)?\b", "GPL-3.0-only"),
)

_LICENCE_COMPILED: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), spdx) for pattern, spdx in _LICENCE_PATTERNS
)

#: Prose that grants public-domain-equivalent terms without naming a licence.
#: Kept separate because these resolve to a `LicenseRef` the map already
#: defines rather than to an SPDX identifier — the terms are real, the
#: identifier is not.
_PUBLIC_DOMAIN_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"usa\.gov/publicdomain",
        r"17 u\.?s\.?c\.? ?§? ?105",
        r"u\.?s\.? government work",
        r"united states government work",
        r"work of the (united states|u\.?s\.?) (federal )?government",
        r"public domain",
    )
)


def normalise_licence(raw: Any) -> Any:
    """A harvested licence string, normalised far enough to be looked up.

    Returns an SPDX identifier, the seed map's own key for a public-domain
    grant, or the input unchanged. Never a guess: an unmatched string comes
    back exactly as it went in, so the caller's existing "did not map"
    behaviour is preserved rather than papered over.
    """
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    if not text:
        return raw
    # `[label](url)` becomes `label url`, so a pattern can match either half.
    haystack = _MARKDOWN_LINK.sub(r"\1 \2", text).lower()
    for pattern, spdx in _LICENCE_COMPILED:
        if pattern.search(haystack):
            return spdx
    for pattern in _PUBLIC_DOMAIN_PATTERNS:
        if pattern.search(haystack):
            # The key `seed-license-map.yaml` already carries, so the note and
            # the reuse permissions come from the map rather than from here.
            return "US Government public domain"
    return raw


class Normalizer:
    """Applies one adapter's mapping to one harvested payload."""

    def __init__(
        self,
        mapping: Mapping | str,
        settings: Settings | None = None,
        *,
        source_domains: Sequence[str] | None = None,
    ) -> None:
        self.mapping = load_mapping(mapping) if isinstance(mapping, str) else mapping
        self.settings = settings or get_settings()
        #: The domains the harvest source declares for itself in
        #: ``data/seed-sources.yaml``. A prior for classification, never a gate.
        self.source_domains = list(source_domains or self.mapping.spec.get("domains", []))
        self._licences = yaml.safe_load(LICENSE_MAP_PATH.read_text())

    def normalize(self, record: HarvestedRecord) -> NormalizedRecord:
        payload = record.payload
        result = NormalizedRecord(document={})

        title = _t_text(resolve(payload, self.mapping.identity.get("title", "title")))
        if not title:
            # Without a title there is no slug, and without a slug there is no
            # stable identity. Rejecting here beats minting `unnamed-3`.
            result.warnings.append("no title in payload; cannot mint an identifier")
            result.document = {}
            return result

        slug = self._slug(payload, title)
        document: dict[str, Any] = {
            "@context": f"{self.settings.catalog_base_url}/context/opengrid-datahub.jsonld",
            "id": f"{DATASET_BASE}{slug}",
            "type": "Dataset",
            "title": title,
            # Harvested records are never confirmed. A steward confirms them
            # (PRD §7.6), and nothing in this pipeline may shortcut that.
            "reviewState": "draft",
            "harvestSource": record.source,
            "sourceRecordId": record.source_id,
            "visibility": "public",
        }
        result.from_source.add("title")

        for term, spec in self.mapping.fields.items():
            value, warning = self._field(payload, spec)
            if warning:
                result.warnings.append(f"{term}: {warning}")
            if value is None:
                result.missing.add(term)
                continue
            document[term] = value
            result.from_source.add(term)

        document.update({k: v for k, v in self.mapping.defaults.items() if k not in document})
        document.update(self._licence(document.pop("license", None), result))
        document.update(self._classify(payload, document, slug, result))
        distributions = self._distributions(payload, slug, result)
        if distributions:
            document["distribution"] = distributions
            result.from_source.add("distribution")
        else:
            result.missing.add("distribution")

        document["completenessLevel"] = self.level(document)
        document["qualityFlags"] = self._flags(result, slug)
        if "modified" not in document:
            document["modified"] = record.fetched_at.isoformat().replace("+00:00", "Z")

        result.document = document
        return result

    # ---- pieces ----------------------------------------------------------

    def _slug(self, payload: dict[str, Any], title: str) -> str:
        """A stable slug.

        Preferring a source-supplied stable name over the title, because a
        source that corrects a typo in a title must not thereby create a second
        record. Where the source has no such name the title is used and the
        adapter's ``source_record_id`` is what re-harvest actually matches on.
        """
        for path in self.mapping.identity.get("slug_from", []):
            candidate = _t_text(resolve(payload, path))
            if candidate:
                return slugify(str(candidate))
        return slugify(title)

    def _field(self, payload: dict[str, Any], spec: Any) -> tuple[Any, str | None]:
        if isinstance(spec, str):
            spec = {"path": spec}
        path = spec.get("path")
        value = resolve(payload, path) if path else None
        if value is None and "const" in spec:
            return spec["const"], None

        for name in _as_list(spec.get("transform")):
            transform = TRANSFORMS.get(name)
            if transform is None:
                return None, f"unknown transform {name!r}"
            try:
                value = transform(value)
            except Exception as exc:
                return None, f"transform {name!r} failed: {type(exc).__name__}"

        if value is None:
            return None, None
        if (values := spec.get("values")) and isinstance(values, dict):
            key = str(value).strip()
            if key not in values:
                # Not mapped is not the same as not present, and it is a
                # different fix: the mapping needs a new entry.
                return None, f"value {key!r} is not in the mapping's value table"
            value = values[key]
        if (prefix := spec.get("prefix")) and isinstance(value, str):
            value = f"{prefix}{value}"
        if spec.get("single") and isinstance(value, list):
            value = value[0] if value else None
        return value, None

    def _licence(self, raw: Any, result: NormalizedRecord) -> dict[str, Any]:
        """The same three outcomes as the seed loader, for the same reason.

        An SPDX identifier where the string is unambiguous; a LicenseRef with
        the real terms where it is not; a LicenseRef marking it unresolved with
        the original preserved. Never a guess — a reader who sees "CC-BY-4.0"
        on a dataset whose terms nobody checked has been actively misled
        (PRD §7.4).
        """
        if raw is None:
            result.missing.add("license")
            return {
                "license": f"{SPDX}LicenseRef-Unstated",
                "licenseNote": (
                    "The source record states no licence. Absent an explicit grant, default "
                    "copyright applies and reuse may not be permitted."
                ),
                "redistributionAllowed": False,
            }
        # Normalise before matching. The map is keyed on exact strings, which
        # is right for the hand-written seed inventory and useless against
        # harvested prose; `normalise_licence` closes that gap and returns the
        # input untouched when it cannot do so unambiguously.
        text = str(normalise_licence(raw)).strip()
        if text.startswith(("http://", "https://")):
            # Already an identifier: DCAT and STAC sources carry licence IRIs.
            result.from_source.add("license")
            return {"license": text}
        if spdx := self._licences["spdx"].get(text):
            result.from_source.add("license")
            return {"license": f"{SPDX}{spdx}"}
        if spdx := self._licences["spdx"].get(text.upper()):
            result.from_source.add("license")
            return {"license": f"{SPDX}{spdx}"}
        for bucket in ("license_ref", "dual"):
            if entry := self._licences[bucket].get(text):
                result.from_source.add("license")
                out: dict[str, Any] = {
                    "license": f"{SPDX}{entry['id']}",
                    "licenseNote": " ".join(str(entry["note"]).split()),
                }
                for key, term in (
                    ("redistribution_allowed", "redistributionAllowed"),
                    ("commercial_use_allowed", "commercialUseAllowed"),
                    ("share_alike", "shareAlike"),
                ):
                    if key in entry:
                        out[term] = entry[key]
                return out
        # Quote the source's own words, not the normalised form: a steward
        # resolving this needs to see what the record actually said.
        stated = " ".join(str(raw).split())
        result.warnings.append(f"licence {stated!r} did not map to a known identifier")
        return {
            "license": f"{SPDX}LicenseRef-Unreviewed-{slugify(stated, max_length=40)}",
            "licenseNote": (
                f'The source states the licence as "{stated}", which does not map to a known '
                "identifier. It has not been reviewed and must not be relied on."
            ),
            "redistributionAllowed": False,
        }

    def _classify(
        self,
        payload: dict[str, Any],
        document: dict[str, Any],
        slug: str,
        result: NormalizedRecord,
    ) -> dict[str, Any]:
        """Derive the two level-1 fields no source states outright.

        Domain and provenance get different treatment on purpose, and the
        difference is the point (see :mod:`datahub.harvest.normalizers.classify`):
        a domain is a filing decision, inferred and marked as inferred; a
        provenance class is a quality claim, set only where the source's own
        words determine it, and otherwise replaced by an explicit
        ``og:provenanceGap`` rather than by a guess — the catalog says "the
        source did not say" instead of asserting something false about how the
        numbers came to exist (ADR-0011).
        """
        text = " ".join(str(document.get(term, "")) for term in ("title", "description", "summary"))
        text += " " + " ".join(str(k) for k in (document.get("keyword") or []))
        text += " " + str(resolve(payload, self.mapping.identity.get("title", "title")) or "")

        found = classify(text, candidates=self.source_domains)
        out: dict[str, Any] = {}

        if "dataDomain" not in document:
            if found.domains:
                out["dataDomain"] = found.domain_iris
                out["inferredAssignment"] = True
                out["inferenceBasis"] = found.domain_basis
                result.from_source.discard("dataDomain")
            elif self.source_domains:
                # Last resort, and a weaker claim than the classifier's: the
                # harvest source's own declaration of what it carries, in
                # `seed-sources.yaml`. Filing a dataset in the drawer its
                # catalogue says it belongs to is a defensible default where
                # the record's own text says nothing, and it is *marked* as the
                # weaker claim it is, so a steward re-filing it can see that
                # nothing in the record supported the assignment.
                #
                # The alternative is what happened before: 30% of a real
                # harvest blocked below level 1 on a filing decision, which
                # makes the dataset invisible rather than mis-filed. A wrong
                # drawer is recoverable and visible; absence is neither.
                out["dataDomain"] = [f"{SCHEME_DATA_DOMAIN}/{d}" for d in self.source_domains]
                out["inferredAssignment"] = True
                out["inferenceBasis"] = (
                    "No domain term matched the record's own text. Filed under the domains "
                    f"the harvest source ({self.mapping.name}) declares for itself, which is "
                    "a claim about the catalogue rather than about this dataset."
                )
                result.from_source.discard("dataDomain")
                result.warnings.append(
                    "no data domain matched the source text; filed under the harvest source's "
                    "declared domains and marked inferred"
                )
            else:
                result.missing.add("dataDomain")
                result.warnings.append(
                    "no data domain could be assigned from the source text; the record cannot "
                    "reach level 1 until a steward files it"
                )

        if "provenanceClass" not in document:
            if found.provenance:
                out["provenanceClass"] = found.provenance_iri
                # Appended rather than replacing: both bases matter, and losing
                # one would make the other unfalsifiable.
                basis = document.get("inferenceBasis") or out.get("inferenceBasis") or ""
                out["inferenceBasis"] = f"{basis} {found.provenance_basis}".strip()
            else:
                # ADR-0011. The class is still not guessed — guessing one would
                # cap the Provenance grade on a fabrication, and that has not
                # changed. What changed is that saying nothing is no longer the
                # only alternative: an explicit gap records that somebody looked
                # and the source was mute, which is a different claim from
                # silence and is the one the catalog can actually stand behind.
                result.missing.add("provenanceClass")
                out["provenanceGap"] = {
                    "id": f"{DATASET_BASE}{slug}#provenance-gap",
                    "type": "ProvenanceGap",
                    "gapReason": (
                        "The source record does not state how the values in this dataset were "
                        "produced, and no provenance class could be determined from its own "
                        "words. The class is left unset rather than guessed: it caps the "
                        "Provenance quality grade, so an invented one would be a fabricated "
                        "quality claim. A steward or a later harvest can close this."
                    ),
                }
                result.warnings.append(
                    "the source does not state how its values were produced, so no provenance "
                    "class is set; an explicit provenance gap is recorded instead"
                )
        return out

    def _distributions(
        self, payload: dict[str, Any], slug: str, result: NormalizedRecord
    ) -> list[dict[str, Any]]:
        """One Distribution per access path the source lists.

        A record with no access path is still a record — a tier 3 pointer is
        exactly that — but it is a fact worth surfacing, so the absence goes in
        ``missing`` rather than passing silently.
        """
        spec = self.mapping.distributions
        if not spec:
            return []
        items = resolve(payload, spec["path"]) or []
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return []

        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            url, warning = self._field(item, spec.get("accessURL", "url"))
            if warning:
                result.warnings.append(f"distribution[{index}]: {warning}")
            url = _t_iri(url)
            if not url or url in seen:
                continue
            seen.add(url)
            dist: dict[str, Any] = {
                "id": f"{DISTRIBUTION_BASE}{slug}--{index}",
                "type": "Distribution",
                "accessURL": url,
                "hostedByOpenGrid": False,
            }
            for term, field_spec in spec.get("fields", {}).items():
                value, field_warning = self._field(item, field_spec)
                if field_warning:
                    result.warnings.append(f"distribution[{index}].{term}: {field_warning}")
                if value is not None:
                    dist[term] = value
            out.append(dist)
        return out

    def _flags(self, result: NormalizedRecord, slug: str) -> dict[str, Any]:
        caveats = [
            "Harvested automatically and not yet reviewed by a steward. Field values come "
            "from the source record as published; nothing here has been verified."
        ]
        if result.warnings:
            caveats.append("Normalisation warnings: " + "; ".join(sorted(result.warnings)[:4]))
        return {
            "id": f"{DATASET_BASE}{slug}#flags",
            "type": "QualityFlags",
            "staleness": "unknown",
            "caveat": caveats,
        }

    # ---- completeness ----------------------------------------------------

    #: Level 1 per PRD §6: it exists, you can find it, you know what it is and
    #: where to get it. Anything short of this is not publishable at all.
    LEVEL_1 = (
        "title",
        "description",
        "dataDomain",
        "provenanceClass",
        "license",
        "distribution",
    )
    #: Terms a record may satisfy either way. `provenanceClass` is satisfied by
    #: an explicit `provenanceGap` too, because ADR-0011 made D6 a disjunction
    #: in the shapes and a level calculation that disagreed with the shapes
    #: would mint records that validate at 2 and are labelled 1.
    LEVEL_1_ALTERNATIVES: dict[str, str] = {"provenanceClass": "provenanceGap"}
    #: Level 2 adds structure: what is inside it, and over what extent.
    LEVEL_2 = ("hasField", "temporal", "spatialGranularity", "updateCadence")

    def level(self, document: dict[str, Any]) -> int:
        """The level the record actually reaches, computed from its contents.

        Computed rather than declared, because a declared level is a claim and
        this one has to be true: the whole point of PRD §6 is that a user can
        trust the label. A harvested record reaching level 3 is not possible
        here — level 3 needs unit IRIs and concept resolution per field, which
        is the semantic layer's job (M7), not the normaliser's.
        """
        if not all(self._satisfied(document, term) for term in self.LEVEL_1):
            return 1
        if all(document.get(term) for term in self.LEVEL_2):
            return 2
        return 1

    def _satisfied(self, document: dict[str, Any], term: str) -> bool:
        """Whether the record carries *term*, or the alternative that stands in
        for it."""
        if document.get(term):
            return True
        alternative = self.LEVEL_1_ALTERNATIVES.get(term)
        return bool(alternative and document.get(alternative))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def normalize(record: HarvestedRecord, settings: Settings | None = None) -> NormalizedRecord:
    """Normalise using the mapping named by the record's source."""
    return Normalizer(record.source, settings).normalize(record)


def normalize_many(
    records: Iterable[HarvestedRecord], settings: Settings | None = None
) -> list[NormalizedRecord]:
    normalizers: dict[str, Normalizer] = {}
    out = []
    for record in records:
        if record.source not in normalizers:
            normalizers[record.source] = Normalizer(record.source, settings)
        out.append(normalizers[record.source].normalize(record))
    return out


__all__ = [
    "TRANSFORMS",
    "Mapping",
    "NormalizedRecord",
    "Normalizer",
    "load_mapping",
    "mapping_names",
    "normalize",
    "normalize_many",
    "resolve",
]
