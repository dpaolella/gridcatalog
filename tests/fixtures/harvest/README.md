# Harvest fixtures

Recorded payloads for the eight network adapters, one file per source.

**These were written from each API's published response schema, not captured
from a live service.** The build environment has no route to any of the eleven
harvest sources, so a captured fixture was not available. The distinction
matters and is worth stating plainly rather than leaving for someone to
discover:

* What these fixtures **do** test: the adapter's paging, its cursor and
  checkpoint handling, the shapes it derives (`_`-prefixed fields), its
  deduplication, and everything the field mapping does downstream. That is most
  of what an adapter is.
* What they **cannot** test: whether the source still returns this shape. A
  field a source has quietly renamed will pass every test here and return
  nothing in production.

The second gap is closed by a live smoke test per source, marked
`@pytest.mark.network` and skipped by default (`-m 'not network'`). Those are
the tests to run when a harvest starts returning fewer records than it did
last week. Replacing these files with genuinely captured responses, once the
sources are reachable, is tracked as its own work item — the file names and
shapes are already what a recorder would produce.

Each fixture is the JSON body of one API response, so an adapter test can serve
it from a stub transport exactly as httpx would.
