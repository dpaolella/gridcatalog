"""Computing links for the catalog and writing them back (WP-8.1, WP-8.2).

PRD §F6.1: *retrieve candidate signals from the semantic layer. Computes no raw
signals itself.* So this module is candidate generation, ranking and
persistence; the signals come from :mod:`datahub.linksvc.signals` and the
lineage from :mod:`datahub.semantic.provenance`.

**Candidates come from the index, not from a cross join.** Every pair of a
5,000-record catalog is 12.5 million comparisons per pass. The index already
knows which records share a concept or a domain, and a record with nothing in
common with another is not a candidate — so candidate generation is a search
and the quadratic part never happens. The cap in config bounds it further.

Output goes to ``og:graph/computed``, like every other derived thing, and the
links are directional: `A → B` and `B → A` are stored separately because their
top-N lists differ. A tiny dataset may have a large one as its strongest
suggestion without being anywhere near the large one's top twelve.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from datahub.api.search.backend import Entitlement, SearchBackend, SearchRequest
from datahub.api.search.document import SearchDocument
from datahub.graph.graphs import NamedGraph
from datahub.graph.store import GraphStore

# Imported from the modules rather than the package: ``datahub.linksvc``
# re-exports a *function* named ``describe`` and a module named ``describe``,
# and `from datahub.linksvc import describe` binds whichever the package
# happens to define last.
from datahub.linksvc.describe import describe as describe_pair
from datahub.linksvc.rank import Link, rank, score, worth_surfacing
from datahub.linksvc.signals import PairSignals
from datahub.linksvc.signals import compute as compute_signals
from datahub.linksvc.weights import Weights, cached
from datahub.logging import get_logger
from datahub.namespaces import OG
from datahub.semantic.provenance import LineageIndex
from datahub.semantic.vocabulary import Vocabulary
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD

log = get_logger(__name__)


@dataclass
class LinkPass:
    """One run over some or all of the catalog."""

    records: int = 0
    links: int = 0
    warned: int = 0
    dropped_without_reason: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "records": self.records,
            "links": self.links,
            "warned": self.warned,
            "dropped_without_reason": self.dropped_without_reason,
            "seconds": (
                round((self.finished_at - self.started_at).total_seconds(), 2)
                if self.started_at and self.finished_at
                else None
            ),
        }


@dataclass
class LinkService:
    """Computes and stores inter-dataset links."""

    backend: SearchBackend
    store: GraphStore | None = None
    vocabulary: Vocabulary | None = None
    lineage: LineageIndex | None = None
    weights: Weights = field(default_factory=cached)

    def __post_init__(self) -> None:
        if self.store is not None:
            if self.vocabulary is None:
                self.vocabulary = Vocabulary.from_store(self.store)
            if self.lineage is None:
                self.lineage = LineageIndex.from_store(self.store)

    # -- one record --------------------------------------------------------

    def links_for(self, dataset_id: str, *, entitlement: Entitlement | None = None) -> list[Link]:
        """The top-N links for one record, ranked and explained.

        The entitlement is threaded through candidate generation rather than
        applied to its results (ADR-0006). A link *to* a record the caller may
        not see would leak its existence through a suggestion list, which is
        the same leak the entitlement matrix hunts for on `/datasets`.
        """
        source = self.backend.get(dataset_id)
        if source is None:
            return []

        entitlement = entitlement or Entitlement.anonymous()
        pairs: dict[str, PairSignals] = {}
        links: list[Link] = []

        for target in self._candidates(source, entitlement):
            pair = compute_signals(source, target, lineage=self.lineage, vocabulary=self.vocabulary)
            description = describe_pair(
                source,
                target,
                pair,
                derives_from=self.derives(source.iri, target.iri),
                derived_by=self.derives(target.iri, source.iri),
            )
            link = score(pair, self.weights, description)
            if not worth_surfacing(link):
                continue
            pairs[link.target] = pair
            links.append(link)

        return rank(links, pairs, self.weights)

    # -- candidate generation ---------------------------------------------

    def _candidates(
        self, source: SearchDocument, entitlement: Entitlement
    ) -> Iterator[SearchDocument]:
        """Records that could plausibly link to *source*.

        Three cheap queries — shared concept, shared domain, shared supported
        analysis — rather than one expensive scan. A record sharing none of
        those is not a candidate at any weighting, so scoring it would be work
        whose answer is known.
        """
        # Keyed by slug, and the lineage pass below resolves IRIs to documents
        # before checking. Mixing the two — an IRI tested against a set of
        # slugs — never matches, and the same record is yielded twice: once as
        # a similarity candidate and once as a lineage neighbour.
        seen: set[str] = {source.id}
        budget = self.weights.max_candidates_per_dataset

        for field_name, values in (
            ("concept", [c.iri for c in source.concepts]),
            ("data_domain", [c.iri for c in source.data_domains]),
            ("supported_analysis", [c.iri for c in source.supported_analysis]),
        ):
            if not values or len(seen) > budget:
                continue
            response = self.backend.search(
                SearchRequest(
                    entitlement=entitlement,
                    filters={field_name: values},
                    limit=budget,
                )
            )
            for hit in response.hits:
                # `full_metadata` False means the caller may see that the
                # record exists but not its detail. Linking to it would publish
                # its title and coverage in a descriptor, which is the detail.
                if hit.document.id in seen or not hit.full_metadata:
                    continue
                seen.add(hit.document.id)
                yield hit.document

        # Lineage neighbours, whatever else they share. Two datasets from the
        # same origin may have no concept in common — a wind atlas and a
        # weather cutout describe different quantities — and those are exactly
        # the pairs the correlation warning exists for. Generating candidates
        # only from similarity would systematically miss them.
        for related in self._lineage_neighbours(source.iri):
            document = self.backend.get(related.rsplit("/", 1)[-1])
            if document is None or document.id in seen:
                continue
            if not entitlement.can_see_full_metadata(document):
                continue
            seen.add(document.id)
            yield document

    def _lineage_neighbours(self, iri: str) -> Iterable[str]:
        if self.lineage is None:
            return ()
        mine = set(self.lineage.ancestors(iri)) | {iri}
        return {
            other
            for other in self.lineage.parents
            if other != iri and (set(self.lineage.ancestors(other)) | {other}) & mine
        }

    def derives(self, child: str, ancestor: str) -> bool:
        return self.lineage is not None and self.lineage.derives_from(child, ancestor)

    # -- batches -----------------------------------------------------------

    #: The batch pass runs with full visibility. It is a system pass, not a
    #: user query: a restricted record that got no links because the batch
    #: could not see it would have none to show its own custodian either, which
    #: is a leak in the other direction — the entitled user losing information
    #: rather than the unentitled gaining it. Entitlement is applied where a
    #: caller reads, in ``links_for``, and it is compiled into candidate
    #: generation there rather than filtered afterwards (ADR-0006).
    SYSTEM = Entitlement(is_steward=True, include_unconfirmed=False)

    def run_all(self, *, limit: int | None = None, write: bool = True) -> LinkPass:
        summary = LinkPass(started_at=datetime.now(UTC))
        for count, document in enumerate(self._all_documents()):
            if limit is not None and count >= limit:
                break
            links = self.links_for(document.id, entitlement=self.SYSTEM)
            summary.records += 1
            summary.links += len(links)
            summary.warned += sum(1 for link in links if link.warning)
            if write and self.store is not None:
                self.write_links(document.iri, links)
        summary.finished_at = datetime.now(UTC)
        log.info("link pass complete", **summary.as_dict())
        return summary

    def _all_documents(self) -> Iterator[SearchDocument]:
        offset = 0
        while True:
            response = self.backend.search(
                SearchRequest(entitlement=self.SYSTEM, limit=100, offset=offset)
            )
            if not response.hits:
                return
            for hit in response.hits:
                yield hit.document
            offset += 100

    # -- persistence -------------------------------------------------------

    def write_links(self, source_iri: str, links: list[Link]) -> None:
        """Replace this record's outbound links in the computed graph."""
        if self.store is None:
            return
        computed = self.store.get_graph(NamedGraph.COMPUTED)
        subject = URIRef(source_iri)
        # Delete by node rather than by predicate: a link node carries a
        # warning node of its own, and removing only the `og:hasLink` edge
        # would orphan both in a graph that is then dropped-and-rebuilt to no
        # effect, because nothing points at them any more.
        for node in list(computed.objects(subject, OG.hasLink)):
            for warning in list(computed.objects(node, OG.sharedOriginWarning)):
                for triple in list(computed.triples((warning, None, None))):
                    computed.remove(triple)
            for triple in list(computed.triples((node, None, None))):
                computed.remove(triple)
            computed.remove((subject, OG.hasLink, node))

        graph = Graph()
        for index, link in enumerate(links):
            node = URIRef(f"{source_iri}#link-{index:02d}")
            target = URIRef(_iri_for(source_iri, link.target))
            graph.add((subject, OG.hasLink, node))
            graph.add((node, RDF.type, OG.DatasetLink))
            graph.add((node, OG.linkFrom, subject))
            graph.add((node, OG.linkTo, target))
            graph.add((node, OG.linkType, Literal(link.relation)))
            graph.add((node, OG.linkStrength, Literal(link.tier)))
            graph.add((node, OG.linkScore, Literal(link.score, datatype=XSD.double)))
            graph.add((node, OG.complementarityDescriptor, Literal(link.descriptor, lang="en")))
            for reason in link.reasons:
                graph.add((node, OG.linkReason, Literal(reason, lang="en")))
            for key in link.joinable_keys:
                graph.add((node, OG.joinableKey, URIRef(key)))
            for tag in link.shared_workflow_tags:
                graph.add((node, OG.sharedWorkflowTag, URIRef(tag)))
            if link.warning and link.shared_origin:
                warning = URIRef(f"{node}-warning")
                graph.add((node, OG.sharedOriginWarning, warning))
                graph.add((warning, RDF.type, OG.SharedOriginWarning))
                graph.add((warning, OG.sharedOrigin, URIRef(link.shared_origin)))
                graph.add((warning, OG.modelingConsequence, Literal(link.warning, lang="en")))
        self.store.add_graph(NamedGraph.COMPUTED, graph)


def _iri_for(source_iri: str, dataset_id: str) -> str:
    if dataset_id.startswith("http"):
        return dataset_id
    return source_iri.rsplit("/", 1)[0] + "/" + dataset_id


__all__ = ["LinkPass", "LinkService"]
