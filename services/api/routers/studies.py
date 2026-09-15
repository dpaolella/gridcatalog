"""``/v1/studies`` — a filing, what it assumed, and what it ran on (#82).

The registry had four kinds of object in it and the API served one. A study,
its assumption set and its run records were writable, validated and
unreachable: `/v1/datasets` would list a study once the projector learned to
project one, and there was no endpoint anywhere that would tell you what the
study actually assumed. The most useful thing the catalog knows about a filing
— that the 0.098 in it is an *estimate*, drawn from a named exhibit, landing on
`Investments/Financials/TechnologyFinancialData#return_on_equity` — reached
nobody.

**Read out of the graph, not the index.** The index carries a study's axes
(`study_kind`, `docket`, `jurisdiction`, `analysis_types`, `citation_id`,
`parent_study`, `frozen_at`) because that is what a list view needs to group
filings into threads and to facet them. It does not carry
the assumptions, and it should not: a set of a few hundred parameter values
flattened into a search document would be a search document mostly made of
numbers nobody searches for. This is the endpoint where somebody is asking for
the values themselves.

**Entitlement still runs through the index.** Existence and visibility are
resolved by :func:`entitled_document` exactly as they are for a dataset
(ADR-0006), and only then is the graph read. A study the caller may not see is
absent here in the same way it is absent there, and the studies listed against
a dataset are filtered the same way one at a time — a citation is not a
back door to a record's existence.
"""

from __future__ import annotations

from typing import Annotated, Any

from datahub.api.deps import CallerDep, RecordsDep, SearchDep
from datahub.api.entitlement.visibility import absent, entitled_document
from datahub.api.schemas import (
    AssumptionDetail,
    AssumptionSetDetail,
    RunRecordDetail,
    StudyDetail,
    StudyUsageResponse,
    StudyUse,
)
from datahub.api.vocabulary import labels
from datahub.graph.graphs import NamedGraph
from datahub.graph.records import slug_of
from datahub.graph.sparql import values_clause
from datahub.logging import get_logger
from fastapi import APIRouter, Path
from rdflib import URIRef

log = get_logger(__name__)

router = APIRouter(tags=["studies"])

StudyId = Annotated[
    str,
    Path(
        description=(
            "The study's slug, which is the last segment of its IRI — "
            "`cascade-pl-irp-2026` for "
            "`https://catalog.opengrid.org/study/cascade-pl-irp-2026`."
        ),
        examples=["cascade-pl-irp-2026"],
    ),
]


@router.get(
    "/studies/{study_id}",
    response_model=StudyDetail,
    summary="One study, with its assumptions and runs",
)
def get_study(
    study_id: StudyId,
    caller: CallerDep,
    backend: SearchDep,
    records: RecordsDep,
) -> StudyDetail:
    """A study, the parameter values behind it, and the runs that produced it.

    The assumption sets arrive resolved rather than as IRIs. A caller handed
    `hasAssumptionSet: [...]` and left to fetch each one has to know that an
    assumption set is a record, which URL serves it, and that the assumptions
    inside it are nested — three facts about this catalog's internals standing
    between them and a table of numbers.
    """
    document, full = entitled_document(study_id, caller, backend)
    if not full or document.record_type != "study":
        # Not a study, or a stub. Either way there is no study here to serve,
        # and saying which would distinguish "withheld" from "not that kind".
        raise absent(study_id)

    record = _node(records, document.iri)
    set_iris = _iris(record.get("hasAssumptionSet"))
    sets = [detail for iri in set_iris if (detail := _assumption_set(records, iri)) is not None]
    runs = [
        detail
        for iri in _run_iris(records, record, set_iris)
        if (detail := _run_record(records, iri)) is not None
    ]

    bound = _iris(record.get("boundReferenceModel"))
    parent = _one(record.get("parentStudy"))
    held = _held(records, [*bound, *([parent] if parent else [])])

    return StudyDetail(
        id=document.id,
        iri=document.iri,
        title=document.title,
        summary=document.summary,
        description=document.description,
        study_kind=document.study_kind,
        publisher=document.publisher,
        jurisdiction=document.jurisdiction,
        docket=document.docket,
        version=_str(record.get("version")),
        citation_id=document.citation_id,
        frozen_at=document.frozen_at,
        issued=document.issued,
        review_state=document.review_state,
        curation_basis=document.curation_basis,
        demonstration=document.demonstration,
        analysis_types=document.analysis_types,
        parent_study=parent,
        parent_study_id=slug_of(parent) if parent and parent in held else None,
        bound_reference_models=bound,
        bound_reference_model_ids=[slug_of(iri) for iri in bound if iri in held],
        assumption_sets=sets,
        run_records=runs,
    )


