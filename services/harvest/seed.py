"""Loading the curated seed inventory into the catalog (WP-2.5).

114 anchor datasets across DD1–DD10, from ``data/seed-sources.yaml``.

**The rule this module exists to enforce.** The seed file's header says the
DD1/DD5/DD8/DD9 entries came from a reviewed feasibility analysis and carry
``verified: true``; the rest were assembled for the PRD, have not been through
licence and access-path review, and:

> Do not treat the license or tier fields on unverified rows as authoritative.

So an unverified row **cannot reach the catalog graph**. It lands in
``og:graph/draft`` with ``og:reviewState "draft"``. The split is not a
convention here; it is a branch with a test on both sides, because a reviewed
record and an unreviewed one look identical to a user and only one of them has
had its licence checked.

**No review-queue entry, though this used to claim one.** The queue is an
operational-store table and this loader is handed a ``RecordStore``; only
``harvest.runner`` enqueues. So the 58 drafted rows are in the draft graph and
absent from the steward queue, which means nothing surfaces them for review —
and since a confirm with no queue row is now refused outright (#31), nothing can
promote them either. Whether ``datahub seed`` should write to the operational
store is a layering decision that belongs with the ingestion pipeline and
ADR-0012's promotion policy, not here; what does not belong here is a docstring
describing a behaviour the module does not have.

**Nothing is inferred.** A field the seed file does not state is left empty and
the completeness level says so. Most seed rows carry a name, a tier, a licence
string and an access URL, which is a level 1 record — and level 1 honestly
labelled is the whole point of PRD §6.
"""

from __future__ import annotations

