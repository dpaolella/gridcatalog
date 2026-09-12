# API reference

**Generated from the OpenAPI document — do not edit by hand.** Regenerate with:

```bash
datahub openapi --markdown docs/api.md
```

The machine-readable document is at `/openapi.json`, and it is the canonical
contract: the web UI, the Python SDK and the MCP server all call this API and
none of them reaches past it into the store. A rule enforced here is enforced
for all three.

Two properties worth knowing before reading the endpoint list:

- **Custody decides whether this API returns bytes.** For a dataset hosted
  elsewhere it does not: `/download` is a redirect and `/access-plan` returns a
  document. For an object registered with the Hub it does, because a citable
  release has to still resolve when somebody follows the citation.
- **A 404 for a record you may not see is byte-identical to a 404 for a record
  that does not exist.** That is deliberate: a distinguishable refusal is an
  existence oracle.


API version **1.0.0**, OpenAPI 3.1.0.

## Endpoints

### allowlists

Who may see a restricted dataset. Managed by its custodian; OpenGrid stores and enforces the list and never arbitrates its contents.

| Method | Path | Summary |
|---|---|---|
| `GET` | `/v1/allowlists/{dataset_id}` | Who may see this dataset. Custodian only. |
| `PUT` | `/v1/allowlists/{dataset_id}` | Replace the allow-list. Custodian only. |

### auth

Signing in, and the tokens that stand in for it.

| Method | Path | Summary |
|---|---|---|
| `GET` | `/v1/auth/callback` | Finish a federated sign-in |
| `GET` | `/v1/auth/login/{provider}` | Begin a federated sign-in |
| `POST` | `/v1/auth/logout` | End this session |
| `POST` | `/v1/auth/logout-everywhere` | End every session for this user |
| `GET` | `/v1/auth/me` | The caller, as the API sees them |
| `GET` | `/v1/auth/providers` | Enabled sign-in providers |
| `GET` | `/v1/auth/tokens` | Your tokens |
| `POST` | `/v1/auth/tokens` | Issue a token. The only time you will see it. |
| `DELETE` | `/v1/auth/tokens/{token_id}` | Revoke a token, permanently |

### concepts

The SKOS vocabulary and the ten data domains.

| Method | Path | Summary |
|---|---|---|
| `GET` | `/v1/concepts` | The SKOS schemes |
| `GET` | `/v1/concepts/{concept_id}` | One concept, with its hierarchy, crosswalks and dataset count |
| `GET` | `/v1/domains` | DD1-DD10 |

### datasets

Search and read catalog records.

| Method | Path | Summary |
|---|---|---|
| `GET` | `/v1/datasets` | Search, filter, facet and paginate the catalog |
| `GET` | `/v1/datasets/{dataset_id}` | One record |
| `POST` | `/v1/datasets/{dataset_id}/access-plan` | How to read this dataset. Never the bytes themselves. |
| `GET` | `/v1/datasets/{dataset_id}/distributions` | Access paths, with capabilities and link health |
| `GET` | `/v1/datasets/{dataset_id}/download` | Redirect to the source. The API never serves bytes. |
| `GET` | `/v1/datasets/{dataset_id}/links` | Datasets that go with this one, and why |
| `GET` | `/v1/datasets/{dataset_id}/quality` | The three quality facets |
| `GET` | `/v1/datasets/{dataset_id}/schema` | Field-level metadata |

### gaps

| Method | Path | Summary |
|---|---|---|
| `GET` | `/v1/gaps` | Data requirements with no known open supplier |

### intake

Submit a dataset, or report a problem with one.

| Method | Path | Summary |
|---|---|---|
| `POST` | `/v1/reports` | Report a problem with a record, a field or a link |
| `POST` | `/v1/submissions` | Tell us about a dataset we do not have |

### review

The steward queue. Highest-leverage records first: most inbound links, then most complete.

| Method | Path | Summary |
|---|---|---|
| `GET` | `/v1/review` | Records awaiting review |
| `POST` | `/v1/review/{dataset_id}/confirm` | Confirm a record, and the fields you checked |

### service

Health and readiness.

| Method | Path | Summary |
|---|---|---|
| `GET` | `/v1/health` | Liveness |
| `GET` | `/v1/health/ready` | Readiness |
| `GET` | `/v1/health/status` | Data state |

## Parameters

### `GET /v1/allowlists/{dataset_id}`

| Name | In | Required | Description |
|---|---|---|---|
| `dataset_id` | path | yes | The dataset's slug. |
| `authorization` | header | no |  |

### `PUT /v1/allowlists/{dataset_id}`

Replace the whole list.

| Name | In | Required | Description |
|---|---|---|---|
| `dataset_id` | path | yes | The dataset's slug. |
| `authorization` | header | no |  |

### `GET /v1/auth/callback`

| Name | In | Required | Description |
|---|---|---|---|
| `state` | query | yes |  |
| `code` | query | yes |  |
| `authorization` | header | no |  |

