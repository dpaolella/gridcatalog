"""Native objects, not dicts (WP-10.1).

PRD §F9 asks for *search and filter returning native objects*. The distinction
that matters is not ergonomics: a dict lets a caller write
``record["quality"]["overall"]`` and get a ``KeyError`` in production, while a
typed object makes the absence of a composite quality score visible at the
point the code is written.

Every model here is built from an API payload with :meth:`from_payload`, which
copies fields and never computes them. A model that derived a value would be a
second implementation of a rule the API already owns, and the two would drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from opengrid.client import DataHub


@dataclass(frozen=True, slots=True)
class Concept:
    iri: str
    label: str | None = None
    definition: str | None = None


@dataclass(frozen=True, slots=True)
class Quality:
    """Three independent facets. There is deliberately no composite.

    ``None`` means *not assessed*, which is not the same as a poor grade — a
    record below completeness level 2 has no field metadata to grade, and
    conflating the two would defame every harvested record.

    There is no ``overall`` and no ``score``, and there is a test that says so.
    A dataset can be perfectly current and completely unprovenanced, and
    averaging those destroys the only information a user could act on.
    """

    provenance: str | None = None
    documentation: str | None = None
    currency: str | None = None
    provenance_label: str | None = None
    documentation_label: str | None = None
    currency_label: str | None = None
    rationales: dict[str, str] = field(default_factory=dict)

    @property
    def assessed(self) -> bool:
        return any((self.provenance, self.documentation, self.currency))

    @classmethod
    def from_payload(cls, data: Any) -> Quality:
        """Read either shape the API uses.

        ``/datasets`` returns a *list* of facet objects and the search document
        carries a badges *dict*. Reading both here rather than at the call site
        keeps one Quality in the SDK: two, differing by which endpoint produced
        them, is how a caller comes to write code that works on a record and
        not on a search hit.
        """
        if isinstance(data, list):
            grades = {f.get("facet"): f for f in data if isinstance(f, dict)}
            return cls(
                provenance=_grade(grades.get("provenance")),
                documentation=_grade(grades.get("documentation")),
                currency=_grade(grades.get("currency")),
                provenance_label=_label(grades.get("provenance")),
                documentation_label=_label(grades.get("documentation")),
                currency_label=_label(grades.get("currency")),
                rationales={
                    name: facet["rationale"]
                    for name, facet in grades.items()
                    if name and facet.get("rationale")
                },
            )
        data = data or {}
        return cls(
            provenance=data.get("provenance"),
            documentation=data.get("documentation"),
            currency=data.get("currency"),
            provenance_label=data.get("provenance_label"),
            documentation_label=data.get("documentation_label"),
            currency_label=data.get("currency_label"),
        )


def _grade(facet: dict[str, Any] | None) -> str | None:
    return facet.get("grade") if facet else None


def _label(facet: dict[str, Any] | None) -> str | None:
    return facet.get("label") if facet else None


@dataclass(frozen=True, slots=True)
class Field:
    """One column, variable or layer."""

    id: str
    local_name: str
    label: str | None = None
    definition: str | None = None
    data_type: str | None = None
    unit: str | None = None
    unit_label: str | None = None
    concept: Concept | None = None
    concept_inferred: bool = False
    #: Why no concept fits. Never silence: a field the catalog could not map
    #: says so, with a reason (rule X4).
    concept_gap_reason: str | None = None
    value_basis: str | None = None
    required: bool | None = None
    completeness_caveats: str | None = None

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> Field:
        concept = data.get("concept")
        return cls(
            id=data.get("id", ""),
            local_name=data.get("local_name", ""),
            label=data.get("label"),
            definition=data.get("definition"),
            data_type=data.get("data_type"),
            unit=data.get("unit"),
            unit_label=data.get("unit_label"),
            concept=(
                Concept(
                    iri=concept["iri"],
                    label=concept.get("label"),
                    definition=concept.get("definition"),
                )
                if isinstance(concept, dict)
                else None
            ),
            concept_inferred=bool(data.get("concept_inferred")),
            concept_gap_reason=data.get("concept_gap_reason"),
            value_basis=data.get("value_basis"),
            required=data.get("required"),
            completeness_caveats=data.get("completeness_caveats"),
        )


@dataclass(frozen=True, slots=True)
class Distribution:
    id: str
    access_url: str | None = None
    media_type: str | None = None
    format_label: str | None = None
    byte_size: int | None = None
    anonymous_access: bool | None = None
    bulk_download: bool | None = None
    supports_range_requests: bool = False
    subsetting_protocol: str | None = None
    link_health: str | None = None

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> Distribution:
        return cls(
            id=data.get("id", ""),
            access_url=data.get("access_url"),
            media_type=data.get("media_type"),
            format_label=data.get("format_label"),
            byte_size=data.get("byte_size"),
            anonymous_access=data.get("anonymous_access"),
            bulk_download=data.get("bulk_download"),
            supports_range_requests=bool(data.get("supports_range_requests")),
            subsetting_protocol=data.get("subsetting_protocol"),
            link_health=data.get("link_health"),
        )


@dataclass(frozen=True, slots=True)
class Link:
    """A related dataset, with the reason it is related."""

    dataset_id: str
    title: str | None
    relation: str
    strength: int
    descriptor: str
    reasons: tuple[str, ...] = ()
    joinable_keys: tuple[str, ...] = ()
    shared_workflow_tags: tuple[str, ...] = ()
    correlation_warning: str | None = None
    shared_origin: str | None = None
    strength_reduced_by_correlation: bool = False

    @property
    def independent(self) -> bool:
        """False when the two share an upstream origin.

        Worth a property rather than a truthiness check on the warning string,
        because ``if link.correlation_warning:`` is easy to write as
        ``if not link.correlation_warning:`` and mean the opposite.
        """
        return self.correlation_warning is None

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> Link:
        return cls(
            dataset_id=data["dataset_id"],
            title=data.get("title"),
            relation=data.get("relation", "related"),
            strength=int(data.get("strength", 1)),
            descriptor=data.get("descriptor", ""),
            reasons=tuple(data.get("reasons") or ()),
            joinable_keys=tuple(data.get("joinable_keys") or ()),
            shared_workflow_tags=tuple(data.get("shared_workflow_tags") or ()),
            correlation_warning=data.get("correlation_warning"),
            shared_origin=data.get("shared_origin"),
            strength_reduced_by_correlation=bool(data.get("strength_reduced_by_correlation")),
        )


@dataclass(frozen=True, slots=True)
class AccessPlan:
    """Where the data is and how to read it.

    The licence and attribution travel *in the plan* rather than in a page
    nobody read. An agent handed a URL cannot know it may not redistribute what
    it downloads; one handed a plan is told in a field it cannot miss.
    """

    dataset_id: str
    distribution_id: str
    mode: str
    location: str
    format: str | None = None
    read_instructions: dict[str, Any] = field(default_factory=dict)
    requested_slice: dict[str, Any] = field(default_factory=dict)
    byte_ranges: tuple[dict[str, Any], ...] = ()
    license: str | None = None
    license_note: str | None = None
    attribution: str | None = None
    redistribution_allowed: bool | None = None
    expires_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> AccessPlan:
        expires = data.get("expires_at")
        return cls(
            dataset_id=data.get("dataset_id", ""),
            distribution_id=data.get("distribution_id", ""),
            mode=data.get("mode", "redirect"),
            location=data.get("location", ""),
            format=data.get("format"),
            read_instructions=data.get("read_instructions") or {},
            requested_slice=data.get("requested_slice") or {},
            byte_ranges=tuple(data.get("byte_ranges") or ()),
            license=data.get("license"),
            license_note=data.get("license_note"),
            attribution=data.get("attribution"),
            redistribution_allowed=data.get("redistribution_allowed"),
            expires_at=_parse_dt(expires),
            raw=dict(data),
        )


@dataclass
class Dataset:
    """One catalog record, with the Hub attached so it can act.

    The Hub reference is what makes ``ds.open()`` a one-liner. It is a weak
    convenience and not a cache: every method here is a call, so a dataset held
    across a token expiry fails at the call rather than returning stale data it
    is no longer entitled to.
    """

    id: str
    title: str
    summary: str | None = None
    description: str | None = None
    publisher: str | None = None
    license_id: str | None = None
    license_url: str | None = None
    data_domains: tuple[Concept, ...] = ()
    concepts: tuple[Concept, ...] = ()
    keywords: tuple[str, ...] = ()
    completeness_level: int = 1
    quality: Quality = field(default_factory=Quality)
    anonymous_access: bool | None = None
    bulk_download: bool | None = None
    formats: tuple[str, ...] = ()
    distribution_count: int = 0
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None
    bbox: tuple[float, ...] | None = None
    reference_only: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    _hub: DataHub | None = field(default=None, repr=False, compare=False)

    # -- navigation --------------------------------------------------------

    def fields(self) -> list[Field]:
        """Field-level metadata, including the gaps and why they are gaps."""
        return self._require_hub().fields(self.id)

    def distributions(self) -> list[Distribution]:
        return self._require_hub().distributions(self.id)

    def links(self) -> list[Link]:
        """Related datasets. Check ``.independent`` before combining two."""
        return self._require_hub().links(self.id)

    def access_plan(self, **slice_spec: Any) -> AccessPlan:
        return self._require_hub().access_plan(self.id, **slice_spec)

    def open(self, **slice_spec: Any) -> Any:
        """Fetch an access plan and execute it here.

        The Hub is not in the path. This process reads the data from wherever
        the plan says it lives, with whatever reader the format needs, and the
        slice is applied at the source where the format supports it — which is
        why a time slice of a 4 TB Zarr transfers a few megabytes.
        """
        from opengrid.readers import execute

        return execute(self.access_plan(**slice_spec))

    # -- construction ------------------------------------------------------

    def _require_hub(self) -> DataHub:
        if self._hub is None:
            raise RuntimeError(
                "this Dataset was built without a DataHub, so it cannot fetch anything. "
                "Use hub.get(dataset_id) rather than constructing it directly."
            )
        return self._hub

    @classmethod
    def from_payload(cls, data: dict[str, Any], hub: DataHub | None = None) -> Dataset:
        spatial = data.get("spatial") or {}
        temporal = data.get("temporal") or {}
        bbox = spatial.get("bbox")
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            summary=data.get("summary"),
            description=data.get("description"),
            publisher=data.get("publisher"),
            license_id=data.get("license_id"),
            license_url=data.get("license_url"),
            data_domains=_concepts(data.get("data_domains")),
            concepts=_concepts(data.get("concepts")),
            keywords=tuple(data.get("keywords") or ()),
            completeness_level=int(data.get("completeness_level", 1)),
            quality=Quality.from_payload(data.get("quality")),
            anonymous_access=data.get("anonymous_access"),
            bulk_download=data.get("bulk_download"),
            formats=tuple(data.get("formats") or ()),
            distribution_count=int(data.get("distribution_count", 0) or 0),
            temporal_start=_parse_dt(temporal.get("start")),
            temporal_end=_parse_dt(temporal.get("end")),
            bbox=tuple(bbox) if bbox else None,
            reference_only=bool(data.get("reference_only")),
            raw=dict(data),
            _hub=hub,
        )


@dataclass
class ResultSet:
    """A page of search results that knows how many there were in total.

    Indexable and iterable, so ``hub.search(...)[0]`` is the one-liner PRD §F9
    asks for, while ``.total`` stays available — a caller who saw twenty
    results and no total would conclude there were twenty.
    """

    datasets: list[Dataset]
    total: int
    offset: int = 0
    limit: int = 20

    def __iter__(self) -> Any:
        return iter(self.datasets)

    def __getitem__(self, index: int) -> Dataset:
        return self.datasets[index]

    def __len__(self) -> int:
        return len(self.datasets)

    def __bool__(self) -> bool:
        return bool(self.datasets)

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.datasets) < self.total


def _concepts(items: Any) -> tuple[Concept, ...]:
    if not items:
        return ()
    return tuple(
        Concept(iri=i["iri"], label=i.get("label"), definition=i.get("definition"))
        for i in items
        if isinstance(i, dict) and i.get("iri")
    )


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


__all__ = [
    "AccessPlan",
    "Concept",
    "Dataset",
    "Distribution",
    "Field",
    "Link",
    "Quality",
    "ResultSet",
]