import re
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
#: The seed file's `access_barrier` vocabulary, where it maps onto D9's.
#:
#: `fragmented` is deliberately absent. It used to map to `discontinued`, and
#: those are not the same claim in either direction: `ar:discontinued` says *the
#: publisher has stopped producing the dataset*, while every `fragmented` row is
#: a live subject with no canonical source — interconnection study results,
#: data-centre load projections, ELCC studies, all being produced right now, as
#: per-jurisdiction PDFs with incompatible methodologies. Six published records
#: asserted that their publishers had stopped, which is false about the
#: publishers and caps the record for the wrong reason.
#:
#: D9 has no member for "no canonical source", so an unmapped barrier falls
#: through to the conservative default in `_access` and the real reason travels
#: in `og:accessBarrier`, `og:pointerRationale` and the caveat — all three of
#: which say "fragmented" in words. Inventing a mapping to fill the enum is what
#: produced the wrong one.
BARRIER_MAP: dict[str, str] = {
    "restricted": "ceii",
    "commercial-paywall": "commercialPaywall",
    "proprietary": "commercialPaywall",
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
        NREL ATB's `curator_note` says explicitly to model it as one dataset
        with domain facets rather than two records. Treating them as two means
        the second write silently replaces the first.

        Identity is `_slug_of`: the row's explicit `slug` where it has one,
        otherwise the slugified name. This docstring used to say identity was
        the name, which was true until the `slug` keys were added and is what
        made the NREL ATB case impossible — its two rows are named differently
        ("NREL ATB (Annual Technology Baseline)" and "NREL ATB (storage and
        emerging technology tables)"), so a name-keyed merge never fired on the
        one row whose own note asks for it. The shared slug is what does it.

        The row carrying the most detail wins as the base; the others
        contribute only their domain. A verified row always beats an unverified
        one, because merging an unreviewed row's licence into a reviewed record
        would launder it.
        """
        by_slug: dict[str, list[HarvestedRecord]] = {}
        for record in harvested:
            by_slug.setdefault(_slug_of(record.payload), []).append(record)

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
                slug=_slug_of(base.payload),
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
        slug = _slug_of(entry)
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
        }

        # `dct:modified` is the *dataset's* vintage, and the Currency grade
        # reads it as one. This line used to be
        # `datetime.now(UTC).isoformat()` — the moment the loader ran — which
        # is a fact about the pipeline wearing the clothes of a fact about the
        # data. No seed row graded on it only because none carried a cadence;
        # the moment one does, a 2016 dataset reads Current on the strength of
        # today's date. `_t_datetime` in the normaliser states the rule this
        # restores: "a wrong `modified` timestamp is worse than a missing one".
        if vintage := _vintage(entry.get("last_update")):
            record["modified"] = vintage
        if cadence := _cadence(entry.get("update_frequency")):
            record["updateCadence"] = cadence

        # Fit-for-purpose (#53). `og:spatialGranularity` is a controlled term
        # and the shape rejects anything else, so a typo in the seed file loses
        # the record rather than the field — hence the check here, where the
        # row name is still in hand to name in the error.
        if granularity := entry.get("spatial_granularity"):
            if granularity not in SPATIAL_GRANULARITY:
                raise ValidationFailed(
                    f"{entry.get('name')!r} states spatial_granularity "
                    f"{granularity!r}, which is not one of "
                    f"{', '.join(sorted(SPATIAL_GRANULARITY))}"
                )
            record["spatialGranularity"] = granularity
        if resolution := _cadence(entry.get("time_resolution")):
            record["timeResolution"] = resolution

        if tier is not None:
            record["tier"] = tier
            if tier == 3:
                record["referenceOnly"] = True
        rationale = self._pointer_rationale(entry)
        if rationale:
            record["pointerRationale"] = rationale
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

        # No key at all when there is no access path, rather than an empty list:
        # the difference is "this record does not answer where to get it" versus
        # "it answers, with nothing", and the second is not a thing to say.
        primary = self._distribution(slug, entry)
        distributions = [primary] if primary is not None else []
        if entry.get("secondary_access") or entry.get("api"):
            distributions.append(self._secondary(slug, entry))
        if distributions:
            record["distribution"] = distributions

        record["qualityFlags"] = {
            "id": f"{iri}#flags",
            "type": "QualityFlags",
            # Always unknown, because the seed inventory states nothing about
            # currency. It used to say "current" for every verified row, which
            # conflated two different things: `verified: true` means a
            # cataloguer checked this row's licence and tier, not that the
            # dataset upstream is up to date. 56 records asserted a currency
            # posture nobody had looked at.
            "staleness": "unknown",
            "caveat": self._caveats(entry, verified=verified),
        }
        return record

    # ---- field groups ----------------------------------------------------

    #: Seed keys whose text is addressed to the cataloguer, not to the reader.
    #: Never projected into anything published.
    #:
    #: Enforced by construction rather than by consulting this tuple: nothing
    #: builds a record by iterating the row's keys, so a key is published only
    #: where a line names it. That is the stronger guarantee — a new curator-only
    #: key is excluded the moment it is added, with nothing to remember — but it
    #: is also invisible, which is how "Confirm with counsel before shipping the
    #: extraction" reached five published descriptions in the first place.
    #: `test_no_published_record_carries_text_written_for_a_cataloguer` is what
    #: holds it, and this tuple is what that test reads.
    CURATOR_ONLY = ("curator_note",)

    def _description(self, entry: dict[str, Any]) -> str:
        """A description assembled only from what the file states.

        The seed file has no description field, so this is built from the facts
        it does carry rather than invented. Saying less than the source is
        honest; saying more is not.

        `note` and `pointer_rationale` are reader-facing and go in. Editorial
        instructions do not: five published records used to carry text written
        for whoever was cataloguing them, including "Confirm with counsel before
        shipping the extraction" — an unresolved legal question rendered as a
        dataset's public description. Those sentences now live in
        `curator_note`, which nothing reads.
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
        """One decision, two coherent fields — and a caveat when it is a default.

        These used to be defaulted independently, and the two defaults
        disagreed: a row with neither ``anonymous`` nor ``access_barrier`` — 42
        of the 56 verified rows — was published as ``accessRestriction: none``
        *and* ``anonymousAccess: false``. "Nothing stands between you and this
        dataset" beside "you cannot retrieve it without an account", on the same
        record, about a row the seed inventory says nothing about either way.

        PRD §14.2 wants silence recorded as "not captured", and the model has
        nowhere to put that: ``og:anonymousAccess`` is ``sh:minCount 1`` at level
        1 because it is a Tier 1 criterion an unauthenticated evaluator filters
        on, and ``og:accessRestriction`` takes one of the six concepts PRD D9
        fixes, none of which means "not established". So the record has to say
        something, and what it says is derived once, conservatively, and
        flagged: assume a barrier until somebody checks, rather than promise
        open access nobody verified. A reader told they may need an account and
        finding they do not has lost nothing; the reverse sends them at a wall.

        The caveat is where the truth goes — that this is an assumption and not
        a finding — until the schema can hold it. See #39.
        """
        anonymous = entry.get("anonymous")
        barrier = entry.get("access_barrier")
        restriction = BARRIER_MAP.get(barrier or "")
        if restriction is None and _cadence(entry.get("update_frequency")) == "discontinued":
            # `ar:discontinued` is on this axis by design, and its scope note
            # says why: "not an access gate — og:blocksAnonymousAccess is false
            # because a discontinued dataset is often still anonymously
            # downloadable — but it belongs on this axis because it is the
            # reason a path stops resolving". Its own worked example is OPSD,
            # one of these rows. A stated barrier still wins: CEII and a
            # paywall are harder gates than a publisher having stopped.
            restriction = "discontinued"
        if restriction is None:
            restriction = "none" if anonymous is True else "accountRequired"
        access: dict[str, Any] = {
            "accessRestriction": f"{SCHEME_ACCESS_RESTRICTION}/{restriction}",
            "anonymousAccess": anonymous is True,
        }
        # Also on the dataset, not only on the distribution. `_distribution`
        # returns None for a row with no access path, so `bulk` on a
        # reference-only row had nowhere to go and was silently dropped —
        # PLEXOS-World, SciGRID and the GridPath RA Toolkit each state bulk
        # availability the catalog was told and discarded (#57).
        if entry.get("bulk") is not None:
            access["bulkDownload"] = bool(entry["bulk"])
        return access

    @staticmethod
    def _access_is_assumed(entry: dict[str, Any]) -> bool:
        """Whether the access posture on this record is a default, not a fact.

        A barrier `BARRIER_MAP` does not cover counts as assumed too. `fragmented`
        is one — a real barrier the seed file states, which D9 has no member for
        — so the restriction that reaches the record is the conservative default
        rather than a translation of it, and the caveat has to say so.
        """
        if entry.get("anonymous") is not None:
            return False
        if _cadence(entry.get("update_frequency")) == "discontinued":
            return False  # derived from a stated fact, not defaulted
        barrier = entry.get("access_barrier")
        return not barrier or barrier not in BARRIER_MAP

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
        """What the seed inventory establishes about documentation. Not much.

        The absence of a `note` used to produce `external-standard-only`, which
        is a specific positive claim — *this dataset's fields are documented by
        reference to an external standard* — asserted on 48 published records
        because a column was empty. The seed file says nothing about
        documentation for any row; it has no field for it.

        `none` instead. Still a claim, because `og:documentationStatus` is
        `sh:minCount 1` at level 1 and the enum has no "not established" member —
        the same bind `_access` documents and #39 tracks — but it is the
        conservative one in both directions that matter. It does not credit a
        dataset with documentation nobody looked for, and it does not prejudge
        the grade: `documentation.assess` special-cases only
        `external-standard-only`, capping it at C, and lets everything else be
        graded from the fields the record actually carries. So a seed row later
        enriched to level 2 is graded on its fields rather than on this guess.
        The caveat in `_caveats` says the value is an absence, not a finding.

        `external-standard-only` is now a value the seed loader never invents.
        It can only arrive from a curated record, where somebody checked.
        """
        if entry.get("tier") == 3:
            return "none"
        return "partial" if entry.get("note") else "none"

    def _pointer_rationale(self, entry: dict[str, Any]) -> str:
        """Why this record has no access path, in the reader's words (#18).

        Required by ``og:AccessPathShape`` of any reference-only record with no
        distribution, so it cannot be left to whether the inventory happened to
        fill the column in. 27 of the 34 do; the rest fall through to a barrier
        stated as a concept, and then to the honest last resort.

        The fallback is a statement about the *record*, not about the dataset —
        "the inventory records no reason" rather than "there is no reason". PRD
        §14.2: a missing field means not captured, never does not exist. It is
        deliberately not spliced into the description, which is assembled from
        what the file states about the dataset itself.
        """
        stated = _clean(entry.get("pointer_rationale") or "")
        if stated:
            return stated
        if not entry.get("access"):
            barrier = entry.get("access_barrier")
            if barrier:
                return (
                    f"No access path is recorded. The seed inventory classifies the barrier "
                    f"as {barrier}."
                )
            return (
                "No access path is recorded, and the seed inventory gives no reason for its "
                "absence. Catalogued so the gap is visible; treat the absence as unexamined "
                "rather than as evidence that the dataset cannot be obtained."
            )
        return ""

    def _distribution(self, slug: str, entry: dict[str, Any]) -> dict[str, Any] | None:
        """The primary access path, or ``None`` where the inventory records none.

        ``None`` for 34 of the 114 rows, all of them tier 3 pointers with no
        access URL, no DOI and no secondary access — CEII-designated, commercial,
        membership-restricted or superseded datasets that the catalog lists so
        the gap is visible (PRD §5) and does not claim to offer.

        This used to mint ``https://opengrid.org/catalog/no-known-access-path``
        and hand it back as a real distribution, because level 1 required one and
        a distribution requires an accessURL. The UI rendered it as a live
        "Open at source" button on 23 of 66 published records. A fabricated URL
        is precisely what PRD §14.4 forbids, and the constraint that forced it
        has moved: ``og:AccessPathShape`` now asks a reference-only record for
        ``og:pointerRationale`` instead of a URL it does not have.
        """
        url = entry.get("access")
        if not url:
            return None
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
            # `formatLabel: "API"` and no `mediaType`. This used to assert
            # `application/json` for all seven rows with an `api` URL, on no
            # evidence — the seed file states a media type for none of them —
            # and wrongly for at least NREL's NSRDB endpoint, which answers CSV.
            # A media type is a concrete fact a client negotiates on and the
            # format facet indexes, so inventing one is the same error as
            # inventing an access URL, in a field that looks more like plumbing.
            # `dcat:mediaType` is `sh:maxCount 1` and not required; absent is
            # the honest value until a probe reads the response.
            dist["formatLabel"] = "API"
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
        if self._access_is_assumed(entry):
            caveats.append(
                "The access barrier has not been checked. The seed inventory records neither "
                "anonymous access nor a barrier for this dataset, so the record assumes an "
                "account is needed rather than promising open access nobody verified. It may "
                "well be freely downloadable."
            )
        if entry.get("tier") != 3 and not entry.get("note"):
            caveats.append(
                "Documentation status records an absence, not a finding. The seed inventory "
                "has no field for documentation, so this record says none because nothing was "
                "captured — not because the dataset is undocumented. Many of these are "
                "extensively documented upstream."
            )
        if entry.get("tier") == 3:
            caveats.append(
                "Reference only. Tier 3 records carry no field-level metadata and no "
                "inter-dataset links; they exist so the gap is visible."
            )
        return caveats


#: `og:spatialGranularity`'s controlled vocabulary, per the shape at
#: `shapes/opengrid-datahub.ttl`. Repeated here rather than read from the
#: shape because the loader has to reject a bad value *before* the record is
#: built, while it can still say which seed row is wrong.
SPATIAL_GRANULARITY: frozenset[str] = frozenset(
    {"nodal", "zonal", "gridded", "administrative", "point", "national", "global"}
)


#: `update_frequency` in the seed file, mapped onto `og:updateCadence`'s
#: grammar: an ISO 8601 duration, or one of irregular / on-demand /
#: discontinued. The workbook writes cadence as prose, so this covers the
#: forms it actually uses and nothing else.
#:
#: `discontinued` is deliberately hard to reach. It says *the publisher has
#: stopped producing this*, which is a claim about the publisher, and
#: `BARRIER_MAP`'s comment records what happened last time it was mapped
#: loosely. "Last updated 2016" is evidence of dormancy, not a statement of
#: discontinuation, so it yields a vintage and no cadence — the record then
#: says 2016 plainly and Currency stays unassessed, which is the honest pair.
#: Only an explicit withdrawal reaches it.
CADENCE_MAP: dict[str, str] = {
    "annual": "P1Y",
    "~annual": "P1Y",
    "annual but w 2-3 yr lag": "P1Y",
    "annual w 2 year delay": "P1Y",
    "quarterly": "P3M",
    "monthly": "P1M",
    "ongoing": "irregular",
    "not regular": "irregular",
    # The controlled tokens, so a row may state one directly.
    "irregular": "irregular",
    "on-demand": "on-demand",
    "varies by country": "irregular",
    "discontinued": "discontinued",
    "no further support planned": "discontinued",
}


def _cadence(raw: Any) -> str | None:
    """The cadence a seed row states, or nothing.

    Nothing is the common answer and the right one. `NA`, `?`, `-`, `Sub-annual`
    and `Real time` are all in the workbook and none is an interval; inventing
    one would put a number into a grade that compares against it.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Case matters here and nowhere else: `P1M` is a month and `PT1M` a minute,
    # so the duration is matched against the text as written and only the
    # prose lookup is case-folded.
    if _DURATION_TOKEN.match(text):
        return text
    return CADENCE_MAP.get(text.lower())


_DURATION_TOKEN = re.compile(r"^P(?:\d+[YMWD])+(?:T(?:\d+[HMS])+)?$|^PT(?:\d+[HMS])+$")
_YEAR_ONLY = re.compile(r"^\d{4}$")
_YEAR_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def _vintage(raw: Any) -> str | None:
    """The dataset's last known update, as an xsd:dateTime.

    A bare year means the year, not mid-year: `2016` becomes 2016-01-01, the
    earliest moment consistent with what was stated. Rounding the other way
    would make a dataset look up to twelve months fresher than the evidence
    supports, and this feeds a staleness grade.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if _YEAR_ONLY.match(text):
        return f"{text}-01-01T00:00:00Z"
    if _YEAR_MONTH.match(text):
        return f"{text}-01T00:00:00Z"
    try:
        return datetime.fromisoformat(text).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
    except ValueError:
        log.warning("seed row states an unparseable last_update", value=text)
        return None


def _slug_of(entry: dict[str, Any]) -> str:
    """This row's identity: the ``slug`` key if the file gives one, else the name.

    The identifier used to be the slugified name and nothing else, which made
    two things impossible to say.

    A seed row and a curated record are the same dataset. Six of them were
    published twice — "PyPSA-Eur grid dataset (pre-built OSM network)" and
    "PyPSA-Eur Grid Dataset (pre-built OSM network)", byte-identical to a reader,
    with different completeness levels and different grades. A duplicate is
    worse than a missing record: nobody can tell which copy is authoritative,
    and the link service treats one dataset as two related ones.

    Two seed rows in different domains are the same dataset. The merge below
    has always handled that and keyed on the *name*, so it only fired when the
    names matched exactly. NREL ATB is in the file twice with the instruction
    "Model as one dataset with domain facets, not two records" written in its
    own note, and the names differ, so it published as two records — three,
    with the curated one.

    The alternative was fuzzy matching on titles or access URLs, which would
    silently merge two datasets that happen to share a landing page. An explicit
    key says what a person decided, and shows up in a diff.
    """
    declared = str(entry.get("slug") or "").strip()
    return declared or slugify(entry["name"])


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
