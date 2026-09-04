"""OpenSearch backend (PRD §3.3). Production path for the read side.

Semantics must match :class:`~datahub.api.search.backend.InMemorySearchBackend`
exactly; ``tests/search/test_backend_parity.py`` asserts that against the same
corpus. Where the two could plausibly diverge — missing-value sort order, an
absent bbox counting as a match, facet counts computed post-filter — the
behaviour is pinned here in a comment naming the parity test.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from datahub.api.search.backend import (
    FIELD_BOOSTS,
    BBoxFilter,
    Entitlement,
    FacetValue,
    Hit,
    SearchBackend,
    SearchRequest,
    SearchResponse,
)
from datahub.api.search.document import FACET_FIELDS, SORT_FIELDS, SearchDocument

#: Explicit mapping. Dynamic mapping is disabled so a stray field cannot become
#: searchable without passing through the document contract first.
INDEX_MAPPING: dict[str, Any] = {
    "settings": {
        "index": {"number_of_shards": 1, "number_of_replicas": 1},
        "analysis": {
            "analyzer": {
                "og_text": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding", "og_stem"],
                },
                "og_prefix": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding", "og_edge"],
                },
            },
            "filter": {
                "og_stem": {"type": "stemmer", "language": "light_english"},
                "og_edge": {"type": "edge_ngram", "min_gram": 2, "max_gram": 20},
            },
        },
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "id": {"type": "keyword"},
            "iri": {"type": "keyword"},
            "persistent_id": {"type": "keyword"},
            "doi": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "og_text",
                "fields": {
                    "raw": {"type": "keyword"},
                    "prefix": {
                        "type": "text",
                        "analyzer": "og_prefix",
                        "search_analyzer": "og_text",
                    },
                },
            },
            "description": {"type": "text", "analyzer": "og_text"},
            "summary": {"type": "text", "analyzer": "og_text"},
            "keywords": {
                "type": "text",
                "analyzer": "og_text",
                "fields": {"raw": {"type": "keyword"}},
            },
            "publisher": {
                "type": "text",
                "analyzer": "og_text",
                "fields": {"raw": {"type": "keyword"}},
            },
            "creators": {"type": "text", "analyzer": "og_text"},
            "data_domains": {
                "type": "object",
                "properties": {
                    "iri": {"type": "keyword"},
                    "label": {
                        "type": "text",
                        "analyzer": "og_text",
                        "fields": {"raw": {"type": "keyword"}},
                    },
                    "notation": {"type": "keyword"},
                },
            },
            "provenance_class": {"type": "keyword"},
            "supported_analysis": {
                "type": "object",
                "properties": {
                    "iri": {"type": "keyword"},
                    "label": {"type": "text", "analyzer": "og_text"},
                    "notation": {"type": "keyword"},
                },
            },
            "excluded_analysis": {
                "type": "object",
                "properties": {
                    "iri": {"type": "keyword"},
                    "label": {"type": "text", "analyzer": "og_text"},
                    "notation": {"type": "keyword"},
                },
            },
            "concepts": {
                "type": "object",
                "properties": {
                    "iri": {"type": "keyword"},
                    "label": {"type": "text", "analyzer": "og_text"},
                    "notation": {"type": "keyword"},
                },
            },
            "concept_iris_expanded": {"type": "keyword"},
            "license_id": {"type": "keyword"},
            "license_label": {"type": "text", "analyzer": "og_text"},
            "license_url": {"type": "keyword"},
            "redistribution_allowed": {"type": "boolean"},
            "access_restriction": {"type": "keyword"},
            "anonymous_access": {"type": "boolean"},
            "bulk_download": {"type": "boolean"},
            "formats": {"type": "keyword"},
            "distributions": {
                "type": "object",
                "properties": {
                    "id": {"type": "keyword"},
                    "media_type": {"type": "keyword"},
                    "format_label": {"type": "keyword"},
                    "byte_size": {"type": "long"},
                    "access_restriction": {"type": "keyword"},
                    "anonymous_access": {"type": "boolean"},
                    "bulk_download": {"type": "boolean"},
                    "supports_range_requests": {"type": "boolean"},
                    "subsetting_protocol": {"type": "keyword"},
                    "link_health": {"type": "keyword"},
                },
            },
            "distribution_count": {"type": "integer"},
            "has_range_requests": {"type": "boolean"},
            "subsetting_protocols": {"type": "keyword"},
            "worst_link_health": {"type": "keyword"},
            "all_distributions_unreachable": {"type": "boolean"},
            "spatial": {
                "type": "object",
                "properties": {
                    "bbox": {"type": "float"},
                    "envelope": {"type": "geo_shape"},
                    "place_labels": {
                        "type": "text",
                        "analyzer": "og_text",
                        "fields": {"raw": {"type": "keyword"}},
                    },
                    "place_iris": {"type": "keyword"},
                    "native_crs": {"type": "keyword"},
                    "geometry_types": {"type": "keyword"},
                    "granularity": {"type": "keyword"},
                    "feature_count": {"type": "long"},
                },
            },
            "temporal": {
                "type": "object",
                "properties": {
                    "start": {"type": "date"},
                    "end": {"type": "date"},
                    "update_cadence": {"type": "keyword"},
                    "time_resolution": {"type": "keyword"},
                },
            },
            "tier": {"type": "integer"},
            "reference_only": {"type": "boolean"},
            "completeness_level": {"type": "integer"},
            "review_state": {"type": "keyword"},
            "harvest_source": {"type": "keyword"},
            "documentation_status": {"type": "keyword"},
            "quality": {
                "type": "object",
                "properties": {
                    "provenance": {"type": "keyword"},
                    "documentation": {"type": "keyword"},
                    "currency": {"type": "keyword"},
                    "provenance_label": {"type": "keyword"},
                    "documentation_label": {"type": "keyword"},
                    "currency_label": {"type": "keyword"},
                },
            },
            "quality_assessed": {"type": "boolean"},
            "has_topology": {"type": "boolean"},
            "has_impedance": {"type": "boolean"},
            "voltage_classes": {"type": "keyword"},
            "field_count": {"type": "integer"},
            "upstream_count": {"type": "integer"},
            "inbound_link_count": {"type": "integer"},
            "superseded_by": {"type": "keyword"},
            "supersedes": {"type": "keyword"},
            "visibility": {"type": "keyword"},
            "entitled_principals": {"type": "keyword"},
            "custodian_id": {"type": "keyword"},
            "issued": {"type": "date"},
            "modified": {"type": "date"},
            "indexed_at": {"type": "date"},
            "last_computed_at": {"type": "object", "enabled": False},
        },
    },
}


def entitlement_clause(entitlement: Entitlement) -> dict[str, Any]:
    """The mandatory visibility filter (ADR-0006).

    Compiled into the query rather than applied to the result set, so a record
    the caller may not see contributes to no hit count, facet count or page
    total.
    """
    visible: list[dict[str, Any]] = [
        {"bool": {"must_not": {"term": {"visibility": "allowlisted-existence"}}}}
    ]
    if entitlement.is_steward:
        visible = [{"match_all": {}}]
    elif entitlement.principal_id:
        visible.append({"term": {"entitled_principals": entitlement.principal_id}})
        visible.append({"term": {"custodian_id": entitlement.principal_id}})
        if entitlement.custodian_of:
            visible.append({"terms": {"custodian_id": sorted(entitlement.custodian_of)}})
    clause: dict[str, Any] = {"bool": {"should": visible, "minimum_should_match": 1}}
    if entitlement.include_unconfirmed:
        return clause
    return {"bool": {"filter": [clause, {"term": {"review_state": "confirmed"}}]}}


def build_query(request: SearchRequest) -> dict[str, Any]:
    """Translate a :class:`SearchRequest` into an OpenSearch request body."""
    filters: list[dict[str, Any]] = [entitlement_clause(request.entitlement)]

    if request.ids is not None:
        filters.append({"terms": {"id": list(request.ids)}})

    for name, accepted in request.filters.items():
        filters.append({"terms": {_keyword_path(name): [_norm(v) for v in accepted]}})

    for name, rng in request.ranges.items():
        path = FACET_FIELDS.get(name) or SORT_FIELDS[name]
        body: dict[str, Any] = {}
        if rng.gte is not None:
            body["gte"] = rng.gte
        if rng.lte is not None:
            body["lte"] = rng.lte
        filters.append({"range": {path: body}})

    if request.bbox is not None:
        filters.append(_bbox_clause(request.bbox))

    if request.temporal is not None:
        # Overlap, not containment; and a record with no declared extent still
        # matches, matching the in-memory backend (parity: test_temporal_absent).
        overlap: list[dict[str, Any]] = []
        if request.temporal.gte is not None:
            overlap.append({"range": {"temporal.end": {"gte": request.temporal.gte}}})
        if request.temporal.lte is not None:
            overlap.append({"range": {"temporal.start": {"lte": request.temporal.lte}}})
        filters.append(
            {
                "bool": {
                    "should": [
                        {"bool": {"filter": overlap}},
                        {
                            "bool": {
                                "must_not": [
                                    {"exists": {"field": "temporal.start"}},
                                    {"exists": {"field": "temporal.end"}},
                                ]
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            }
        )

    if request.q and request.q.strip():
        fields = [f"{name}^{boost}" for name, boost in FIELD_BOOSTS.items()]
        should: list[dict[str, Any]] = [
            {
                "multi_match": {
                    "query": request.q,
                    "fields": fields,
                    "type": "best_fields",
                    "operator": "and",
                }
            }
        ]
        if request.prefix_last_token:
            should.append(
                {
                    "multi_match": {
                        "query": request.q,
                        "fields": ["title.prefix^2.1", "keywords^0.9"],
                        "type": "bool_prefix",
                        "boost": 0.35,
                    }
                }
            )
        query: dict[str, Any] = {
            "bool": {"filter": filters, "should": should, "minimum_should_match": 1}
        }
    else:
        query = {"bool": {"filter": filters}}

    body: dict[str, Any] = {
        "query": query,
        "from": request.offset,
        "size": request.limit,
        "track_total_hits": True,
    }

    if request.sort:
        body["sort"] = [
            {
                SORT_FIELDS[spec.field]: {
                    "order": "desc" if spec.descending else "asc",
                    "missing": "_last",  # parity: missing always last, both directions
                }
            }
            for spec in request.sort
        ]
    elif not request.q:
        body["sort"] = [{"title.raw": {"order": "asc", "missing": "_last"}}]

    if request.facets:
        body["aggs"] = {
            name: {"terms": {"field": _keyword_path(name), "size": 50}} for name in request.facets
        }
    return body


def _keyword_path(name: str) -> str:
    path = FACET_FIELDS[name]
    if path in {"formats", "voltage_classes", "subsetting_protocols"}:
        return path
    return path


def _norm(value: Any) -> Any:
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _bbox_clause(box: BBoxFilter) -> dict[str, Any]:
    # A record without a declared envelope still matches (parity: test_bbox_absent).
    return {
        "bool": {
            "should": [
                {
                    "geo_shape": {
                        "spatial.envelope": {
                            "shape": {
                                "type": "envelope",
                                "coordinates": [
                                    [box.min_lon, box.max_lat],
                                    [box.max_lon, box.min_lat],
                                ],
                            },
                            "relation": "intersects",
                        }
                    }
                },
                {"bool": {"must_not": {"exists": {"field": "spatial.envelope"}}}},
            ],
            "minimum_should_match": 1,
        }
    }


def to_source(doc: SearchDocument) -> dict[str, Any]:
    """Serialise a document, adding the geo_shape envelope OpenSearch needs."""
    source = doc.model_dump(mode="json")
    bbox = doc.spatial.bbox
    if bbox and len(bbox) == 4:
        min_lon, min_lat, max_lon, max_lat = bbox
        source["spatial"]["envelope"] = {
            "type": "envelope",
            "coordinates": [[min_lon, max_lat], [max_lon, min_lat]],
        }
    return source


class OpenSearchBackend(SearchBackend):
    def __init__(
        self,
        url: str,
        index_name: str,
        *,
        auth: tuple[str, str] | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            from opensearchpy import OpenSearch  # imported lazily: optional extra

            client = OpenSearch(hosts=[url], http_auth=auth, timeout=30)
        self.client = client
        self.index_name = index_name

    def ensure_index(self) -> None:
        if not self.client.indices.exists(index=self.index_name):
            self.client.indices.create(index=self.index_name, body=INDEX_MAPPING)

    def index(self, documents: Iterable[SearchDocument]) -> int:
        from opensearchpy.helpers import bulk

        actions = [
            {"_index": self.index_name, "_id": doc.id, "_source": to_source(doc)}
            for doc in documents
        ]
        if not actions:
            return 0
        success, _ = bulk(self.client, actions, refresh=False)
        return int(success)

    def delete(self, ids: Iterable[str]) -> int:
        removed = 0
        for doc_id in ids:
            response = self.client.delete(index=self.index_name, id=doc_id, ignore=[404])
            if response.get("result") == "deleted":
                removed += 1
        return removed

    def get(self, doc_id: str) -> SearchDocument | None:
        response = self.client.get(index=self.index_name, id=doc_id, ignore=[404])
        if not response.get("found"):
            return None
        source = dict(response["_source"])
        source.get("spatial", {}).pop("envelope", None)
        return SearchDocument.model_validate(source)

    def search(self, request: SearchRequest) -> SearchResponse:
        started = time.perf_counter()
        body = build_query(request)
        response = self.client.search(index=self.index_name, body=body)
        hits: list[Hit] = []
        for raw in response["hits"]["hits"]:
            source = dict(raw["_source"])
            source.get("spatial", {}).pop("envelope", None)
            doc = SearchDocument.model_validate(source)
            hits.append(
                Hit(
                    document=doc,
                    score=float(raw.get("_score") or 0.0),
                    full_metadata=request.entitlement.can_see_full_metadata(doc),
                )
            )
        facets: dict[str, list[FacetValue]] = {}
        for name, agg in (response.get("aggregations") or {}).items():
            facets[name] = [
                FacetValue(bucket["key"], bucket["doc_count"]) for bucket in agg.get("buckets", [])
            ]
        return SearchResponse(
            total=int(response["hits"]["total"]["value"]),
            hits=hits,
            facets=facets,
            took_ms=(time.perf_counter() - started) * 1000,
        )

    def count(self) -> int:
        return int(self.client.count(index=self.index_name)["count"])

    def clear(self) -> None:
        self.client.indices.delete(index=self.index_name, ignore=[404])
        self.ensure_index()

    def refresh(self) -> None:
        self.client.indices.refresh(index=self.index_name)
