"""The schema-probe stage: read a dataset's own description of its shape.

    harvest -> filter -> normalize -> SCHEMA PROBE -> enrich -> validate -> review

Eight of the nine adapters read a *catalog record about* a dataset — title,
licence, access URL — and none of them reads the dataset. So the catalog held
24 field descriptions across 66 records, while ERA5 alone publishes 273
variables with a long name and a unit on every one, in a single 130 KB request
to its own store. This stage closes that gap.

Four rules, and none of them is new — they are the rules the pipeline already
holds, applied to a new kind of read:

1. **Source-stated only.** A surface with no units yields fields with no unit.
   Nothing here infers or defaults. `og:unit` is a QUDT IRI and stays the
   semantic layer's to assign; this stage writes `og:unitAsStated`, which is
   the source's own string and a different claim.

2. **Never a full download.** Range requests and a hard byte cap, reusing the
   restraint `broker/prober.py` already documents: *a prober that fetched what
   it was checking would move terabytes a week across sources that did not ask
   to be crawled, and would be indistinguishable from abuse.* A store whose
   schema will not fit in one bounded read is skipped, not fetched.

3. **Every field says where it was read from**, so a wrong field is traceable
   to a wrong parse rather than to nobody-knows.

4. **A failed probe is not a failed record.** A dataset whose schema could not
   be read stays exactly as it was and says so through its completeness level.
   That is what level 1 is for.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from datahub.config import Settings, get_settings
from datahub.harvest.schema.surfaces import (
    ProbedField,
    from_arcgis,
    from_ckan_datastore,
    from_csv_header,
    from_datapackage,
    from_socrata,
    from_stac_datacube,
    from_zarr_v2,
    from_zarr_v3,
)
from datahub.harvest.schema.uris import to_http
from datahub.logging import get_logger
from datahub.namespaces import FIELD_BASE

log = get_logger(__name__)

#: The hard cap on any single read. Generous enough for a consolidated Zarr
#: manifest of a few hundred variables (ERA5's is 130 KB) and far too small to
#: be a download of anything.
MAX_BYTES = 4 * 1024 * 1024

#: The range a header-only surface needs. A CSV header is one line; 64 KB is
#: room for a very wide one and nothing else.
HEADER_BYTES = 64 * 1024

#: Deliberately short. A schema surface is a small document served by a store
#: that either answers quickly or is not worth waiting for, and this stage runs
#: once per record across thousands of them — so the cost that matters is the
#: cost of a *failure*, not of a success. At the harvester's default 10-second
#: connect timeout a run of 1,199 records against unreachable hosts spends
#: hours doing nothing.
TIMEOUT = httpx.Timeout(10.0, connect=3.0)


@dataclass(frozen=True, slots=True)
class Surface:
    """One thing to try against one distribution.

    ``suffix`` is appended to the distribution URL — a Zarr store's schema
    lives at ``<store>/.zmetadata``, not at the store root — and ``ranged``
    says whether a byte range is enough, which is what keeps a CSV probe from
    pulling a 4 TB file.
    """

    name: str
    suffix: str
    parse: Any
    ranged: bool = False


#: Ordered per media type. The first surface that yields fields wins, so the
#: richest is tried first: a Zarr v2 manifest carries units, a v3 one carries
#: units, and a CSV header carries names.
SURFACES: dict[str, tuple[Surface, ...]] = {
    "zarr": (
        Surface("zarr-v2-consolidated", "/.zmetadata", from_zarr_v2),
        Surface("zarr-v3", "/zarr.json", from_zarr_v3),
    ),
    "csv": (Surface("csv-header", "", from_csv_header, ranged=True),),
    "datapackage": (Surface("datapackage", "", from_datapackage),),
    "stac": (Surface("stac-datacube", "", from_stac_datacube),),
    "arcgis": (Surface("arcgis-fields", "?f=json", from_arcgis),),
    "socrata": (Surface("socrata-columns", "", from_socrata),),
    "ckan": (Surface("ckan-datastore", "", from_ckan_datastore),),
}


def surfaces_for(url: str, media_type: str | None) -> tuple[Surface, ...]:
    """Which surfaces are worth trying against this distribution.

    Dispatch is on the media type where the source stated one and on the URL
    shape where it did not, because a great many records carry a perfectly
    good `.csv` URL and no media type at all.
    """
    media = (media_type or "").lower()
    lowered = url.lower().split("?")[0].rstrip("/")

    if "zarr" in media or lowered.endswith(".zarr") or "/zarr" in lowered:
        return SURFACES["zarr"]
    if lowered.endswith("datapackage.json"):
        return SURFACES["datapackage"]
    if "featureserver" in lowered or "mapserver" in lowered:
        return SURFACES["arcgis"]
    if "/api/views/" in lowered:
        return SURFACES["socrata"]
    if "datastore_search" in lowered or "/api/3/action/datastore" in lowered:
        return SURFACES["ckan"]
    if "csv" in media or lowered.endswith((".csv", ".tsv", ".txt")):
        return SURFACES["csv"]
    if "/collections/" in lowered and "stac" in lowered:
        return SURFACES["stac"]
    return ()


@dataclass(slots=True)
class ProbeOutcome:
    """What one record's probe found, and where."""

    dataset_id: str
    fields: list[ProbedField] = field(default_factory=list)
    #: The distribution the schema was read from, and the surface that read it.
    distribution_id: str | None = None
    surface: str | None = None
    source_url: str | None = None
    bytes_read: int = 0
    attempts: int = 0
    #: Why nothing was found, when nothing was. Never left blank on a failure:
    #: "no schema" and "we did not look" are different facts.
    reason: str = ""

    @property
    def found(self) -> bool:
        return bool(self.fields)


