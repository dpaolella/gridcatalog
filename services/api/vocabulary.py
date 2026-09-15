"""Display labels for the terms a record names.

One query for all of them and one ranking, because there were two.
`routers/datasets.py` resolved a field's unit and `routers/studies.py` resolved
an assumption's, against the same graph, with the same three predicates — and
the ranking that decides between them lived in one of the two files. A second
copy of that ranking is a table that shows "kV" in one row and "kilovolt" in the
next depending on which endpoint the row came through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datahub.graph.graphs import NamedGraph
from datahub.graph.sparql import values_clause
from rdflib import URIRef

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from datahub.graph.records import RecordStore

#: Which label to show when a term carries several, best first.
#:
#: The symbol wins where there is one, and only units have one: "kV" is what
#: belongs beside a number in a table, and "kilovolt" is what belongs in prose.
#: Concepts have no symbol, so for them this is prefLabel over rdfs:label — the
#: SKOS-preferred name over an incidental one.
LABEL_RANK = {
    "http://qudt.org/schema/qudt/symbol": 0,
    "http://www.w3.org/2004/02/skos/core#prefLabel": 1,
    "http://www.w3.org/2000/01/rdf-schema#label": 2,
}


def terms(records: RecordStore, iris: list[str]) -> dict[str, dict[str, str]]:
    """Label and definition for each IRI that resolves in the vocabulary.

    One query rather than one per term: a record with ninety fields would
    otherwise be ninety round trips to render one page, and the labels all live
    in the same graph.

    The definition comes back with the label because PRD §F4.2 asks that a
    plain-language definition sit beside every resolved concept — so that a
    field documented only through CIM or CGMES is intelligible to somebody who
    does not own the standard. A caller that only wants the label ignores it.

    Ranked rather than last-write-wins. Three predicates match and the store
    returns them in no stated order, so a unit rendered as its symbol or its
    name depending on which row arrived last: the GB model's schema showed
    "kilovolt", "Ω" and "MVA" in the same column, from three registry entries
    written identically. Same record, same query, three conventions.
    """
    wanted = sorted({iri for iri in iris if iri})
    if not wanted:
        return {}

    rows = records.store.select(
        f"""
        SELECT ?iri ?p ?label ?definition WHERE {{
          GRAPH ??vocab {{
            ?iri ?p ?label .
            OPTIONAL {{ ?iri skos:definition ?definition }}
          }}
          {values_clause("iri", [URIRef(i) for i in wanted])}
          VALUES ?p {{ skos:prefLabel rdfs:label qudt:symbol }}
        }}
        """,
        {"vocab": NamedGraph.VOCAB.uri()},
    )

    found: dict[str, dict[str, str]] = {}
    ranked: dict[str, int] = {}
    for row in rows:
        iri = str(row["iri"])
        entry = found.setdefault(iri, {})
        rank = LABEL_RANK.get(str(row["p"]), len(LABEL_RANK))
        if "label" not in entry or rank < ranked.get(iri, len(LABEL_RANK)):
            entry["label"] = str(row["label"])
            ranked[iri] = rank
        if row.get("definition") is not None:
            entry["definition"] = str(row["definition"])
    return found


def labels(records: RecordStore, iris: list[str]) -> dict[str, str]:
    """Just the label, for the callers that render a table cell."""
    return {iri: entry["label"] for iri, entry in terms(records, iris).items() if "label" in entry}
