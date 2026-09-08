"""Could the pipeline have found this on its own? (the self-population benchmark)

The catalog has two populations with opposite problems. A small hand-curated
set carries deep metadata — concepts, units, caveats, quality grades — because
somebody typed it. A large harvested set carries almost none, because nothing
in the pipeline reads a dataset's own schema or fills a judgement field. The
gap between them is not a fact about the datasets; it is a fact about how each
record got here.

**The curated set is the benchmark, not the goal.** A pipeline worth running
would rediscover every publicly-discoverable curated dataset without being told
it exists, and describe it at least as well. Where it cannot, that is a
measurable hole in source coverage, identity resolution, or field extraction —
and this module measures it rather than arguing about it.

Three numbers, each answering one question:

``recall``   Of the curated datasets that *are* publicly discoverable, how many
             does the harvested corpus already contain? Anything below 1.0 is a
             source the pipeline does not reach or an identity it cannot match.
``parity``   For the ones it did find, does the harvested record carry at least
             as much as the hand-written one? Below 1.0 means a human is still
             doing work the pipeline could do.
``breadth``  How much does the pipeline find *beyond* the curated set? The
             point of automation, and the one number that is already good.

**Identity is the hard part and it is deliberately conservative here.** A DOI
match is certain. A normalised access URL is near-certain. A title match is a
guess, and a wrong match inflates recall — the number this exists to be honest
about — so titles must agree exactly after normalisation, and a near-miss is
reported as a near-miss rather than counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

#: Curated records nothing open could rediscover, with the reason. These are
#: excluded from the recall denominator: a benchmark that demands the pipeline
#: find CEII-designated data is not measuring the pipeline.
#:
#: The test that reads this asserts every id here is really unreachable — a row
#: cannot be quietly excused by being added to the list.
UNDISCOVERABLE_REASONS: dict[str, str] = {
    "commercial-paywall": "behind a commercial subscription; no open catalog lists it",
    "ceii": "CEII-designated under 18 CFR 388.113; not published anywhere",
    "restricted": "access requires an NDA or clearance",
}


def normalise_url(url: str) -> str:
    """Host and path, with the noise that makes two spellings of one URL differ.

    `https://www.eia.gov/electricity/data/eia860/` and
    `http://eia.gov/electricity/data/eia860` are the same access path and a
    string comparison says otherwise.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    host = (parts.netloc or "").lower().removeprefix("www.")
    path = (parts.path or "").rstrip("/").lower()
    return f"{host}{path}"


def normalise_title(title: str) -> str:
    """Enough to match one dataset's several spellings, and no more.

    Parentheticals go because a catalog entry is as likely to say "NREL WIND
    Toolkit (WTK)" as "NREL Wind Toolkit"; punctuation and case go for the same
    reason. Word order does not, because "solar atlas global" is not the Global
    Solar Atlas and pretending otherwise is how recall gets inflated.
    """
    text = re.sub(r"\([^)]*\)", " ", title.lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def normalise_doi(doi: str) -> str:
    text = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        text = text.removeprefix(prefix)
    return text.rstrip("/")


@dataclass(frozen=True, slots=True)
class Identity:
    """The keys a record can be recognised by, strongest first."""

    id: str
    doi: str | None = None
    urls: frozenset[str] = frozenset()
    title: str = ""

    @classmethod
    def of(cls, node: dict[str, Any]) -> Identity:
        urls = set()
        for dist in _as_list(node.get("distribution")):
            if isinstance(dist, dict) and dist.get("accessURL"):
                urls.add(normalise_url(str(dist["accessURL"])))
        if node.get("landingPage"):
            urls.add(normalise_url(str(node["landingPage"])))
        persistent = node.get("persistentId")
        return cls(
            id=str(node.get("id", "")).rsplit("/", 1)[-1],
            doi=normalise_doi(str(persistent)) if persistent else None,
            urls=frozenset(u for u in urls if u),
            title=normalise_title(str(node.get("title", ""))),
        )


@dataclass
class Match:
    curated: str
    found: str | None = None
    #: `doi`, `url`, `title`, or `None` when nothing matched.
    how: str | None = None
    #: Set when nothing matched but something was close, so a reader can see
    #: whether the miss is a coverage gap or an identity-resolution gap.
    near: str | None = None


@dataclass
class Report:
    matches: list[Match] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)
    harvested_total: int = 0

    @property
    def discoverable(self) -> list[Match]:
        return [m for m in self.matches if m.curated not in self.excluded]

    @property
    def recall(self) -> float:
        wanted = self.discoverable
        if not wanted:
            return 1.0
        return sum(1 for m in wanted if m.found) / len(wanted)

    @property
    def missed(self) -> list[Match]:
        return [m for m in self.discoverable if not m.found]

    def summary(self) -> str:
        found = len(self.discoverable) - len(self.missed)
        return (
            f"rediscovery: {found}/{len(self.discoverable)} "
            f"({self.recall:.0%}) of the discoverable curated set, "
            f"{len(self.excluded)} excluded as undiscoverable, "
            f"{self.harvested_total} harvested records in the corpus"
        )