@router.get(
    "/datasets/{dataset_id}/studies",
    response_model=StudyUsageResponse,
    summary="Registered studies standing on this record",
)
def studies_using(
    dataset_id: str,
    caller: CallerDep,
    backend: SearchDep,
    records: RecordsDep,
) -> StudyUsageResponse:
    """Which registered studies use this record, and how.

    Not the same claim as `usage_evidence`. That is a citation a harvest found
    in a source's own metadata: a string, unverifiable, and absent for most
    records because most sources have no field that could carry one. Every
    entry here is an object in this catalog with an assumption set behind it,
    so the count is something a reader can go and check rather than a number
    the catalog is asking to be trusted on.

    Two ways to stand on a record, kept apart because they are different
    claims. Binding it as the network a study ran on says the study's results
    are about this topology. Citing it as the source of an assumption value
    says one number in the study came from here — which may matter more, and is
    the relationship that was previously invisible at every layer.
    """
    document, _ = entitled_document(dataset_id, caller, backend)

    rows = records.store.select(
        """
        SELECT DISTINCT ?study ?role ?path WHERE {
          GRAPH ??g {
            ?study a og:Study .
            {
              ?study og:boundReferenceModel ??d .
              BIND("reference-model" AS ?role)
            } UNION {
              ?study og:hasRunRecord ?run .
              ?run og:onReferenceModel ??d .
              BIND("reference-model" AS ?role)
            } UNION {
              # The same arrow read from the run's end. A study carries
              # og:frozenAt, so a run registered after the filing cannot be
              # added by editing the study — the run names the set it ran, and
              # that is the link that still points the right way afterwards.
              ?study og:hasAssumptionSet ?ranSet .
              ?laterRun og:ranAssumptionSet ?ranSet ; og:onReferenceModel ??d .
              BIND("reference-model" AS ?role)
            } UNION {
              ?study og:hasAssumptionSet ?set .
              ?set og:hasAssumption ?assumption .
              ?assumption og:fieldSource ??d .
              OPTIONAL { ?assumption og:assumptionPath ?path }
              BIND("assumption-source" AS ?role)
            }
          }
        }
        """,
        {"g": NamedGraph.CATALOG.uri(), "d": URIRef(document.iri)},
    )

    roles: dict[str, set[str]] = {}
    paths: dict[str, set[str]] = {}
    for row in rows:
        iri = str(row["study"])
        roles.setdefault(iri, set()).add(str(row["role"]))
        if row.get("path") is not None:
            paths.setdefault(iri, set()).add(str(row["path"]))

    uses: list[StudyUse] = []
    for iri in sorted(roles):
        try:
            doc, full = entitled_document(slug_of(iri), caller, backend)
        except Exception:
            # A study the caller may not see contributes to no count. The
            # filter is the same one the list endpoint applies; applying it
            # here too is what keeps a citation from working as an oracle.
            continue
        if not full:
            continue
        uses.append(
            StudyUse(
                id=doc.id,
                iri=doc.iri,
                title=doc.title,
                study_kind=doc.study_kind,
                docket=doc.docket,
                jurisdiction=doc.jurisdiction,
                publisher=doc.publisher,
                frozen_at=doc.frozen_at,
                roles=sorted(roles[iri]),
                assumption_paths=sorted(paths.get(iri, ())),
            )
        )

    return StudyUsageResponse(dataset_id=document.id, total=len(uses), studies=uses)


# ---------------------------------------------------------------------------
# Reading the graph
# ---------------------------------------------------------------------------


def _node(records: RecordsDep, iri: str) -> dict[str, Any]:
    """The record's own node, framed, or an empty node if it cannot be read.

    Never fatal. A study whose subgraph will not read is still a study the
    index knows about, and refusing the whole response for it would turn one
    unreadable record into a broken page.
    """
    from datahub.graph.records import record_node

    try:
        return record_node(records.get(iri))
    except Exception as exc:  # pragma: no cover - a record the index just named
        log.warning("could not read record", iri=iri, error=str(exc))
        return {}


def _run_iris(records: RecordsDep, record: dict[str, Any], sets: list[str]) -> list[str]:
    """Every registered run of this study, from both directions.

    The study names its runs, and that is the primary answer. It cannot be the
    only one: a study carries `og:frozenAt`, which says its inputs stopped
    moving — so a run registered after the filing was frozen cannot be added by
    editing the study without editing a record that is supposed to be fixed.
    The run itself names the assumption set it ran, which is the arrow that
    still points the right way afterwards.

    The coalition intervention in the fixtures is exactly that case: R-0537
    exists, names the fork it ran, and the study record does not list it. Read
    from one side only, the single most interesting number in the registry —
    $18.068bn against the filing's $18.42bn, one assumption apart — was
    registered, validated and shown to nobody.
    """
    declared = _iris(record.get("hasRunRecord"))
    if not sets:
        return declared

    rows = records.store.select(
        f"""
        SELECT DISTINCT ?run WHERE {{
          GRAPH ??g {{
            ?run a og:RunRecord ; og:ranAssumptionSet ?set .
          }}
          {values_clause("set", [URIRef(i) for i in sorted(sets)])}
        }}
        """,
        {"g": NamedGraph.CATALOG.uri()},
    )
    found = [str(row["run"]) for row in rows]
    # Declared first, then anything only the run itself claims, each once and
    # in a stable order — the store returns rows in no stated order and a page
    # whose runs shuffle between builds is a diff nobody can read.
    seen = set(declared)
    return [*declared, *sorted(iri for iri in found if iri not in seen)]


