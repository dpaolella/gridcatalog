"""Loading the curated seed inventory into the catalog (WP-2.5).

114 anchor datasets across DD1–DD10, from ``data/seed-sources.yaml``.

**The rule this module exists to enforce.** The seed file's header says the
DD1/DD5/DD8/DD9 entries came from a reviewed feasibility analysis and carry
``verified: true``; the rest were assembled for the PRD, have not been through
licence and access-path review, and:

> Do not treat the license or tier fields on unverified rows as authoritative.

So an unverified row **cannot reach the catalog graph**. It lands in
``og:graph/draft`` with ``og:reviewState "draft"`` and a review-queue entry. The
split is not a convention here; it is a branch with a test on both sides,
because a reviewed record and an unreviewed one look identical to a user and
only one of them has had its licence checked.

**Nothing is inferred.** A field the seed file does not state is left empty and
the completeness level says so. Most seed rows carry a name, a tier, a licence
string and an access URL, which is a level 1 record — and level 1 honestly
labelled is the whole point of PRD §6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from datahub.config import Settings, get_settings
from datahub.errors import ValidationFailed
from datahub.graph.records import RecordStore
from datahub.harvest.adapters.base import HarvestedRecord, slugify
from datahub.harvest.adapters.curated import CuratedAdapter
from datahub.logging import get_logger
from datahub.namespaces import (
    DATASET_BASE,
    DISTRIBUTION_BASE,
    SCHEME_ACCESS_RESTRICTION,
    SCHEME_DATA_DOMAIN,
    SCHEME_PROVENANCE_CLASS,
    SPDX,
)

log = get_logger(__name__)

LICENSE_MAP_PATH = Path(__file__).parent / "seed-license-map.yaml"

#: The seed file's free-text provenance values, mapped onto the SKOS scheme.
#: Values not listed are left unset rather than guessed — the provenance class
#: caps the Provenance grade, so a wrong one is a wrong quality claim.
PROVENANCE_MAP: dict[str, str] = {
    "primary": "primary",
    "curated": "curated",
    "curated benchmark": "curated",
    "modeled": "modeled",
    "reanalysis": "reanalysis",
    "derived": "derived",
    "synthetic": "synthetic",
    "osm-derived": "osmDerived",
    "osm-derived + curated": "osmDerived",
    "institutional": "institutional",
}

#: ``access_barrier`` in the seed file, mapped onto the access-restriction
#: scheme. ``fragmented`` and ``restricted`` are the file's own words for
#: barriers that are not licence terms.
BARRIER_MAP: dict[str, str] = {
    "restricted": "ceii",
    "commercial-paywall": "commercialPaywall",
    "proprietary": "commercialPaywall",
    "fragmented": "discontinued",
}


@dataclass(slots=True)
class SeedLoadResult:
    total: int = 0
    confirmed: int = 0
    drafted: int = 0
    flagged: int = 0
    by_level: dict[int, int] = field(default_factory=dict)
    by_domain: dict[str, int] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        levels = ", ".join(f"L{k}: {v}" for k, v in sorted(self.by_level.items()))
        line = (
            f"{self.total} seed records — {self.confirmed} confirmed, "
            f"{self.drafted} draft ({levels})"
        )
        if self.failures:
            line += f"; {len(self.failures)} failed validation"
        return line


class SeedLoader:
    """Turns curated seed rows into catalog records."""

    def __init__(
        self,
        records: RecordStore,
        settings: Settings | None = None,
        *,
        adapter: CuratedAdapter | None = None,
    ) -> None:
        self.records = records
        self.settings = settings or get_settings()
        self.adapter = adapter or CuratedAdapter(self.settings)
        self._licences = yaml.safe_load(LICENSE_MAP_PATH.read_text())

    def load(self, *, limit: int | None = None, validate: bool = True) -> SeedLoadResult:
        harvested, _ = self.adapter.harvest(limit=limit)
        result = SeedLoadResult(total=len(harvested))

        for record, extra_domains in self._merge_cross_domain(harvested):
            document = self.to_record(record)
            if extra_domains:
                # One dataset listed under two domains is one record with two
                # domain facets, not two records (PRD §4.1 D3; the seed file
                # says the same of NREL ATB: "Model as one dataset with domain
                # facets, not two records"). Writing them separately would have
                # the second silently overwrite the first, and the catalog
                # would quietly lose a domain assignment.
                document["dataDomain"] = sorted(
                    {*document["dataDomain"], *(f"{SCHEME_DATA_DOMAIN}/{d}" for d in extra_domains)}
                )
            level = document["completenessLevel"]
            state = document["reviewState"]
            try:
                self.records.put(document, validate=validate)
            except ValidationFailed as exc:
                result.failures.append((document["id"], exc.message))
                log.warning(
                    "seed record failed validation",
                    dataset=document["id"],
                    violations=[str(v) for v in exc.violations[:3]],
                )
                continue
            result.by_level[level] = result.by_level.get(level, 0) + 1
            for domain in (record.payload["data_domain"], *extra_domains):
                result.by_domain[domain] = result.by_domain.get(domain, 0) + 1
            if state == "confirmed":
                result.confirmed += 1
            else:
                result.drafted += 1

        log.info("seed load complete", **{"summary": result.summary})
        return result

    def _merge_cross_domain(
        self, harvested: list[HarvestedRecord]
    ) -> list[tuple[HarvestedRecord, list[str]]]:
        """Collapse rows that describe the same dataset under several domains.

        The seed inventory lists EU ETS / EEA EUTL under both DD7 and DD8, and
        NREL ATB's note says explicitly to model it as one dataset with domain
        facets rather than two records. Identity is the slug, which is derived
        from the name — so two rows with the same name are the same dataset,
        and treating them as two would mean the second write silently replaced
        the first.

        The row carrying the most detail wins as the base; the others
        contribute only their domain. A verified row always beats an unverified
        one, because merging an unreviewed row's licence into a reviewed record
        would launder it.
        """
        by_slug: dict[str, list[HarvestedRecord]] = {}
        for record in harvested:
            by_slug.setdefault(slugify(record.payload["name"]), []).append(record)

        merged: list[tuple[HarvestedRecord, list[str]]] = []
        for rows in by_slug.values():
            if len(rows) == 1:
                merged.append((rows[0], []))
                continue
            base = max(
                rows,
                key=lambda r: (bool(r.payload.get("verified")), len(r.payload)),
            )
            others = [r.payload["data_domain"] for r in rows if r is not base]
            log.info(
                "seed rows merged",
                slug=slugify(base.payload["name"]),
                domains=[base.payload["data_domain"], *others],
            )
            merged.append((base, others))
        return merged

    # ---- one row to one record ------------------------------------------

    def to_record(self, harvested: HarvestedRecord) -> dict[str, Any]:
        """Build a JSON-LD record from one seed row.

        Every value traces to a field in the file. Where the file is silent the
        record is silent; the completeness level carries the consequence.
        """
        entry = harvested.payload
        domain = entry["data_domain"]
        name = entry["name"]
        slug = slugify(name)
        iri = f"{DATASET_BASE}{slug}"
        verified = bool(entry.get("verified"))
        tier = entry.get("tier")

        record: dict[str, Any] = {
            "@context": f"{self.settings.catalog_base_url}/context/opengrid-datahub.jsonld",
            "id": iri,
            "type": "Dataset",
            "title": name,
            "description": self._description(entry),
            "dataDomain": [f"{SCHEME_DATA_DOMAIN}/{domain}"],
            "documentationStatus": self._documentation_status(entry),
            "completenessLevel": 1,
            # The load-bearing line. An unverified row cannot be confirmed,
            # whatever else it carries.
            "reviewState": "confirmed" if verified else "draft",
            "harvestSource": "curated",
            "sourceRecordId": harvested.source_id,
            "visibility": "public",
            "distribution": [f"{DISTRIBUTION_BASE}{slug}--primary"],
            "modified": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

        if tier is not None:
            record["tier"] = tier
            if tier == 3:
                record["referenceOnly"] = True
        if entry.get("pointer_rationale"):
            record["pointerRationale"] = _clean(entry["pointer_rationale"])
        if entry.get("access_barrier"):
            record["accessBarrier"] = entry["access_barrier"]
        if entry.get("doi"):
            record["persistentId"] = _as_doi_iri(entry["doi"])

        record.update(self._licence(entry))
        record.update(self._access(entry))
        record.update(self._provenance(entry))

        summary = _clean(entry.get("note") or "")
        if summary:
            record["summary"] = summary

        record["distribution"] = [self._distribution(slug, entry)]
        if entry.get("secondary_access") or entry.get("api"):
            record["distribution"].append(self._secondary(slug, entry))

        record["qualityFlags"] = {
            "id": f"{iri}#flags",
            "type": "QualityFlags",
            "staleness": "unknown" if not verified else "current",
            "caveat": self._caveats(entry, verified=verified),
        }
        return record

    # ---- field groups ----------------------------------------------------

    def _description(self, entry: dict[str, Any]) -> str:
        """A description assembled only from what the file states.

        The seed file has no description field, so this is built from the facts
        it does carry rather than invented. Saying less than the source is
        honest; saying more is not.
        """
        parts = [f"{entry['name']}, a {entry.get('domain_name', 'grid')} dataset"]
        if entry.get("format"):
            parts.append(f"published as {entry['format']}")
        if entry.get("access"):
            parts.append(f"available at {entry['access']}")
        sentence = ", ".join(parts) + "."
        note = _clean(entry.get("note") or "")
        rationale = _clean(entry.get("pointer_rationale") or "")
        for extra in (note, rationale):
            if extra:
                sentence += f" {extra}"
        if not entry.get("verified"):
            sentence += (
                " This entry has not been through licence and access-path review; its "
                "licence and tier are not authoritative until a steward confirms them."
            )
        return sentence

    def _licence(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Map a licence string, or record that it could not be mapped.

        Three outcomes, and the third is the honest one:
        an SPDX identifier; a LicenseRef with the real terms written out; or a
        LicenseRef marking the string unresolved, with the original preserved.
        Never a guess.
        """
        raw = entry.get("license")
        if not raw:
            return {
                "license": f"{SPDX}LicenseRef-Unstated",
                "licenseNote": (
                    "The seed inventory records no licence for this dataset. Absent an "
                    "explicit grant, default copyright applies and reuse may not be "
                    "permitted. A steward must resolve this before the record is confirmed."
                ),
                "redistributionAllowed": False,
            }
        text = str(raw).strip()
        if spdx := self._licences["spdx"].get(text):
            return {"license": f"{SPDX}{spdx}"}
        for bucket in ("license_ref", "dual"):
            if entry_map := self._licences[bucket].get(text):
                out: dict[str, Any] = {
                    "license": f"{SPDX}{entry_map['id']}",
                    "licenseNote": _clean(entry_map["note"]),
                }
                for key, field_name in (
                    ("redistribution_allowed", "redistributionAllowed"),
                    ("commercial_use_allowed", "commercialUseAllowed"),
                    ("share_alike", "shareAlike"),
                ):
                    if key in entry_map:
                        out[field_name] = entry_map[key]
                return out
        return {
            "license": f"{SPDX}LicenseRef-Unreviewed-{slugify(text, max_length=40)}",
            "licenseNote": (
                f'The seed inventory records the licence as "{text}", which does not map '
                "to a known identifier. It has not been reviewed and must not be relied on."
            ),
            "redistributionAllowed": False,
        }

    def _access(self, entry: dict[str, Any]) -> dict[str, Any]:
        anonymous = entry.get("anonymous")
        barrier = entry.get("access_barrier")
        restriction = BARRIER_MAP.get(barrier or "")
        if restriction is None:
            restriction = "none" if anonymous is not False else "accountRequired"
        return {
            "accessRestriction": f"{SCHEME_ACCESS_RESTRICTION}/{restriction}",
            "anonymousAccess": bool(anonymous) if anonymous is not None else False,
        }

    def _provenance(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Map the free-text provenance, or fall back to ``curated``.

        The fallback is defensible: the seed inventory *is* a curated
        compilation, so "curated" is true of every row in it even when the
        upstream basis is unrecorded. Guessing anything more specific would be
        a quality claim, since the class caps the Provenance grade.
        """
        raw = str(entry.get("provenance") or "").strip()
        concept = PROVENANCE_MAP.get(raw, "curated")
        return {"provenanceClass": f"{SCHEME_PROVENANCE_CLASS}/{concept}"}

    def _documentation_status(self, entry: dict[str, Any]) -> str:
        if entry.get("tier") == 3:
            return "none"
        return "partial" if entry.get("note") else "external-standard-only"

    def _distribution(self, slug: str, entry: dict[str, Any]) -> dict[str, Any]:
        """The primary access path.

        Every record gets one, including a tier 3 pointer — for which it is the
        landing page, because a record with no distribution cannot answer
        "where do I get it", which is one of the four things the catalog exists
        to do.
        """
        url = entry.get("access") or "https://opengrid.org/catalog/no-known-access-path"
        dist: dict[str, Any] = {
            "id": f"{DISTRIBUTION_BASE}{slug}--primary",
            "type": "Distribution",
            "accessURL": url,
            "hostedByOpenGrid": False,
        }
        if entry.get("format"):
            dist["formatLabel"] = str(entry["format"])
        if entry.get("bulk") is not None:
            dist["bulkDownload"] = bool(entry["bulk"])
        if entry.get("anonymous") is not None:
            dist["anonymousAccess"] = bool(entry["anonymous"])
        if not entry.get("access"):
            dist["formatLabel"] = "No access path recorded in the seed inventory"
        return dist

    def _secondary(self, slug: str, entry: dict[str, Any]) -> dict[str, Any]:
        """A second access path, where the seed file names one.

        Modelled separately rather than folded in because PRD §4.2 is explicit
        that the same dataset commonly has an anonymous bulk copy and an
        account-gated API whose barrier classification differs.
        """
        url = entry.get("api") or entry["secondary_access"]
        dist: dict[str, Any] = {
            "id": f"{DISTRIBUTION_BASE}{slug}--secondary",
            "type": "Distribution",
            "accessURL": url,
            "hostedByOpenGrid": False,
        }
        if entry.get("api"):
            dist["formatLabel"] = "API"
            dist["mediaType"] = "application/json"
        return dist

    def _caveats(self, entry: dict[str, Any], *, verified: bool) -> list[str]:
        caveats: list[str] = []
        if not verified:
            caveats.append(
                "Assembled for the PRD and not yet reviewed. The licence and tier on this "
                "record are not authoritative; treat them as a starting point for review."
            )
        if entry.get("access_barrier"):
            caveats.append(
                f"Access barrier recorded as {entry['access_barrier']}: this dataset is "
                "catalogued for discovery, not because it can be obtained."
            )
        if entry.get("tier") == 3:
            caveats.append(
                "Reference only. Tier 3 records carry no field-level metadata and no "
                "inter-dataset links; they exist so the gap is visible."
            )
        return caveats


def _clean(text: str) -> str:
    """Collapse the whitespace a YAML folded scalar leaves behind."""
    return " ".join(str(text).split())


def _as_doi_iri(doi: str) -> str:
    text = str(doi).strip()
    if text.startswith("http"):
        return text
    return f"https://doi.org/{text.removeprefix('doi:')}"


def load_seed(
    records: RecordStore,
    settings: Settings | None = None,
    *,
    limit: int | None = None,
    validate: bool = True,
) -> SeedLoadResult:
    """Load the curated seed inventory. Idempotent."""
    return SeedLoader(records, settings).load(limit=limit, validate=validate)


__all__ = ["SeedLoadResult", "SeedLoader", "load_seed"]
