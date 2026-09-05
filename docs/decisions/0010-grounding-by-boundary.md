# ADR-0010: Grounding by boundary, not by prompt

**Status:** Accepted · **Date:** 2026-09-05 · **Extends:** ADR-0003

## Context

PRD §F9 names one property as the most important correctness requirement in the
whole system:

> Every response grounded strictly in real catalog metadata. **The server never
> fabricates a dataset or a field.** Treat this as the single most important
> correctness property; a plausible fabricated dataset is worse than no answer.

The last clause is the hard part. A wrong number is checkable. A dataset that
does not exist, described plausibly, with a licence and a coverage window and a
publisher, is not — a user reads it, believes it, and finds out at the point of
download, if then. And an agent is an *untrusted client fully outside our
control*: no guardrail may depend on its cooperation, so nothing that lives in a
prompt is a guardrail.

The usual approaches are all cooperative:

* Instruct the model not to invent things. This works most of the time, which is
  the worst possible failure rate for a correctness property: frequent enough to
  be relied on, rare enough not to be caught.
* Validate the output against the catalog before returning it. Better, and it
  requires enumerating every field that could carry a fabricated id — a list
  that is correct on the day it is written.
* Ask the model to cite. A fabricated citation is the same class of problem one
  level down.

## Decision

**The MCP server may not reach the store. It calls the REST API and nothing
else.** This is the architecture boundary table's row for `services/mcp`, and it
is stated here because the reason is not the usual one.

A server that could read the store could also compose, summarise, join and
infer — and every one of those is a place where something plausible and untrue
can be produced. A server that can only forward what the API returned **cannot
fabricate a dataset, because it has no way to make one.** The property stops
being a behaviour to verify and becomes a shape the code has.

Three consequences follow, and each is a rule rather than a habit:

1. **Every field of every tool response is copied from an API payload.** There
   is no code path in `datahub.mcp.tools` that constructs a dataset, a field or
   an identifier.
2. **"No connection recorded" is an answer.** `explain_connection` on an
   unlinked pair returns exactly that, with the note that it is a statement
   about what has been catalogued rather than a claim the two are unrelated. A
   plausible paragraph about two datasets that are not linked is this failure
   wearing its most convincing hat.
3. **A tool that references records fetches them.** `author_workflow` retrieves
   every dataset it names before writing it into the specification, because a
   workflow naming a dataset that does not exist would be handed to a user as a
   plan.

The same boundary applies to `sdk/python` and to `web`, for a related reason:
each would otherwise hold a second copy of a rule the API owns, and a second
copy eventually disagrees with the first.

## Consequences

- `tests/mcpserver/test_tools.py::test_no_tool_invents_a_dataset` walks every id
  in every tool's response and checks it against the catalog directly — not
  against another API call, because the point is to catch an id that was
  composed rather than copied.
- The MCP server cannot answer a question the API cannot. That is the trade, and
  it is the right one: the alternative to "I cannot tell you" is not a better
  answer, it is a plausible one.
- A new tool is a new API call. Adding a tool that needed store access would
  require moving it out of `services/mcp`, which is a visible change rather than
  a quiet one.
- Payload caps and truncation live here too, for the adjacent reason: an agent
  handed a truncated list that looked complete reports a count that is wrong,
  confidently. Truncation is therefore reported in words aimed at a model —
  *this response is INCOMPLETE: do not report a count or a conclusion from it.*

## Alternatives considered

- **Let the server read the store and validate its output.** Faster, one fewer
  hop, and it makes the correctness property depend on a list of fields to check
  staying complete. The list will not stay complete.
- **A shared library between the API and the MCP server.** Removes the hop and
  reintroduces the ability to compose. The hop is the mechanism.
- **Prompt-level instructions only.** Kept — the server's `instructions` do say
  the catalog never invents — but as an explanation to a cooperating model, not
  as a control. Every enforceable control is server-side (PRD §F9).