### `GET /v1/auth/login/{provider}`

Authorization Code with PKCE.

| Name | In | Required | Description |
|---|---|---|---|
| `provider` | path | yes |  |
| `next` | query | no |  |
| `authorization` | header | no |  |

### `POST /v1/auth/logout`

Revoke server-side and clear the cookie.

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

### `POST /v1/auth/logout-everywhere`

What a person clicks after losing a laptop.

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

### `GET /v1/auth/me`

Answers honestly for an anonymous caller rather than returning 401.

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

### `GET /v1/auth/providers`

What a sign-in page should offer.

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

### `GET /v1/auth/tokens`

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

### `POST /v1/auth/tokens`

Issue a personal access token, within the caller's own permissions.

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

### `DELETE /v1/auth/tokens/{token_id}`

No un-revoke. A token someone revoked because they think it leaked must stay dead, and an undo button is a way to reinstate a compromised credential by accident.

| Name | In | Required | Description |
|---|---|---|---|
| `token_id` | path | yes |  |
| `authorization` | header | no |  |

### `GET /v1/concepts`

List concepts. Flat, with a scheme filter and a label search.

| Name | In | Required | Description |
|---|---|---|---|
| `scheme` | query | no | Restrict to one concept scheme. |
| `q` | query | no | Substring match on the label. |
| `limit` | query | no |  |
| `authorization` | header | no |  |

### `GET /v1/concepts/{concept_id}`

| Name | In | Required | Description |
|---|---|---|---|
| `concept_id` | path | yes | A concept IRI, or its last segment. |
| `authorization` | header | no |  |

### `GET /v1/domains`

The ten data domains, each with its structural note.

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

### `GET /v1/datasets`

Search the catalog.

| Name | In | Required | Description |
|---|---|---|---|
| `q` | query | no | Free text. Prefix-matched on the last token. |
| `record_type` | query | no | dataset or reference_model. |
| `fidelity_class` | query | no | Reference models only: indicative, screening or authoritative. |
| `data_domain` | query | no | DD1-DD10, or the concept IRI. |
| `provenance_class` | query | no |  |
| `license` | query | no | SPDX id or LicenseRef. |
| `concept` | query | no | Concept IRI carried by a field of the dataset. |
| `spatial_granularity` | query | no |  |
| `format` | query | no | Distribution format label. |
| `completeness_level` | query | no |  |
| `anonymous_access` | query | no |  |
| `field_count_bucket` | query | no | How much of the schema is described: none, 1-9, 10-49, 50+. |
| `access_restriction` | query | no |  |
| `review_state` | query | no |  |
| `harvest_source` | query | no | How the record got here — see #85. |
| `supported_analysis` | query | no | Analysis-type concept IRI the dataset supports. |
| `voltage_class` | query | no |  |
| `time_resolution` | query | no |  |
| `update_cadence` | query | no |  |
| `link_health` | query | no | verified, degraded, unreachable, redirected. |
| `provenance_grade` | query | no | A, B, C or D. |
| `documentation_grade` | query | no | A, B, C or D. |
| `currency_grade` | query | no | A, B or D. |
| `bulk_download` | query | no |  |
| `reference_only` | query | no |  |
| `has_usage_evidence` | query | no |  |
| `resolution_max_m` | query | no | Keep only datasets whose spatial resolution is this fine or finer, in metres. Records that do not state one are excluded, because 'not captured' is not 'fine enough' — see `spatial_granularity` for the class, which most records do carry. |
| `field_count_min` | query | no | Keep only datasets that describe at least this many fields. `field_count_min=1` is 'show me the datasets I can actually interpret' — the question completeness_level cannot answer, because level 1 means no field metadata by definition and level 2 additionally requires a definition and a value basis on every field, which a schema probe cannot supply. |
| `bbox` | query | no | west,south,east,north in WGS 84. |
| `temporal_start` | query | no | ISO 8601. |
| `temporal_end` | query | no | ISO 8601. |
| `sort` | query | no | Comma-separated fields; a leading `-` is descending, e.g. `-modified`. |
| `facets` | query | no | Comma-separated facet fields. |
| `offset` | query | no |  |
| `limit` | query | no |  |
| `include_unconfirmed` | query | no | Stewards only. |
| `authorization` | header | no |  |

### `GET /v1/datasets/{dataset_id}`

| Name | In | Required | Description |
|---|---|---|---|
| `dataset_id` | path | yes | The dataset's slug, which is the last segment of its IRI — `ecmwf-era5` for `https://catalog.opengrid.org/ds/ecmwf-era5`. A caller holding the IRI takes its last segment; a full IRI is not accepted in the path, because its slashes are indistinguishable from the sub-resource paths (`/schema`, `/quality`) that follow it. |
| `authorization` | header | no |  |

### `POST /v1/datasets/{dataset_id}/access-plan`

Issue an access plan.

