# Golden set

PRD §11: *~60 fully-specified level 3 records across all ten domains, used to
regression-test concept resolution, link ranking and quality grading.
Hand-curated once, then frozen.*

## What is here, and what is not

The golden set is **the fixture corpus plus a frozen expectations file**. The
records live in `tests/fixtures/records/` — they are already loaded, validated
and depended on by several suites, and a second copy would be a second thing to
keep in step. What lives here is the part that makes them a regression set:
`expectations.yaml`, which states what each record's fields must resolve to and
what each facet must grade, so a change to the resolver or a grader that
silently alters an answer fails a test rather than a review.

**It is 17 records, not 60.** That gap is deliberate and is stated rather than
papered over. Every fact in every record comes from `data/seed-sources.yaml` or
from the dataset's own documentation (PRD principle 4: never fabricate). Growing
to 60 is curation work against real sources — reading a data dictionary,
recording what it says, leaving out what it does not — and inventing 43 records
to hit a number would produce a regression suite that regression-tests fiction.

All ten data domains are represented at level 1. Level 3 coverage is DD1 and
DD5; that is where the concept resolution and link work is currently
demonstrable, and it is what the M7 and M8 done-criteria are asserted against.

## Why the expectations are frozen against a fixed date

Currency is a function of the record *and the clock*. `as_of` in
`expectations.yaml` pins the clock, so a Currency expectation means "this
record, graded on this date" rather than "this record, graded whenever the
suite happens to run" — which would turn every A into a B and fail the build on
a Tuesday for no reason.

## Changing an expectation

An expectation is a claim about what the system *should* answer, not a snapshot
of what it does. So:

1. Change it only together with the change that makes it true, in the same
   commit, with the reason in the commit message.
2. If a change makes an expectation fail and you cannot say why the new answer
   is better, the change is wrong — that is the entire purpose of the file.
3. Never regenerate the file wholesale from current behaviour. A regenerated
   baseline agrees with whatever the code does, including the bug you were
   about to find.

## Adding a record

Follow `tests/fixtures/records/README.md`, then add its expectations here.
A record with no entry is loaded but not regression-tested, which
`tests/semantic/test_golden_set.py` reports rather than tolerates silently.