def match(curated: list[dict[str, Any]], harvested: list[dict[str, Any]]) -> Report:
    """Which curated datasets the harvested corpus already contains."""
    by_doi: dict[str, str] = {}
    by_url: dict[str, str] = {}
    by_title: dict[str, str] = {}
    for node in harvested:
        identity = Identity.of(node)
        if identity.doi:
            by_doi.setdefault(identity.doi, identity.id)
        for url in identity.urls:
            by_url.setdefault(url, identity.id)
        if identity.title:
            by_title.setdefault(identity.title, identity.id)

    report = Report(harvested_total=len(harvested))
    for node in curated:
        identity = Identity.of(node)
        result = Match(curated=identity.id)
        if identity.doi and identity.doi in by_doi:
            result.found, result.how = by_doi[identity.doi], "doi"
        elif hit := next((by_url[u] for u in identity.urls if u in by_url), None):
            result.found, result.how = hit, "url"
        elif identity.title in by_title:
            result.found, result.how = by_title[identity.title], "title"
        else:
            result.near = _near(identity, by_title)
        report.matches.append(result)

        barrier = str(node.get("accessBarrier") or "")
        restriction = str(node.get("accessRestriction") or "").rsplit("/", 1)[-1]
        for key, reason in UNDISCOVERABLE_REASONS.items():
            if barrier == key or restriction == _camel(key):
                report.excluded[identity.id] = reason
                break
    return report


def parity(curated: dict[str, Any], harvested: dict[str, Any], fields: list[str]) -> set[str]:
    """Fields the hand-written record fills and the harvested one does not.

    The question the user asked in one function: *the human picked seed
    datasets shouldn't have more metadata than the ones found in an automated
    way.* An empty set is the passing answer.
    """
    return {
        name
        for name in fields
        if _populated(curated.get(name)) and not _populated(harvested.get(name))
    }


def _populated(value: Any) -> bool:
    return value not in (None, "", [], {})


def _camel(text: str) -> str:
    head, *rest = text.split("-")
    return head + "".join(part.title() for part in rest)


def _near(identity: Identity, by_title: dict[str, str]) -> str | None:
    """A harvested title sharing most of its words. Reported, never counted."""
    words = set(identity.title.split())
    if len(words) < 2:
        return None
    best, score = None, 0.0
    for title, found in by_title.items():
        other = set(title.split())
        overlap = len(words & other) / max(len(words | other), 1)
        if overlap > score:
            best, score = found, overlap
    return best if score >= 0.5 else None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


__all__ = [
    "UNDISCOVERABLE_REASONS",
    "Identity",
    "Match",
    "Report",
    "match",
    "normalise_doi",
    "normalise_title",
    "normalise_url",
    "parity",
]
