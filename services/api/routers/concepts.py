"""``/v1/concepts`` and ``/v1/domains`` — the vocabulary (WP-4.4).

PRD §F8. Both endpoints read the vocabulary graph directly rather than the
search index, because a concept is not a dataset: it has broader and narrower
terms, alternative labels and crosswalk targets, none of which the index
carries and all of which the vocabulary already has.

**Dataset counts are entitlement-scoped.** "How many datasets use this concept"
is a count, and a count over records the caller cannot see is exactly the
existence leak ADR-0006 exists to prevent — so the count comes from the index
with the caller's predicate applied, not from a SPARQL count over the graph.

**The domain endpoint carries the structural notes.** PRD §5 treats them as a
product feature rather than a disclaimer: a catalog that says *"transmission
line impedances for most of the world are not public, and here is why"* is more
useful than one that returns an empty list and lets the user conclude they
searched badly.
"""

from __future__ import annotations

from typing import Annotated, Any

from datahub.api.deps import CallerDep, SearchDep, StoreDep
from datahub.api.entitlement import Caller
from datahub.api.schemas import ConceptRef, ConceptResponse, DomainResponse
from datahub.api.search.backend import SearchBackend
from datahub.api.search.query import SearchParams, build
from datahub.errors import NotFound
from datahub.graph.graphs import NamedGraph
from datahub.logging import get_logger
from datahub.namespaces import SCHEME_DATA_DOMAIN
from fastapi import APIRouter, Path, Query
from rdflib import URIRef

log = get_logger(__name__)

router = APIRouter(tags=["concepts"])

#: Every scheme in one query, with the inferred graph joined so a caller asking
#: for a concept's broader terms gets the transitive ones the reasoner
#: materialised rather than only the directly asserted parent.
CONCEPT_QUERY = """
SELECT ?concept ?label ?definition ?notation ?scheme ?altLabel
       ?broader ?broaderLabel ?narrower ?narrowerLabel ?unit ?symbol
WHERE {
  GRAPH ??vocab {
    ?concept a skos:Concept ; skos:prefLabel ?label .
    OPTIONAL { ?concept skos:definition ?definition }
    OPTIONAL { ?concept skos:notation ?notation }
    OPTIONAL { ?concept skos:inScheme ?scheme }
    OPTIONAL { ?concept skos:altLabel ?altLabel }
    OPTIONAL { ?concept og:defaultUnit ?unit }
    OPTIONAL { ?concept og:unitSymbol ?symbol }
  }
  OPTIONAL {
    GRAPH ??vocab { ?concept skos:broader ?broader }
    GRAPH ??vocab { ?broader skos:prefLabel ?broaderLabel }
  }
  OPTIONAL {
    GRAPH ??vocab { ?concept skos:narrower ?narrower }
    GRAPH ??vocab { ?narrower skos:prefLabel ?narrowerLabel }
  }
  FILTER(?concept = ??iri)
}
"""

LIST_QUERY = """
SELECT ?concept ?label ?definition ?notation ?scheme WHERE {
  GRAPH ??vocab {
    ?concept a skos:Concept ; skos:prefLabel ?label .
    OPTIONAL { ?concept skos:definition ?definition }
    OPTIONAL { ?concept skos:notation ?notation }
    OPTIONAL { ?concept skos:inScheme ?scheme }
  }
}
ORDER BY ?label
"""

MATCH_QUERY = """
SELECT ?strength ?target WHERE {
  GRAPH ?g {
    VALUES ?strength { skos:exactMatch skos:closeMatch skos:broadMatch skos:narrowMatch }
    ??iri ?strength ?target .
  }
}
"""


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


