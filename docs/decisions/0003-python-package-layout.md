# ADR-0003: `services/` is the `datahub` Python package

**Status:** Accepted · **Date:** 2026-09-04

## Context

PRD §9 fixes the repository layout as `services/graph/`, `services/api/`,
`services/harvest/` and so on, while PRD §7.1 fixes the CLI entry point as
`python -m datahub.harvest --source oedi --limit 100`. Taken literally these
disagree: the first implies an importable name of `services`, the second
`datahub`.

## Decision

Keep the directory layout exactly as specified and map it to the `datahub`
import name in packaging:

```toml
[tool.setuptools]
package-dir = { "datahub" = "services" }
```

`services/graph/client.py` is therefore `datahub.graph.client`. Packages are
enumerated explicitly in `pyproject.toml`; `tests/test_packaging.py` asserts
that every directory under `services/` containing an `__init__.py` appears in
that list, so the enumeration cannot drift silently.

The SDK is a separate distribution in `sdk/python/` (`opengrid-datahub-sdk`,
import name `opengrid`) because it is installed by users who will never install
the services.

## Consequences

- Both PRD statements hold with no deviation to document to a reader.
- Editable installs work (verified), and the mapping is invisible at import
  time.
- Adding a subpackage requires one line in `pyproject.toml`, enforced by a test.

## Alternatives considered

- **`src/datahub/…`.** Conventional, but silently renames every path the PRD
  names, which makes the spec harder to trace against the tree.
- **Import name `services`.** Collides with a very common name in a downstream
  virtualenv, and contradicts the documented CLI.