def _assumption_set(records: RecordsDep, iri: str) -> AssumptionSetDetail | None:
    node = _node(records, iri)
    if not node:
        return None
    assumptions = node.get("hasAssumption") or []
    if isinstance(assumptions, dict):
        assumptions = [assumptions]

    units = {
        unit
        for entry in assumptions
        if isinstance(entry, dict) and isinstance(unit := entry.get("unit"), str)
    }
    unit_labels = labels(records, sorted(units))

    cited = {
        source
        for entry in assumptions
        if isinstance(entry, dict)
        for source in _iris(entry.get("fieldSource"))
    }
    held = _held(records, sorted(cited))

    forked = _one(node.get("forkedFrom"))
    forked_held = _held(records, [forked]) if forked else set()

    rows = [
        AssumptionDetail(
            id=str(entry.get("id") or ""),
            path=_str(entry.get("assumptionPath")),
            value=_str(entry.get("assumptionValue")),
            unit=_str(entry.get("unit")),
            unit_label=unit_labels.get(str(entry.get("unit"))),
            value_basis=_str(entry.get("valueBasis")),
            field_sources=(sources := _iris(entry.get("fieldSource"))),
            field_source_ids=[slug_of(s) for s in sources if s in held],
            inherited_from=_one(entry.get("inheritedFrom")),
            justification=_str(entry.get("justification")),
        )
        for entry in assumptions
        if isinstance(entry, dict)
    ]
    # By field path, because the path is the address and a reader comparing two
    # sets is comparing them row by row. The store returns nodes in no stated
    # order, so an unsorted table would put the same two sets in different
    # orders and make a diff impossible to read by eye.
    rows.sort(key=lambda a: (a.path or "", a.id))

    return AssumptionSetDetail(
        id=slug_of(iri),
        iri=iri,
        title=_str(node.get("title")),
        description=_str(node.get("description")),
        schema_pin=_str(node.get("schemaPin")),
        forked_from=forked,
        forked_from_id=slug_of(forked) if forked and forked in forked_held else None,
        receipt_errors=_int(node.get("receiptErrors")),
        receipt_warnings=_int(node.get("receiptWarnings")),
        assumptions=rows,
    )


def _run_record(records: RecordsDep, iri: str) -> RunRecordDetail | None:
    node = _node(records, iri)
    if not node:
        return None
    unit = _str(node.get("objectiveUnit"))
    unit_labels = labels(records, [unit]) if unit else {}
    model = _one(node.get("onReferenceModel"))
    held = _held(records, [model]) if model else set()
    return RunRecordDetail(
        id=slug_of(iri),
        iri=iri,
        tool=_str(node.get("tool")),
        tool_version=_str(node.get("toolVersion")),
        solver=_str(node.get("solver")),
        executed_by=_str(node.get("executedBy")),
        executed_at=_str(node.get("executedAt")),
        case_hash=_str(node.get("caseHash")),
        wall_time_seconds=_float(node.get("wallTimeSeconds")),
        objective_value=_float(node.get("objectiveValue")),
        objective_unit=unit,
        objective_unit_label=unit_labels.get(unit or ""),
        ran_assumption_set=_one(node.get("ranAssumptionSet")),
        on_reference_model=model,
        on_reference_model_id=slug_of(model) if model and model in held else None,
    )


def _held(records: RecordsDep, iris: list[str | None] | list[str] | None) -> set[str]:
    """Which of these IRIs the catalog holds a record for.

    The published graph only: a draft record is not somewhere a public page may
    send a reader. See `RecordStore.held` for why "is there a record" and "does
    this IRI appear in the graph" are different questions.
    """
    return records.held([iri for iri in (iris or []) if iri])


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------
#
# Framed JSON-LD gives a term one value or a list of them depending on what the
# context declares and what the record happened to carry, and a reader that
# assumes either shape is wrong on some record. These say which is expected.


def _iris(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out = [
        str(item.get("id")) if isinstance(item, dict) and item.get("id") else str(item)
        for item in items
        if item is not None
    ]
    return [iri for iri in out if iri and iri != "None"]


def _one(value: Any) -> str | None:
    found = _iris(value)
    return found[0] if found else None


def _str(value: Any) -> str | None:
    if value is None or isinstance(value, dict | list):
        return None
    return str(value)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
