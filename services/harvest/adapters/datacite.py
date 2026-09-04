"""DataCite — https://api.datacite.org/dois (WP-3.2).

The seed file: *"Backstop for academic datasets not on Zenodo. High noise,
aggressive filtering needed."* The noise is the relevance filter's problem, not
this adapter's; what this adapter must get right is not amplifying it.

**Cursor paging, because DataCite requires it.** Past 10,000 results the
page-number API refuses, and this source is expected to return more than that
before filtering. ``page[cursor]`` is stable across a changing result set, which
also removes the offset-drift problem CKAN has.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from datahub.harvest.adapters.base import Adapter, HarvestedRecord
from datahub.logging import get_logger

log = get_logger(__name__)

PAGE_SIZE = 100
#: A cap on pages followed per query. DataCite's cursor paging ends by omitting
#: `links.next`; this is what happens when it does not. A server that keeps
#: returning the same cursor would otherwise be followed forever, and a
#: harvester stuck in a loop against a third party is worse than one that stops
#: early and says so.
MAX_PAGES = 500


class DataCiteAdapter(Adapter):
    name = "datacite_api"

    def iter_records(
        self, *, limit: int | None = None, checkpoint: dict[str, Any] | None = None
    ) -> Iterator[HarvestedRecord]:
        base = str(self.endpoint or "https://api.datacite.org/dois").rstrip("/")
        queries: list[str] = list(self.config.get("queries") or ["query=energy"])
        emitted = 0
        cursor = (checkpoint or {}).get("cursor")

        # A DOI can match more than one configured query. Emitting it twice
        # would make the run's counts wrong and write the second copy over the
        # first for no reason.
        seen: set[str] = set()

        for query in queries:
            params: dict[str, Any] = {
                **_parse_query(query),
                "page[size]": PAGE_SIZE,
                "page[cursor]": cursor or 1,
            }
            cursors: set[str] = set()
            for _page in range(MAX_PAGES):
                payload = self.get_json(base, params=params)
                items = payload.get("data") or []
                if not items:
                    break

                for item in items:
                    identifier = str(item.get("id") or "")
                    if not identifier or identifier in seen:
                        continue
                    seen.add(identifier)
                    yield HarvestedRecord(
                        source_id=f"{self.source_id}:{identifier}",
                        source=self.name,
                        payload=self._prepare(item),
                        source_url=(item.get("attributes") or {}).get("url"),
                    )
                    emitted += 1
                    if limit is not None and emitted >= limit:
                        return

                next_url = ((payload.get("links") or {}).get("next")) or ""
                cursor = _cursor_of(next_url)
                if not cursor or cursor in cursors:
                    break  # a repeated cursor means the server is not advancing
                cursors.add(cursor)
                params["page[cursor]"] = cursor
            else:
                log.warning("stopped paging at the cap", source=self.source_id, pages=MAX_PAGES)
            cursor = None

    @staticmethod
    def _prepare(item: dict[str, Any]) -> dict[str, Any]:
        """Flatten the JSON:API shapes the mapping cannot index into.

        ``titles`` is a list of ``{title, lang}`` and ``rightsList`` a list of
        ``{rights, rightsIdentifier}``. English is preferred where the record
        says which language a title is in; where it does not, the first is
        taken, because a title in an unstated language beats no title.
        """
        prepared = dict(item)
        attributes = dict(item.get("attributes") or {})

        titles = attributes.get("titles")
        if isinstance(titles, list) and titles:
            english = next(
                (
                    t
                    for t in titles
                    if isinstance(t, dict) and str(t.get("lang", "")).startswith("en")
                ),
                None,
            )
            chosen = english or titles[0]
            attributes["titles"] = chosen if isinstance(chosen, dict) else {"title": chosen}

        rights = attributes.get("rightsList")
        if isinstance(rights, list) and rights:
            # Prefer an entry that carries an identifier: "CC-BY-4.0" maps, and
            # "Creative Commons Attribution 4.0 International" does not.
            identified = next(
                (r for r in rights if isinstance(r, dict) and r.get("rightsIdentifier")), None
            )
            attributes["rightsList"] = identified or rights[0]

        content = attributes.get("contentUrl")
        if isinstance(content, str):
            attributes["contentUrl"] = [{"url": content}]
        elif isinstance(content, list):
            attributes["contentUrl"] = [{"url": url} for url in content if isinstance(url, str)]
        prepared["attributes"] = attributes
        return prepared


def _parse_query(query: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for part in query.split("&"):
        if "=" in part:
            key, _, value = part.partition("=")
            params[key.strip()] = value.strip().replace("+", " ")
    return params


def _cursor_of(url: str) -> str | None:
    from urllib.parse import parse_qs, urlparse

    values = parse_qs(urlparse(url).query).get("page[cursor]")
    return values[0] if values else None


__all__ = ["DataCiteAdapter"]
