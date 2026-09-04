"""Deriving the two fields no source states outright (WP-3.5).

Level 1 requires ``og:dataDomain`` and ``og:provenanceClass``, and no harvest
source publishes either. They have to be derived — but they are not the same
kind of thing, and treating them the same would be the mistake.

**Data domain is a filing decision.** Which of DD1–DD10 a dataset belongs in is
a statement about its subject matter. Getting it wrong files a record in the
wrong drawer: a user browsing DD5 does not see it, which is bad, and a steward
fixes it in seconds. So it is inferred, from term signatures and from the
domains the harvest source itself declares — and every inferred assignment is
marked ``og:inferredAssignment true`` with a basis, so nobody mistakes it for a
curator's judgement.

**Provenance class is a quality claim.** It caps the Provenance grade
(PRD §6), so a wrong one is not a mis-filing, it is the catalog asserting
something false about how the numbers came to exist. It is therefore *never*
guessed. Where the source's own words determine it — a dataset that says
"reanalysis" is reanalysis, one that says it was extracted from OpenStreetMap
is OSM-derived — it is set, with the phrase that determined it recorded as the
basis. Where they do not, it is left absent, the record fails level 1, and it
goes to ``flagged`` for a steward. That costs throughput and it is the right
trade: PRD principle 2 says absent means "not captured", and inventing a
provenance class would make that principle unenforceable exactly where it
matters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from datahub.namespaces import SCHEME_DATA_DOMAIN, SCHEME_PROVENANCE_CLASS

#: Term signatures per data domain, from the domain names and structural notes
#: in ``data/seed-sources.yaml``. Weighted: a term in the first list is
#: near-decisive for that domain, one in the second is suggestive.
DOMAIN_SIGNATURES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # DD1 Network topology & parameters
    "DD1": (
        (
            "transmission network",
            "network topology",
            "grid topology",
            "transmission line",
            "substation",
            "one-line diagram",
            "single-line diagram",
            "busbar",
            "line impedance",
            "power flow case",
            "matpower",
            "pypsa",
            "powsybl",
            "cim cgmes",
            "circuit parameters",
        ),
        ("topology", "impedance", "reactance", "kilovolt", "circuit", "feeder", "interconnector"),
    ),
    # DD2 Generator fleet
    "DD2": (
        (
            "power plant",
            "generator fleet",
            "generation fleet",
            "generating unit",
            "plant inventory",
            "unit inventory",
            "nameplate capacity",
            "heat rate",
            "commissioning date",
            "retirement date",
        ),
        ("plant", "unit", "turbine", "fleet", "capacity mw", "generator"),
    ),
    # DD3 IC queue & project pipeline
    "DD3": (
        (
            "interconnection queue",
            "queued up",
            "project pipeline",
            "queue position",
            "interconnection agreement",
            "withdrawn projects",
            "cluster study",
        ),
        ("queue", "pipeline", "proposed projects", "permitting", "planned capacity"),
    ),
    # DD4 Load & demand
    "DD4": (
        (
            "electricity demand",
            "electricity load",
            "load profile",
            "demand profile",
            "hourly load",
            "load forecast",
            "peak demand",
            "electricity consumption",
            "balancing authority",
        ),
        ("demand", "load", "consumption", "hourly", "peak"),
    ),
    # DD5 Renewable resource & weather
    "DD5": (
        (
            "wind resource",
            "solar resource",
            "wind speed",
            "irradiance",
            "reanalysis",
            "weather data",
            "capacity factor",
            "wind atlas",
            "solar atlas",
            "meteorological",
            "climate reanalysis",
        ),
        ("wind", "solar", "weather", "climate", "temperature", "hydrology", "runoff"),
    ),
    # DD6 Emerging technology parameters
    "DD6": (
        (
            "battery storage",
            "energy storage",
            "long duration storage",
            "electrolyser",
            "electrolyzer",
            "direct air capture",
            "hydrogen production",
            "carbon capture",
            "emerging technology",
        ),
        ("storage", "hydrogen", "battery", "ccs", "geothermal", "smr"),
    ),
    # DD7 Fuel & commodity
    "DD7": (
        (
            "natural gas price",
            "coal price",
            "fuel price",
            "henry hub",
            "commodity price",
            "fuel cost",
            "gas supply",
            "carbon price",
            "emissions trading",
        ),
        ("fuel", "commodity", "gas", "coal", "oil", "price index", "allowance"),
    ),
    # DD8 Policy & regulatory
    "DD8": (
        (
            "renewable portfolio standard",
            "policy target",
            "regulatory order",
            "market rule",
            "reliability standard",
            "capacity market",
            "feed-in tariff",
            "contracts for difference",
            "emissions trading scheme",
            "nationally determined contribution",
        ),
        ("policy", "regulation", "regulatory", "directive", "legislation", "target", "mandate"),
    ),
    # DD9 Cost & financial
    "DD9": (
        (
            "annual technology baseline",
            "levelized cost of electricity",
            "levelised cost of electricity",
            "lcoe",
            "capital cost",
            "overnight capital cost",
            "cost and performance",
            "discount rate",
            "financing cost",
            "cost projection",
        ),
        ("cost", "capex", "opex", "tariff", "investment", "financial", "price forecast"),
    ),
    # DD10 Geospatial & siting
    "DD10": (
        (
            "land cover",
            "land use",
            "protected area",
            "exclusion zone",
            "siting",
            "bathymetry",
            "digital elevation",
            "terrain",
            "administrative boundaries",
            "population density",
        ),
        ("geospatial", "raster", "shapefile", "geotiff", "elevation", "parcel", "zoning"),
    ),
}

#: Phrases that *determine* a provenance class rather than suggest one. Each is
#: a statement the source makes about how its values came to exist, not an
#: inference from subject matter. Absent one of these, no class is set.
PROVENANCE_EVIDENCE: tuple[tuple[str, str], ...] = (
    ("reanalysis", "reanalysis"),
    ("era5", "reanalysis"),
    ("merra-2", "reanalysis"),
    ("merra2", "reanalysis"),
    ("data assimilation", "reanalysis"),
    ("numerical weather prediction", "reanalysis"),
    ("openstreetmap", "osmDerived"),
    ("osm extract", "osmDerived"),
    ("derived from osm", "osmDerived"),
    ("synthetic network", "synthetic"),
    ("synthetic grid", "synthetic"),
    ("synthetic power system", "synthetic"),
    ("statistically representative", "synthetic"),
    ("fictitious but realistic", "synthetic"),
    ("scenario output", "modeled"),
    ("model output", "modeled"),
    # A description that opens "Modeled wind speed and power output" is
    # stating how its values came to exist, which is exactly the bar. Kept as
    # whole-token matches so "remodelled" and "modelling guidance" do not hit.
    ("modeled", "modeled"),
    ("modelled", "modeled"),
    ("mesoscale model", "modeled"),
    ("numerical model", "modeled"),
    ("wrf", "modeled"),
    ("capacity expansion model", "modeled"),
    ("projections of cost", "modeled"),
    ("cost and performance projections", "modeled"),
    ("expert elicitation", "modeled"),
    ("simulation results", "modeled"),
    ("metered", "primary"),
    ("as reported by the operator", "primary"),
    ("mandatory reporting", "primary"),
    ("survey response", "primary"),
    ("official statistics", "primary"),
    ("regulatory order", "institutional"),
    ("market rule", "institutional"),
    ("reliability standard", "institutional"),
    ("policy target", "institutional"),
    ("legislation", "institutional"),
    ("bias-corrected", "derived"),
    ("bias corrected", "derived"),
    ("downscaled", "derived"),
    ("computed from", "derived"),
    ("aggregated from", "derived"),
    ("harmonised from", "curated"),
    ("harmonized from", "curated"),
    ("compiled from", "curated"),
    ("cleaned and reconciled", "curated"),
    ("deduplicated", "curated"),
)

#: Score a domain must reach to be assigned at all.
DOMAIN_FLOOR = 1.0
#: How close a runner-up must be to the leader to also be assigned. A dataset
#: of wind speeds *and* capacity factors is genuinely DD5 and DD10; forcing one
#: label would lose that.
DOMAIN_MARGIN = 0.75

_WORD = re.compile(r"[a-z0-9][a-z0-9+/.-]*")


@dataclass(slots=True)
class Classification:
    domains: list[str] = field(default_factory=list)
    provenance: str | None = None
    #: Human-readable, and stored on the record as ``og:inferenceBasis``. A
    #: drafted value with no basis fails validation (ADR-0005), so this is not
    #: optional decoration.
    domain_basis: str = ""
    provenance_basis: str = ""

    @property
    def domain_iris(self) -> list[str]:
        return [f"{SCHEME_DATA_DOMAIN}/{d}" for d in self.domains]

    @property
    def provenance_iri(self) -> str | None:
        return f"{SCHEME_PROVENANCE_CLASS}/{self.provenance}" if self.provenance else None


def classify(text: str, *, candidates: list[str] | None = None) -> Classification:
    """Derive domains and, where the text determines it, a provenance class.

    ``candidates`` is the domain list the harvest source declares for itself in
    ``data/seed-sources.yaml``. It is used as a tie-breaker and a prior, never
    as a hard filter: a source that says it carries DD1 and DD2 can still
    publish a DD5 dataset, and silently refiling that one as DD1 would be worse
    than the source's list being incomplete.
    """
    haystack = _normalise(text)
    scores = _score_domains(haystack, candidates or [])
    result = Classification()

    if scores:
        best = scores[0][1]
        chosen = [name for name, score in scores if score >= best - DOMAIN_MARGIN]
        result.domains = sorted(chosen)
        detail = ", ".join(f"{name} {score:.1f}" for name, score in scores[:4])
        result.domain_basis = (
            f"Assigned by term-signature match over the source record: {detail}."
            + (f" Source declares {', '.join(candidates)}." if candidates else "")
        )

    for phrase, concept in PROVENANCE_EVIDENCE:
        if phrase in haystack:
            result.provenance = concept
            result.provenance_basis = (
                f'Determined by the phrase "{phrase}" in the source record, which states how '
                f"the values were produced rather than what they are about."
            )
            break

    return result


def _score_domains(haystack: str, candidates: list[str]) -> list[tuple[str, float]]:
    scored: list[tuple[str, float]] = []
    for domain, (decisive, suggestive) in DOMAIN_SIGNATURES.items():
        score = 0.0
        score += 1.6 * sum(1 for term in decisive if _contains(haystack, term))
        score += 0.4 * sum(1 for term in suggestive if _contains(haystack, term))
        if domain in candidates and score > 0:
            # A prior, not a gate: it breaks ties between two domains the text
            # supports equally, and does nothing at all to a domain the text
            # does not support.
            score += 0.5
        if score >= DOMAIN_FLOOR:
            scored.append((domain, score))
    return sorted(scored, key=lambda pair: (-pair[1], pair[0]))


def _normalise(text: str) -> str:
    tokens: list[str] = []
    for token in _WORD.findall(str(text).lower()):
        tokens.append(token)
        if "-" in token or "/" in token:
            tokens.extend(part for part in re.split(r"[-/]", token) if part)
    return " ".join(tokens)


def _contains(haystack: str, term: str) -> bool:
    return f" {term} " in f" {haystack} "


__all__ = [
    "DOMAIN_FLOOR",
    "DOMAIN_MARGIN",
    "DOMAIN_SIGNATURES",
    "PROVENANCE_EVIDENCE",
    "Classification",
    "classify",
]