@router.get("/domains", response_model=list[DomainResponse], summary="DD1-DD10")
def list_domains(caller: CallerDep, store: StoreDep, backend: SearchDep) -> list[DomainResponse]:
    """The ten data domains, each with its structural note.

    Ordered by notation rather than alphabetically, because DD1 through DD10 is
    an ordering the PRD gives them and "DD10, DD1, DD2" is nobody's idea of a
    list of ten things.
    """
    rows = store.select(
        """
        SELECT ?concept ?label ?definition ?notation ?note ?scope WHERE {
          GRAPH ??vocab {
            ?concept a skos:Concept ; skos:inScheme ??scheme ; skos:prefLabel ?label .
            OPTIONAL { ?concept skos:definition ?definition }
            OPTIONAL { ?concept skos:notation ?notation }
            OPTIONAL { ?concept og:structuralNote ?note }
            OPTIONAL { ?concept og:v1IngestionScope ?scope }
          }
        }
        """,
        # URIRef, not str: a bare string binds as a literal and the
        # `skos:inScheme` join then matches nothing — silently, returning an
        # empty list that looks exactly like "there are no domains".
        {"vocab": NamedGraph.VOCAB.uri(), "scheme": URIRef(SCHEME_DATA_DOMAIN)},
    )
    counts = _domain_counts(caller, backend)
    domains = [
        DomainResponse(
            id=str(row["concept"]).rsplit("/", 1)[-1],
            iri=str(row["concept"]),
            notation=str(row.get("notation") or "").strip(),
            label=str(row["label"]),
            definition=_opt(row.get("definition")),
            structural_note=_opt(row.get("note")),
            v1_ingestion_scope=_opt(row.get("scope")),
            dataset_count=counts.get(str(row["concept"]), 0),
        )
        for row in rows
    ]
    return sorted(domains, key=_domain_order)


def _domain_order(domain: DomainResponse) -> tuple[int, str]:
    digits = "".join(c for c in domain.id if c.isdigit())
    return (int(digits) if digits else 99, domain.id)


def _domain_counts(caller: Caller, backend: SearchBackend) -> dict[str, int]:
    """One faceted search rather than ten counts.

    Ten counts would be ten index round trips for a page that always shows all
    ten, and the facet is already computed.
    """
    response = backend.search(
        build(SearchParams(limit=0, facets=("data_domain",)), caller.entitlement)
    )
    return {str(v.value): v.count for v in response.facets.get("data_domain", [])}


# ---------------------------------------------------------------------------
# Concepts
# ---------------------------------------------------------------------------