| Name | In | Required | Description |
|---|---|---|---|
| `dataset_id` | path | yes | The dataset's slug, which is the last segment of its IRI — `ecmwf-era5` for `https://catalog.opengrid.org/ds/ecmwf-era5`. A caller holding the IRI takes its last segment; a full IRI is not accepted in the path, because its slashes are indistinguishable from the sub-resource paths (`/schema`, `/quality`) that follow it. |
| `authorization` | header | no |  |

### `GET /v1/datasets/{dataset_id}/distributions`

Every way to get the data, and what is known about each.

| Name | In | Required | Description |
|---|---|---|---|
| `dataset_id` | path | yes | The dataset's slug, which is the last segment of its IRI — `ecmwf-era5` for `https://catalog.opengrid.org/ds/ecmwf-era5`. A caller holding the IRI takes its last segment; a full IRI is not accepted in the path, because its slashes are indistinguishable from the sub-resource paths (`/schema`, `/quality`) that follow it. |
| `authorization` | header | no |  |

### `GET /v1/datasets/{dataset_id}/download`

The human-facing path: click, and end up at the source.

| Name | In | Required | Description |
|---|---|---|---|
| `dataset_id` | path | yes | The dataset's slug, which is the last segment of its IRI — `ecmwf-era5` for `https://catalog.opengrid.org/ds/ecmwf-era5`. A caller holding the IRI takes its last segment; a full IRI is not accepted in the path, because its slashes are indistinguishable from the sub-resource paths (`/schema`, `/quality`) that follow it. |
| `authorization` | header | no |  |

### `GET /v1/datasets/{dataset_id}/links`

Ranked, explained connections to other catalog records (PRD §F6).

| Name | In | Required | Description |
|---|---|---|---|
| `dataset_id` | path | yes | The dataset's slug, which is the last segment of its IRI — `ecmwf-era5` for `https://catalog.opengrid.org/ds/ecmwf-era5`. A caller holding the IRI takes its last segment; a full IRI is not accepted in the path, because its slashes are indistinguishable from the sub-resource paths (`/schema`, `/quality`) that follow it. |
| `authorization` | header | no |  |

### `GET /v1/datasets/{dataset_id}/quality`

Currency, provenance and documentation, graded independently.

| Name | In | Required | Description |
|---|---|---|---|
| `dataset_id` | path | yes | The dataset's slug, which is the last segment of its IRI — `ecmwf-era5` for `https://catalog.opengrid.org/ds/ecmwf-era5`. A caller holding the IRI takes its last segment; a full IRI is not accepted in the path, because its slashes are indistinguishable from the sub-resource paths (`/schema`, `/quality`) that follow it. |
| `authorization` | header | no |  |

### `GET /v1/datasets/{dataset_id}/schema`

The record's fields, with units and concepts where they resolve.

| Name | In | Required | Description |
|---|---|---|---|
| `dataset_id` | path | yes | The dataset's slug, which is the last segment of its IRI — `ecmwf-era5` for `https://catalog.opengrid.org/ds/ecmwf-era5`. A caller holding the IRI takes its last segment; a full IRI is not accepted in the path, because its slashes are indistinguishable from the sub-resource paths (`/schema`, `/quality`) that follow it. |
| `limit` | query | no |  |
| `offset` | query | no |  |
| `authorization` | header | no |  |

### `GET /v1/gaps`

Search the gap register.

| Name | In | Required | Description |
|---|---|---|---|
| `q` | query | no | Free text, matched against the title, category and reason. |
| `data_domain` | query | no | DD1-DD10. |
| `needed_by` | query | no | An analysis class that needs this, from the analysis-type scheme — capacityExpansion, productionCost, reliabilityAssessment, acPowerFlow. |
| `limit` | query | no |  |
| `authorization` | header | no |  |

### `POST /v1/reports`

Accept an issue report.

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

### `POST /v1/submissions`

Accept an intake form.

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

### `GET /v1/review`

The next batch, highest leverage first.

| Name | In | Required | Description |
|---|---|---|---|
| `state` | query | no | draft, in-review, confirmed or flagged. |
| `data_domain` | query | no |  |
| `limit` | query | no |  |
| `authorization` | header | no |  |

### `POST /v1/review/{dataset_id}/confirm`

Mark a record reviewed.

| Name | In | Required | Description |
|---|---|---|---|
| `dataset_id` | path | yes |  |
| `authorization` | header | no |  |

### `GET /v1/health`

Is the process up. Nothing else.

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

### `GET /v1/health/ready`

Can this instance serve requests, and how well.

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

### `GET /v1/health/status`

What is loaded, what is indexed, how far behind the index is.

| Name | In | Required | Description |
|---|---|---|---|
| `authorization` | header | no |  |

## Errors

Every deliberate failure is an RFC 9457 problem document with `type`, `title`, `status`, `instance` and `requestId`. One error shape, from one handler, so a client writes one error path rather than one per endpoint.
