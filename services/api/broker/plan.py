"""The access plan (WP-5.1, WP-5.2).

PRD §F7's central object, and the reason the catalog is worth having rather
than a spreadsheet of links:

> One uniform shape regardless of whether the dataset is 800 KB or 4 TB. Only
> the path differs.
>
> License, attribution and quality grades travel with the plan. **This is what
> makes agentic access defensible: the guardrail metadata is in the payload,
> not in a page the agent never read.**

That second paragraph is the design. An agent handed a URL has no way to know
it may not redistribute what it downloads; an agent handed a *plan* is told, in
the same object, in a field it cannot miss. The licence is not a footnote on
the plan, it is part of it.

**Path selection is derived from metadata, not configured.** PRD §F7:

    og:supportsRangeRequests + og:chunkIndexMethod → partial-read
    og:subsettingProtocol set                     → that protocol
    otherwise                                     → redirect, and the plan
                                                    says no partial read exists

The last clause matters as much as the first two. A plan that silently omits
the partial-read section looks identical to one for a dataset that supports it
but whose metadata is missing; saying "this dataset has no partial read, and
here is why" is the difference between a catalog and a link list.

**Never bytes.** The plan says where the data is and how to read it. The Hub is
not in the read path — which is a decision about what OpenGrid is, not an
optimisation. Proxying would make it an egress bill, a bandwidth bottleneck and
a party to every licence it does not hold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse

from datahub.api.schemas import DistributionDetail
from datahub.api.search.document import SearchDocument
from datahub.config import Settings, get_settings
from datahub.errors import NoUsableDistribution
from datahub.logging import get_logger

log = get_logger(__name__)

Mode = Literal["redirect", "partial-read", "subsetting-protocol"]

#: Reader recipes by format. Not a full driver table — enough that a client
#: knows which library opens the thing, which is the question that actually
#: blocks someone. A format not listed gets no instructions rather than
#: guessed ones: a wrong engine wastes more of a user's time than a missing one.
READERS: dict[str, dict[str, Any]] = {
    "zarr": {"library": "xarray", "engine": "zarr"},
    "netcdf": {"library": "xarray", "engine": "h5netcdf"},
    "nc": {"library": "xarray", "engine": "h5netcdf"},
    "hdf5": {"library": "xarray", "engine": "h5netcdf"},
    "grib": {"library": "xarray", "engine": "cfgrib"},
    "grib2": {"library": "xarray", "engine": "cfgrib"},
    "parquet": {"library": "pandas", "engine": "pyarrow"},
    "geoparquet": {"library": "geopandas", "engine": "pyarrow"},
    "csv": {"library": "pandas", "engine": "c"},
    "geotiff": {"library": "rioxarray", "engine": "rasterio"},
    "cog": {"library": "rioxarray", "engine": "rasterio"},
    "geojson": {"library": "geopandas", "engine": "pyogrio"},
    "shapefile": {"library": "geopandas", "engine": "pyogrio"},
    "geopackage": {"library": "geopandas", "engine": "pyogrio"},
}

#: Object-store schemes a reader reaches through fsspec rather than HTTP.
_OBJECT_STORE = {"s3": "s3", "gs": "gcs", "gcs": "gcs", "az": "abfs", "abfs": "abfs"}


@dataclass(slots=True)
class SliceSpec:
    """What the caller asked for.

    Echoed back in the plan rather than merely acted on, so a client can see
    that a slice it requested was understood — and, when it was not, that it
    was dropped rather than silently applied to the wrong axis.
    """

    time: tuple[str, str] | None = None
    bbox: tuple[float, float, float, float] | None = None
    variables: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.time:
            out["time"] = list(self.time)
        if self.bbox:
            out["bbox"] = list(self.bbox)
        if self.variables:
            out["variables"] = list(self.variables)
        return out

    @property
    def empty(self) -> bool:
        return not (self.time or self.bbox or self.variables)


@dataclass(slots=True)
class AccessPlan:
    """One uniform shape. Only the path differs."""

    dataset_id: str
    distribution_id: str
    mode: Mode
    location: str
    format: str | None = None
    read_instructions: dict[str, Any] = field(default_factory=dict)
    requested_slice: dict[str, Any] = field(default_factory=dict)
    byte_ranges: list[dict[str, Any]] = field(default_factory=list)
    credentials: dict[str, Any] | None = None
    expires_at: datetime | None = None

    # -- the guardrail metadata, in the payload --
    license: str | None = None
    license_note: str | None = None
    attribution: str | None = None
    redistribution_allowed: bool | None = None
    commercial_use_allowed: bool | None = None
    quality_grades: dict[str, str | None] = field(default_factory=dict)

    #: Why this path and not another, and what is not available. A plan that
    #: silently omits partial-read looks identical to one for a dataset that
    #: supports it but whose metadata is missing.
    path_rationale: str = ""
    partial_read_unavailable_reason: str | None = None
    #: Set when the chosen distribution is not the first choice — a sibling
    #: after a dead primary, a gated path after an unreachable open one.
    fallback_reason: str | None = None
    caveats: list[str] = field(default_factory=list)


class Broker:
    """Issues access plans. Never bytes."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def plan(
        self,
        document: SearchDocument,
        record: dict[str, Any],
        *,
        slice_spec: SliceSpec | None = None,
        distribution_id: str | None = None,
    ) -> AccessPlan:
        """Choose a path and build the plan.

        Takes both the index document and the record: the index says who may
        see this and carries the computed grades, and the record carries the
        licence terms and the access paths. The index deliberately holds
        neither — a search response should not haul every licence note and
        every URL in the catalog to a client that wanted ten titles.

        ``distribution_id`` pins a specific path — for a client that already
        knows it wants the Zarr and not the CSV. Without it the broker picks,
        and says why in ``path_rationale``.
        """
        node = _dataset_node(record)
        distributions = DistributionDetail.from_record(record)
        slice_spec = slice_spec or SliceSpec()
        chosen, fallback = self.choose(
            distributions, distribution_id, slice_spec, dataset_id=document.id
        )

        mode, rationale, unavailable = self.select_mode(chosen, slice_spec)
        plan = AccessPlan(
            dataset_id=document.id,
            distribution_id=chosen.id,
            mode=mode,
            location=str(chosen.access_url),
            format=chosen.format_label,
            read_instructions=self.read_instructions(chosen),
            requested_slice=slice_spec.as_dict(),
            expires_at=datetime.now(UTC) + timedelta(seconds=self.settings.access_plan_ttl_s),
            path_rationale=rationale,
            partial_read_unavailable_reason=unavailable,
            fallback_reason=fallback,
        )
        self._attach_terms(plan, document, node, chosen)
        self._attach_caveats(plan, document, node, chosen, slice_spec)
        return plan

    # ---- choosing a distribution ----------------------------------------

    def choose(
        self,
        distributions: list[DistributionDetail],
        distribution_id: str | None,
        slice_spec: SliceSpec,
        dataset_id: str = "",
    ) -> tuple[DistributionDetail, str | None]:
        """Pick the path, and say if it was not the obvious one.

        PRD §F1.13: a distribution that has failed its probes is excluded and a
        live sibling returned instead. Excluded rather than merely ranked
        lower — a plan pointing at a URL we know is dead is worse than a plan
        that says the only path left is the gated one.
        """
        usable = [d for d in distributions if d.access_url]
        if not usable:
            raise NoUsableDistribution(
                "this dataset records no access path",
                dataset_id=dataset_id,
                hint="It may be a reference-only pointer; see the record's landing page.",
            )

        if distribution_id:
            pinned = next((d for d in usable if d.id == distribution_id), None)
            if pinned is None:
                raise NoUsableDistribution(
                    f"no distribution {distribution_id!r} on this dataset",
                    dataset_id=dataset_id,
                    available=[d.id for d in usable],
                )
            if not pinned.reachable:
                # Honoured anyway: the caller named it, and a client that pins
                # a path usually knows something the prober does not — a
                # transient outage, a network only they can reach.
                return pinned, (
                    f"You pinned this distribution and its last probe was "
                    f"{pinned.link_health.status if pinned.link_health else 'unknown'}."
                )
            return pinned, None

        live = [d for d in usable if d.reachable]
        excluded = [d for d in usable if not d.reachable]
        pool = live or usable

        best = min(pool, key=lambda d: self._rank(d, slice_spec))
        fallback = None
        if excluded and live:
            fallback = (
                f"{len(excluded)} distribution(s) were skipped: the prober's last attempt "
                "did not reach them. This is a live sibling."
            )
        elif excluded and not live:
            fallback = (
                "Every recorded path failed its last probe. This plan points at the least "
                "recently failed one; expect it not to work."
            )
        return best, fallback

    def _rank(self, dist: DistributionDetail, slice_spec: SliceSpec) -> tuple[int, ...]:
        """Lower is better.

        A caller who asked for a slice wants a path that can serve one, even if
        that path is gated: fetching 4 TB to read a month of it is not a
        smaller inconvenience than making an account.
        """
        wants_slice = not slice_spec.empty
        return (
            0 if (wants_slice and self._can_slice(dist)) else 1,
            0 if dist.anonymous_access is not False else 1,
            0 if not dist.requester_pays else 1,
            0 if dist.bulk_download else 1,
        )

    @staticmethod
    def _can_slice(dist: DistributionDetail) -> bool:
        return bool(
            dist.subsetting_protocol or (dist.supports_range_requests and dist.chunk_index_method)
        )

    # ---- choosing a mode -------------------------------------------------

    def select_mode(
        self, dist: DistributionDetail, slice_spec: SliceSpec
    ) -> tuple[Mode, str, str | None]:
        """PRD §F7's rule, applied to one distribution.

        Returns the mode, why, and — when partial read is not on offer — what
        is missing. The third value is the one that keeps a plan honest: an
        absent partial-read section is ambiguous, an explained one is not.
        """
        if dist.subsetting_protocol:
            return (
                "subsetting-protocol",
                (
                    f"The source exposes a subsetting protocol "
                    f"({dist.subsetting_protocol}), so the slice is requested from the "
                    "service rather than filtered after download."
                ),
                None,
            )

        if dist.supports_range_requests and dist.chunk_index_method:
            return (
                "partial-read",
                (
                    f"The distribution supports byte-range requests and is "
                    f"{dist.chunk_index_method}-indexed, so a client can read the chunks it "
                    "needs without fetching the whole object."
                ),
                None,
            )

        missing = []
        if not dist.supports_range_requests:
            missing.append("the source does not advertise byte-range support")
        if not dist.chunk_index_method:
            missing.append(
                "no chunk index is recorded, so there is no way to know which bytes to ask for"
            )
        reason = (
            "No partial read is available for this distribution: " + "; and ".join(missing) + "."
        )
        if not slice_spec.empty:
            reason += (
                " Your requested slice is recorded on the plan but has to be applied after "
                "download."
            )
        return (
            "redirect",
            "Whole-object access. Neither a subsetting protocol nor a usable byte-range "
            "path is recorded for this distribution.",
            reason,
        )

    # ---- reading it ------------------------------------------------------

    def read_instructions(self, dist: DistributionDetail) -> dict[str, Any]:
        """Which library opens this, and how.

        A format not in the table gets no instructions rather than guessed
        ones. A wrong engine costs a user more time than a missing one: they
        try it, it fails obscurely, and they conclude the data is broken.
        """
        key = _format_key(dist)
        recipe = READERS.get(key)
        if recipe is None:
            return {}

        instructions = dict(recipe)
        scheme = urlparse(str(dist.access_url)).scheme
        if protocol := _OBJECT_STORE.get(scheme):
            instructions["protocol"] = protocol
            instructions["storage_options"] = {"anon": dist.anonymous_access is not False}
            if dist.requester_pays:
                instructions["storage_options"]["requester_pays"] = True
        return instructions

    # ---- what travels with the plan --------------------------------------

    def _attach_terms(
        self,
        plan: AccessPlan,
        document: SearchDocument,
        node: dict[str, Any],
        dist: DistributionDetail,
    ) -> None:
        """Licence, attribution and grades, in the payload.

        The point of the whole object. An agent handed a URL cannot know it may
        not redistribute what it downloads; an agent handed a plan is told, in
        a field it cannot miss.

        An unknown permission is reported as unknown, never as permitted. A
        client that reads ``redistribution_allowed`` and finds ``null`` has to
        go and look; one that finds ``true`` because we defaulted it has been
        told something false.
        """
        plan.license = node.get("license") or document.license_id
        plan.license_note = node.get("licenseNote")
        plan.attribution = node.get("attribution")
        plan.redistribution_allowed = node.get("redistributionAllowed")
        plan.commercial_use_allowed = node.get("commercialUseAllowed")
        plan.quality_grades = {
            "provenance": document.quality.provenance if document.quality else None,
            "documentation": document.quality.documentation if document.quality else None,
            "currency": document.quality.currency if document.quality else None,
        }
        if dist.anonymous_access is False:
            plan.credentials = {
                "required": True,
                "how": dist.credential_requirement
                or "The source requires an account. See the dataset's landing page.",
            }

    def _attach_caveats(
        self,
        plan: AccessPlan,
        document: SearchDocument,
        node: dict[str, Any],
        dist: DistributionDetail,
        slice_spec: SliceSpec,
    ) -> None:
        """The things a caller acting on this plan should know.

        On the plan rather than only on the record, because the plan is what an
        agent reads. A caveat in a record the agent never fetched is a caveat
        nobody saw.
        """
        if plan.redistribution_allowed is False:
            plan.caveats.append(
                "Redistribution is not permitted under this licence. You may read this data; "
                "publishing it or a derivative may require permission from the source."
            )
        elif plan.redistribution_allowed is None:
            plan.caveats.append(
                "Redistribution terms are not recorded for this dataset. Absent an explicit "
                "grant, assume default copyright applies."
            )
        if document.reference_only:
            plan.caveats.append(
                "This is a reference-only pointer. The catalog records where the dataset is "
                "and does not describe its contents."
            )
        if dist.link_health and dist.link_health.status not in (None, "verified"):
            plan.caveats.append(f"The last probe of this URL reported {dist.link_health.status}.")
        if not slice_spec.empty and plan.mode == "redirect":
            plan.caveats.append(
                "The requested slice could not be pushed to the source and is recorded for "
                "your client to apply after download."
            )
        flags = node.get("qualityFlags") or {}
        staleness = flags.get("staleness") if isinstance(flags, dict) else None
        if staleness and staleness not in ("current", "unknown"):
            plan.caveats.append(f"The record's staleness is recorded as {staleness}.")
        caveats = flags.get("caveat") if isinstance(flags, dict) else None
        for caveat in (caveats if isinstance(caveats, list) else [caveats] if caveats else [])[:3]:
            # A steward's caveat belongs on the plan, not only on the record:
            # the plan is what an agent reads, and a caveat in a record the
            # agent never fetched is a caveat nobody saw.
            plan.caveats.append(str(caveat))


def _format_key(dist: DistributionDetail) -> str:
    """Normalise a format label into a reader table key.

    Sources write "Zarr v2", "application/vnd+zarr" and "zarr" for the same
    thing, so the media type and the label are both tried, longest match first
    — "geoparquet" must not be read as "parquet".
    """
    haystack = f"{dist.format_label or ''} {dist.media_type or ''}".lower()
    for key in sorted(READERS, key=len, reverse=True):
        if key in haystack:
            return key
    return ""


__all__ = ["READERS", "AccessPlan", "Broker", "Mode", "SliceSpec"]


def _dataset_node(record: dict[str, Any]) -> dict[str, Any]:
    from datahub.graph.records import dataset_node

    try:
        return dataset_node(record)
    except Exception:
        log.warning("record has no dataset node; the plan will carry no licence terms")
        return {}