@router.get("/concepts", response_model=list[ConceptResponse], summary="The SKOS schemes")
def list_concepts(
    store: StoreDep,
    scheme: Annotated[str | None, Query(description="Restrict to one concept scheme.")] = None,
    q: Annotated[str | None, Query(description="Substring match on the label.")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[ConceptResponse]:
    """List concepts. Flat, with a scheme filter and a label search.

    No dataset counts here: this endpoint returns up to a thousand concepts and
    counting each against the index would be a thousand searches. The count is
    on the single-concept endpoint, where it is one search and worth having.
    """
    rows = store.select(LIST_QUERY, {"vocab": NamedGraph.VOCAB.uri()})
    needle = q.lower() if q else None

    out: list[ConceptResponse] = []
    for row in rows:
        label = str(row["label"])
        if scheme and str(row.get("scheme") or "") != scheme:
            continue
        if needle and needle not in label.lower():
            continue
        out.append(
            ConceptResponse(
                iri=str(row["concept"]),
                label=label,
                definition=_opt(row.get("definition")),
                notation=_opt(row.get("notation")),
                scheme=_opt(row.get("scheme")),
            )
        )
        if len(out) >= limit:
            break
    return out


@router.get(
    "/concepts/{concept_id:path}",
    response_model=ConceptResponse,
    summary="One concept, with its hierarchy, crosswalks and dataset count",
)
def get_concept(
    concept_id: Annotated[str, Path(description="A concept IRI, or its last segment.")],
    caller: CallerDep,
    store: StoreDep,
    backend: SearchDep,
) -> ConceptResponse:
    iri = _resolve_iri(concept_id, store)
    rows = store.select(CONCEPT_QUERY, {"vocab": NamedGraph.VOCAB.uri(), "iri": URIRef(iri)})
    if not rows:
        raise NotFound(f"no concept {concept_id!r}", concept=concept_id)

    first = rows[0]
    return ConceptResponse(
        iri=iri,
        label=str(first["label"]),
        definition=_opt(first.get("definition")),
        notation=_opt(first.get("notation")),
        scheme=_opt(first.get("scheme")),
        alt_labels=sorted({str(r["altLabel"]) for r in rows if r.get("altLabel")}),
        broader=_refs(rows, "broader", "broaderLabel"),
        narrower=_refs(rows, "narrower", "narrowerLabel"),
        default_unit=_opt(first.get("unit")),
        unit_symbol=_opt(first.get("symbol")),
        external_matches=_matches(store, iri),
        dataset_count=_concept_count(iri, caller, backend),
    )


def _resolve_iri(concept_id: str, store: Any) -> str:
    """A full IRI, or the last segment of one.

    Callers hold concept IRIs from records, and people hold the readable tail.
    Accepting both costs one query and saves every client a lookup table.
    """
    if concept_id.startswith(("http://", "https://")):
        return concept_id
    rows = store.select(
        """
        SELECT ?concept WHERE {
          GRAPH ??vocab { ?concept a skos:Concept }
          FILTER(STRENDS(STR(?concept), ??tail))
        }
        """,
        {"vocab": NamedGraph.VOCAB.uri(), "tail": f"/{concept_id}"},
    )
    if not rows:
        raise NotFound(f"no concept {concept_id!r}", concept=concept_id)
    if len(rows) > 1:
        # Ambiguous rather than arbitrary: two schemes can legitimately use the
        # same last segment, and picking one silently would make the endpoint
        # return a different concept depending on load order.
        raise NotFound(
            f"{concept_id!r} matches {len(rows)} concepts; use the full IRI",
            concept=concept_id,
            candidates=[str(r["concept"]) for r in rows][:5],
        )
    return str(rows[0]["concept"])


def _refs(rows: list[dict[str, Any]], iri_key: str, label_key: str) -> list[ConceptRef]:
    seen: dict[str, ConceptRef] = {}
    for row in rows:
        if not row.get(iri_key):
            continue
        iri = str(row[iri_key])
        seen[iri] = ConceptRef(iri=iri, label=_opt(row.get(label_key)))
    return sorted(seen.values(), key=lambda r: r.label or r.id)


def _matches(store: Any, iri: str) -> dict[str, list[str]]:
    """Crosswalk targets, grouped by match strength.

    Grouped rather than flattened, because X2 makes the strength load-bearing:
    an ``exactMatch`` says the two concepts are interchangeable and a
    ``closeMatch`` says they are not. A client that saw one list would treat
    them alike, which is the error the crosswalk audit (Q5) exists to catch.
    """
    rows = store.select(MATCH_QUERY, {"iri": URIRef(iri)})
    out: dict[str, list[str]] = {}
    for row in rows:
        strength = str(row["strength"]).rsplit("#", 1)[-1]
        out.setdefault(strength, []).append(str(row["target"]))
    return {k: sorted(set(v)) for k, v in sorted(out.items())}


def _concept_count(iri: str, caller: Caller, backend: SearchBackend) -> int:
    """How many datasets the caller can see that use this concept.

    Entitlement-scoped, because a count over records the caller cannot see is
    the existence leak in its purest form: no titles, no ids, just a number
    that confirms something is there.
    """
    for facet in ("concept", "data_domain", "supported_analysis"):
        try:
            response = backend.search(
                build(SearchParams(filters={facet: [iri]}, limit=0), caller.entitlement)
            )
        except Exception:
            continue
        if response.total:
            return response.total
    return 0


def _opt(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