class SchemaProber:
    """Reads schema surfaces. Owns the HTTP client and every limit on it."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self.settings = settings or get_settings()
        self.max_bytes = max_bytes
        self._client = client
        self._owned = client is None
        #: Hosts that could not be reached this run. A catalog points at the
        #: same few hosts over and over — the AWS registry has hundreds of
        #: records on a handful of domains — so a host that is down, blocked or
        #: simply not serving schemas costs one timeout rather than one per
        #: record. Per-run and in memory: the next run asks again, because a
        #: host being unreachable this morning is not a fact about the host.
        self._dead_hosts: set[str] = set()

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": self.settings.harvest_user_agent},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owned:
            self._client.close()
            self._client = None

    def __enter__(self) -> SchemaProber:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- fetching -------------------------------------------------------

    def fetch(self, url: str, *, ranged: bool) -> bytes | None:
        """A bounded GET. None on anything that is not a readable body.

        Every failure mode — refused, absent, too large, not parseable as
        text — is the same answer here: this is not a schema surface. The
        caller tries the next one.
        """
        headers = {"Range": f"bytes=0-{HEADER_BYTES - 1}"} if ranged else {}
        # The cap is enforced here, on bytes actually received, rather than
        # trusted to the `Range` header. A range bounds what crosses the wire;
        # it does not bound what arrives, because a gzipped response decodes on
        # the way in. A 64 KB range of the WRI power-plant CSV expands to 431
        # KB, which is not a download but is seven times what was asked for,
        # and on a larger file the same ratio is not harmless.
        cap = HEADER_BYTES if ranged else self.max_bytes
        host = urlsplit(url).netloc
        if host in self._dead_hosts:
            return None
        try:
            with self.client.stream("GET", url, headers=headers) as response:
                if response.status_code >= 400:
                    return None
                declared = response.headers.get("content-length")
                if declared and not ranged and int(declared) > self.max_bytes:
                    log.debug("schema surface too large", url=url, bytes=declared)
                    return None
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= cap:
                        # For a ranged read this is the normal ending, not a
                        # failure: a CSV header is the first line and whatever
                        # follows it in the first chunk is surplus. For a whole
                        # read it means the surface is bigger than this stage
                        # is willing to fetch, and it is abandoned.
                        if ranged:
                            break
                        log.debug("schema surface exceeded cap", url=url)
                        return None
                body = b"".join(chunks)
                return body[:cap] if ranged else body
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # The host itself is unreachable, which is a property of the host
            # and not of this URL. Remember it.
            self._dead_hosts.add(host)
            log.debug(
                "schema host unreachable; skipping it for this run", host=host, error=str(exc)
            )
            return None
        except (httpx.HTTPError, ValueError) as exc:
            log.debug("schema surface unreachable", url=url, error=str(exc))
            return None

    # ---- probing --------------------------------------------------------

    def probe_distribution(
        self, url: str, media_type: str | None
    ) -> tuple[list[ProbedField], str, int]:
        """Try each surface this distribution could carry, richest first."""
        for surface in surfaces_for(url, media_type):
            target = f"{url.rstrip('/')}{surface.suffix}" if surface.suffix else url
            payload = self.fetch(target, ranged=surface.ranged)
            if payload is None:
                continue
            try:
                fields = surface.parse(payload)
            except Exception as exc:
                log.debug("schema surface unparseable", url=target, error=str(exc))
                continue
            if fields:
                return fields, surface.name, len(payload)
        return [], "", 0

    def probe(self, document: dict[str, Any]) -> ProbeOutcome:
        """Read the first distribution of this record that has a schema.

        First rather than all, deliberately. A dataset published as Zarr *and*
        as NetCDF has one schema described twice, and merging two readings of
        it would invent disagreements the source never had.
        """
        dataset_id = str(document.get("id") or "")
        outcome = ProbeOutcome(dataset_id=dataset_id)

        for dist in _distributions(document):
            raw = str(dist.get("accessURL") or dist.get("downloadURL") or "")
            url = to_http(raw)
            if not url:
                continue
            media = dist.get("mediaType")
            if not surfaces_for(url, media):
                continue
            outcome.attempts += 1
            fields, surface, size = self.probe_distribution(url, media)
            if fields:
                outcome.fields = fields
                outcome.distribution_id = str(dist.get("id") or "")
                outcome.surface = surface
                outcome.source_url = url
                outcome.bytes_read = size
                return outcome

        outcome.reason = (
            "no distribution carries a machine-readable schema surface"
            if outcome.attempts
            else "no distribution has a readable schema surface for its format"
        )
        return outcome


def _local_name(entry: Any) -> str:
    """The local name of a field, however the record happens to carry it."""
    if isinstance(entry, dict):
        name = entry.get("localName") or entry.get("fieldId") or ""
        if not name and isinstance(entry.get("id"), str):
            name = entry["id"].rsplit("/", 1)[-1]
        return str(name).lower()
    if isinstance(entry, str):
        return entry.rsplit("/", 1)[-1].lower()
    return ""


def _distributions(document: dict[str, Any]) -> Sequence[dict[str, Any]]:
    raw = document.get("distribution") or []
    if isinstance(raw, dict):
        return [raw]
    return [d for d in raw if isinstance(d, dict)]


def apply(document: dict[str, Any], outcome: ProbeOutcome, *, slug: str) -> dict[str, Any]:
    """Add probed fields to a record. **Strictly additive, keyed on local name.**

    Merged rather than replaced, and the distinction is the whole design of
    this function.

    A hand-authored field carries things a probe cannot produce: a resolved
    concept IRI, a QUDT unit, a completeness caveat, a stated inference basis.
    ERA5's four golden-set fields say that `ssrd` is accumulated rather than
    instantaneous and that a consumer reading it as W/m2 overstates irradiance
    by 3600x. Overwriting that with `long_name: "Surface solar radiation
    downwards"` would be a loss dressed as an update.

    But refusing to touch a record that has *any* fields is equally wrong,
    because four hand-authored fields out of 273 is not a described schema —
    it is four descriptions and a silent gap. So an existing field is kept
    exactly as it is, and a probed field the record does not already have is
    added beside it.
    """
    if not outcome.found:
        return document

    existing = document.get("hasField") or []
    if isinstance(existing, dict):
        existing = [existing]
    # A record carries its fields either nested (read back from the store, or
    # freshly normalised) or as bare IRIs (a flat `@graph`, which is how the
    # fixture files on disk are written). Both have to dedupe, or loading a
    # hand-authored record and probing it would add a second `ssrd` beside the
    # one that carries the concept and the caveat.
    known = {_local_name(f) for f in existing}
    known.discard("")
    added = [
        f.as_node(FIELD_BASE, slug) for f in outcome.fields if f.local_name.lower() not in known
    ]
    if not added:
        return document

    document["hasField"] = [*existing, *added]
    document["schemaSource"] = (
        f"{outcome.surface} at {outcome.source_url}"
        if outcome.source_url
        else str(outcome.surface or "")
    )
    return document


__all__ = [
    "HEADER_BYTES",
    "MAX_BYTES",
    "SURFACES",
    "ProbeOutcome",
    "SchemaProber",
    "Surface",
    "apply",
    "surfaces_for",
]
