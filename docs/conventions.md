# Conventions

Read this before writing code in this repository. It exists so that work done in
parallel converges instead of colliding.

## IRIs

Minted under fixed bases, defined once in `services/namespaces.py`. Never
hand-write a base in another module.

| Kind | Pattern | Example |
|---|---|---|
| Dataset | `https://catalog.opengrid.org/ds/{slug}` | `.../ds/ecmwf-era5` |
| Distribution | `https://catalog.opengrid.org/dist/{dataset-slug}--{n}` | `.../dist/ecmwf-era5--zarr-s3` |
| Field | `https://catalog.opengrid.org/field/{dataset-slug}/{field-token}` | `.../field/ecmwf-era5/ssrd` |
| File / group | `https://catalog.opengrid.org/file/{dataset-slug}/{token}` | |
| Link | `https://catalog.opengrid.org/link/{a-slug}--{b-slug}` | |
| Concept | `https://schema.opengrid.org/concept/{scheme}/{token}` | `.../concept/data-domain/DD5` |
| Concept scheme | `https://schema.opengrid.org/concept/{scheme}` | `.../concept/grid-concept` |
| Property / class | `https://schema.opengrid.org/ns#{term}` | `og:dataDomain` |

Slugs are lowercase, `[a-z0-9-]`, derived from the title and stable for the life
of the record. A slug never encodes a version; version identity lives in
`og:persistentId` and the supersedes chain (D1, D7).

Concept tokens are `lowerCamelCase` except the data-domain scheme, which uses the
`DD1`–`DD10` notation the PRD fixes.

## Named graphs

`datahub.graph.graphs.NamedGraph`. Seven graphs; nothing writes outside them.
`INFERRED` and `COMPUTED` are derived: droppable and rebuildable at any time.
If losing a graph would lose information, it is in the wrong graph.

## SPARQL

Lives only in `services/graph/`, `services/semantic/`, `services/linksvc/` and
`services/projector/` (PRD principle 9). A contributor who knows Python and not
RDF must be able to write a harvester, an API route or an SDK method without
learning it.

Build queries with `datahub.graph.sparql.bind` and `??placeholder` markers.
Never interpolate with an f-string: a title containing `}` is enough to change
the shape of a query.

## Python

- Python 3.12. `from __future__ import annotations` at the top of every module.
- Public functions and dataclass fields are annotated. `Any` needs a reason.
- Pydantic v2 for anything crossing a process boundary; dataclasses for
  in-process value objects; SQLAlchemy 2.0 typed ORM for operational tables.
- No module reads `os.environ`; take `Settings` (`datahub.config`) as an
  argument, defaulting to `get_settings()`.
- Errors derive from `datahub.errors.DataHubError` and carry the fact that
  failed, not a rendered sentence.
- Log through `datahub.logging.get_logger(__name__)`.
- Docstrings say *why*, not *what the code obviously does*. A comment that
  restates the line above it is noise; a comment naming the failure mode a line
  prevents is worth its space.

## Tests

- `pytest`, no container runtime required for the default suite. Anything
  needing Fuseki, OpenSearch or Postgres is marked `@pytest.mark.integration`;
  anything hitting a third party is marked `@pytest.mark.network`.
- Third-party responses are recorded as fixtures under
  `tests/fixtures/harvest/<source>/`. The suite must not depend on a stranger's
  uptime.
- A test asserts the behaviour named in the PRD, not the implementation. Where a
  test exists because a specific PRD line demands it, cite the line:
  `# PRD §F6.9: reduce, never zero`.

## Things that are not negotiable

These are the PRD's principles restated as review criteria. A change that
violates one is rejected regardless of what it enables.

1. The Hub is never in the byte path.
2. A missing field means "not captured", never "does not exist". Gap markers
   over silent omission.
3. Never a composite quality score.
4. Grounded or absent. Never fabricate a dataset, field, licence or URL.
5. Honest completeness. A thin record labelled thin beats a thin record dressed
   as complete.
6. Explainability over score. Every link, grade and warning carries a concrete
   human-readable reason.
7. Server-side enforcement. Agents and clients are untrusted.
8. The graph is the record; the index is derived.
9. SPARQL stays contained.

## Commit messages

Subject line: `<area>: <what changed>`, imperative, under 72 characters. Body
explains why, and names the PRD section or ADR when the change implements one.
