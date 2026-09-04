"""Namespace registry and named-graph IRIs.

Single source of truth for every IRI prefix the project uses. Importing a
namespace from anywhere else is a bug: prefix drift between a shape, a query and
a record is the kind of defect that produces empty result sets rather than
errors.
"""

from __future__ import annotations

from rdflib import Namespace
from rdflib.namespace import DCAT, DCTERMS, FOAF, OWL, PROV, RDF, RDFS, SDO, SKOS, XSD

#: The OpenGrid extension namespace (PRD §4).
OG = Namespace("https://schema.opengrid.org/ns#")

#: QUDT unit and quantity-kind vocabularies (PRD §4.3 C5).
QUDT = Namespace("http://qudt.org/schema/qudt/")
UNIT = Namespace("http://qudt.org/vocab/unit/")
QUANTITYKIND = Namespace("http://qudt.org/vocab/quantitykind/")

#: SPDX licence identifiers (PRD §4.1 D8).
SPDX = Namespace("http://spdx.org/licenses/")

#: SHACL, used by the validation runner to read validation reports.
SH = Namespace("http://www.w3.org/ns/shacl#")

#: Dublin Core elements 1.1, present in harvested CKAN and OAI records.
DC = Namespace("http://purl.org/dc/elements/1.1/")

#: VoID, used for dataset-level statistics on the catalog itself.
VOID = Namespace("http://rdfs.org/ns/void#")

#: ADMS, used by DCAT-AP sources for identifier and status statements.
ADMS = Namespace("http://www.w3.org/ns/adms#")

#: Concept scheme roots. Every controlled value in the catalog lives under one.
CONCEPT_BASE = "https://schema.opengrid.org/concept/"
SCHEME_DATA_DOMAIN = f"{CONCEPT_BASE}data-domain"
SCHEME_PROVENANCE_CLASS = f"{CONCEPT_BASE}provenance-class"
SCHEME_ANALYSIS_TYPE = f"{CONCEPT_BASE}analysis-type"
SCHEME_ACCESS_RESTRICTION = f"{CONCEPT_BASE}access-restriction"
SCHEME_GRID_CONCEPT = f"{CONCEPT_BASE}grid-concept"

#: Instance IRI bases. Records are minted under these.
DATASET_BASE = "https://catalog.opengrid.org/ds/"
DISTRIBUTION_BASE = "https://catalog.opengrid.org/dist/"
FIELD_BASE = "https://catalog.opengrid.org/field/"
FILE_BASE = "https://catalog.opengrid.org/file/"
LINK_BASE = "https://catalog.opengrid.org/link/"
AGENT_BASE = "https://catalog.opengrid.org/agent/"

#: Prefixes bound on every graph the project creates and prepended to every
#: SPARQL query built by :func:`datahub.graph.sparql.prologue`.
PREFIXES: dict[str, Namespace | str] = {
    "og": OG,
    "dcat": DCAT,
    "dct": DCTERMS,
    "prov": PROV,
    "skos": SKOS,
    "qudt": QUDT,
    "unit": UNIT,
    "quantitykind": QUANTITYKIND,
    "rdf": RDF,
    "rdfs": RDFS,
    "owl": OWL,
    "xsd": XSD,
    "sh": SH,
    "foaf": FOAF,
    "schema": SDO,
    "spdx": SPDX,
    "adms": ADMS,
    "void": VOID,
    "dc": DC,
}

__all__ = [
    "ADMS",
    "AGENT_BASE",
    "CONCEPT_BASE",
    "DATASET_BASE",
    "DC",
    "DCAT",
    "DCTERMS",
    "DISTRIBUTION_BASE",
    "FIELD_BASE",
    "FILE_BASE",
    "FOAF",
    "LINK_BASE",
    "OG",
    "OWL",
    "PREFIXES",
    "PROV",
    "QUANTITYKIND",
    "QUDT",
    "RDF",
    "RDFS",
    "SCHEME_ACCESS_RESTRICTION",
    "SCHEME_ANALYSIS_TYPE",
    "SCHEME_DATA_DOMAIN",
    "SCHEME_GRID_CONCEPT",
    "SCHEME_PROVENANCE_CLASS",
    "SDO",
    "SH",
    "SKOS",
    "SPDX",
    "UNIT",
    "VOID",
    "XSD",
]
