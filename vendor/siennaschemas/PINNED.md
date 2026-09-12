# SiennaSchemas, vendored

Source: <https://github.com/Sienna-Platform/SiennaSchemas>
Pinned at commit `3325d27c8c626a375c7492f1341af139d5686e53` (2026-09-11).
Licence: BSD 3-Clause — see `LICENSE`, copied verbatim from upstream.

## Why a commit and not a tag

Upstream's own README says "Pin an exact tag — never track a moving branch or
'latest'". That instruction is currently unfollowable: `git ls-remote --tags`
returns nothing, so there is no tag to pin. The commit SHA above is the pin,
and it should be replaced with a tag the moment upstream publishes one.

Upstream is pre-1.0 and says so: *"Every version below `1.0` is a pre-release.
Schemas can change incompatibly in any release, and no stability is promised
until `1.0`."* Anything in this repository that validates against these files
records the SHA alongside the result, so a validation receipt stays meaningful
after the pin moves.

## Why vendored rather than a submodule

Every case, filing and reference model the Hub registers is validated against a
specific schema version, and the receipt is part of the record. A submodule
makes that version a property of somebody's checkout; a vendored copy makes it
a property of the commit. The 900 KB is worth the reproducibility.

## What is here

109 JSON Schema files (2020-12 dialect), 183 types in six groups:

| Directory | Types |
|---|---|
| `Core/` | Topology (ACBus, DCBus, Arc, Area, LoadZone), `SystemDocument`, the closed unit vocabulary in `units.json`, and the supplemental attributes `DataSource`, `GeographicInfo`, `EmissionsData` |
| `Operations/` | Branches, static injections, services, market participants, and the operations-side supplemental attributes |
| `Investments/` | Candidate technologies, financial data, requirements (carbon caps, energy share, reserve margin), `PortfolioDocument` |
| `TimeSeries/` | Six ways to attach a series to a component |
| `Dynamics/` | Generator and inverter dynamic models |

`$ref` paths are relative and load-bearing — upstream's contributor guide warns
that moving a file breaks every reference to it, so the directory layout is
reproduced exactly rather than flattened.

## What it does not cover

Sienna models what is *in* a power system. It has no schema for a dataset
record, a study or filing, an assumption set, a quality rubric, or a declared
fidelity class — grep for `study`, `assumption`, `licence`, `quality` and
`fidelity` across these files and every one returns nothing. Those are the
Hub's to define, and they wrap Sienna documents rather than replacing them.
