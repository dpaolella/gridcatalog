"""``docs/api.md``, generated from the OpenAPI document (WP-10.3).

PRD's repository tree says *api.md — generated from OpenAPI*. Generated, not
written: a hand-maintained API reference is a document that is correct on the
day it is written and wrong by the end of the month, and the wrongness is
invisible because nothing checks it.

``tests/api/test_docs.py`` regenerates it and compares, so a route added
without regenerating the document fails a test rather than shipping.
"""

from __future__ import annotations

from typing import Any

HEADER = """\
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

- **This API never returns data.** `/download` is a redirect and `/access-plan`
  returns a document. Nothing here proxies bytes.
- **A 404 for a record you may not see is byte-identical to a 404 for a record
  that does not exist.** That is deliberate: a distinguishable refusal is an
  existence oracle.
"""


def to_markdown(document: dict[str, Any]) -> str:
    """Render an OpenAPI 3.1 document as a reference page."""
    lines = [HEADER, ""]
    info = document.get("info", {})
    lines.append(
        f"API version **{info.get('version', '?')}**, OpenAPI {document.get('openapi', '?')}."
    )
    lines.append("")

    tags = {t["name"]: t.get("description", "") for t in document.get("tags", [])}
    by_tag: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for path, operations in sorted(document.get("paths", {}).items()):
        for method, operation in operations.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            tag = (operation.get("tags") or ["other"])[0]
            by_tag.setdefault(tag, []).append((method.upper(), path, operation))

    lines.append("## Endpoints")
    lines.append("")
    for tag in sorted(by_tag, key=lambda t: (t == "service", t)):
        lines.append(f"### {tag}")
        lines.append("")
        if tags.get(tag):
            lines.append(tags[tag])
            lines.append("")
        lines.append("| Method | Path | Summary |")
        lines.append("|---|---|---|")
        for method, path, operation in sorted(by_tag[tag], key=lambda o: (o[1], o[0])):
            summary = (operation.get("summary") or "").replace("|", "\\|")
            lines.append(f"| `{method}` | `{path}` | {summary} |")
        lines.append("")

    lines.extend(_details(by_tag))
    lines.append("## Errors")
    lines.append("")
    lines.append(
        "Every deliberate failure is an RFC 9457 problem document with `type`, `title`, "
        "`status`, `instance` and `requestId`. One error shape, from one handler, so a client "
        "writes one error path rather than one per endpoint."
    )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _details(by_tag: dict[str, list[tuple[str, str, dict[str, Any]]]]) -> list[str]:
    lines = ["## Parameters", ""]
    for tag in sorted(by_tag):
        for method, path, operation in sorted(by_tag[tag], key=lambda o: (o[1], o[0])):
            params = operation.get("parameters") or []
            if not params:
                continue
            lines.append(f"### `{method} {path}`")
            lines.append("")
            if operation.get("description"):
                lines.append(_first_paragraph(operation["description"]))
                lines.append("")
            lines.append("| Name | In | Required | Description |")
            lines.append("|---|---|---|---|")
            for param in params:
                description = (param.get("description") or "").replace("|", "\\|")
                lines.append(
                    f"| `{param['name']}` | {param.get('in', '')} | "
                    f"{'yes' if param.get('required') else 'no'} | {description} |"
                )
            lines.append("")
    return lines


def _first_paragraph(text: str) -> str:
    return text.strip().split("\n\n")[0].replace("\n", " ")


__all__ = ["to_markdown"]
