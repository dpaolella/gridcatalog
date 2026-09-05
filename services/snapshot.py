"""A static snapshot of the catalog, for publishing to a host with no backend.

GitHub Pages serves files, not a process. To put the UI there, every answer the
API would have given has to be computed ahead of time and written to disk.

**The snapshot is produced by driving the real API in-process**, not by a second
serialiser over the same models. That is the whole design:

* the JSON is byte-identical to what the server returns, because it *is* what
  the server returns;
* the shapes cannot drift, because there is no second implementation to drift;
* and entitlement is enforced by the thing that already enforces it — every
  request is made anonymously, so a restricted record is absent from the
  snapshot for exactly the reason it is absent from an anonymous search.

That last point is the one that matters most. A static site is world-readable
by definition and cannot be un-published; an exporter that read the store
directly would happily write an allow-listed record's metadata into a file
served to everyone. This one cannot, because it never sees it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from datahub.logging import get_logger

log = get_logger(__name__)

#: How many records to pull per search page while walking the catalog.
PAGE = 100

#: Facets the static site can filter on. The same list the server-rendered
#: search asks for, so the two show the same filter panel.
FACETS = (
    "data_domain",
    "provenance_class",
    "license",
    "format",
    "completeness_level",
    "spatial_granularity",
    "anonymous_access",
    "link_health",
)


@dataclass
class SnapshotResult:
    directory: Path
    datasets: int = 0
    files: int = 0
    bytes_written: int = 0
    #: Records whose existence is public and whose detail is not. Listed
    #: separately from `skipped` because this is the system working: the
    #: snapshot carries the stub the API served and nothing more.
    restricted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "directory": str(self.directory),
            "datasets": self.datasets,
            "files": self.files,
            "bytes": self.bytes_written,
            "restricted": self.restricted,
            "skipped": self.skipped,
        }


class Snapshot:
    """Writes the catalog as static JSON under a directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.result = SnapshotResult(directory=self.directory)

    # -- driving the API ---------------------------------------------------

    def export(self) -> SnapshotResult:
        from datahub.api.app import create_app
        from datahub.config import get_settings
        from fastapi.testclient import TestClient

        self.directory.mkdir(parents=True, exist_ok=True)

        # The exporter is not a caller — it is the build, driving the app to
        # produce an artefact — so it is not counted against the anonymous
        # budget. Left on, a full export trips its own rate limit and writes a
        # site with pages silently missing, which is the failure mode this
        # whole module is arranged to avoid.
        settings = get_settings()
        was_enabled = settings.rate_limit_enabled
        object.__setattr__(settings, "rate_limit_enabled", False)

        try:
            # No Authorization header anywhere below. Anonymous is not a
            # default here, it is the guarantee: whatever an anonymous caller
            # cannot see does not reach the disk.
            #
            # Deliberately not used as a context manager: entering one runs the
            # app's lifespan, and leaving it runs the shutdown hook, which
            # flushes and drops the process-wide store and search backend. That
            # is right for a server stopping and wrong for a function returning
            # — it would reach out and reset state belonging to whoever called
            # `export()`. Nothing here needs startup; the exporter only reads.
            client = TestClient(create_app(), base_url="http://snapshot")
            summaries = self._walk(client)
            self._write("index.json", self._index(summaries))
            self._write("domains.json", self._get(client, "/v1/domains"))
            self._write("facets.json", self._facets(client))

            for summary in summaries:
                self._export_record(client, summary["id"])
        finally:
            object.__setattr__(settings, "rate_limit_enabled", was_enabled)

        self._write(
            "meta.json",
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "datasets": self.result.datasets,
                "restricted_metadata": self.result.restricted,
                "visibility": "public",
                "note": (
                    "Generated by driving the OpenGrid Data Hub API anonymously, so this "
                    "snapshot contains exactly what an unauthenticated caller can see and "
                    "nothing else."
                ),
            },
        )
        log.info("snapshot written", **self.result.as_dict())
        return self.result

    def _walk(self, client: Any) -> list[dict[str, Any]]:
        """Every dataset an anonymous caller can see, one page at a time."""
        found: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._get(
                client,
                "/v1/datasets",
                params={"limit": PAGE, "offset": offset, "facets": ",".join(FACETS)},
            )
            results = page.get("results", [])
            if not results:
                break
            found.extend(results)
            offset += PAGE
            if offset >= page.get("total", 0):
                break
        self.result.datasets = len(found)
        return found

    def _export_record(self, client: Any, dataset_id: str) -> None:
        """One record's detail responses.

        Two kinds of absence, distinguished — conflating them makes the export
        log useless:

        * A record with **restricted metadata** is listed publicly and its
          detail is not, so every detail endpoint 404s. That is the system
          working; the snapshot keeps the stub the API served and says so.
        * Anything else that fails is a genuine skip: named, logged, and not
          allowed to abort the export, because one record with a broken probe
          should not cost the whole site.
        """
        record = self._try(client, f"/v1/datasets/{dataset_id}", dataset_id, "record")
        if record is None:
            return
        self._write(f"datasets/{dataset_id}/record.json", record)

        parts = (
            ("schema", f"/v1/datasets/{dataset_id}/schema"),
            ("quality", f"/v1/datasets/{dataset_id}/quality"),
            ("distributions", f"/v1/datasets/{dataset_id}/distributions"),
            ("links", f"/v1/datasets/{dataset_id}/links"),
        )
        for index, (name, path) in enumerate(parts):
            body = self._try(client, path, dataset_id, name, quiet=True)
            if body is None:
                if index == 0:
                    # The first 404 settles it: this is a public stub, and the
                    # remaining three would fail for the same reason. Asking
                    # anyway would put three misleading lines in the log.
                    self.result.restricted.append(dataset_id)
                    return
                continue
            self._write(f"datasets/{dataset_id}/{name}.json", body)

    def _try(
        self, client: Any, path: str, dataset_id: str, part: str, *, quiet: bool = False
    ) -> Any | None:
        """Fetch, or report the failure and carry on.

        ``quiet`` for the detail endpoints, where a 404 is an expected answer
        rather than a fault.
        """
        try:
            return self._get(client, path)
        except Exception as exc:
            if not quiet:
                self.result.skipped.append(f"{dataset_id}/{part}: {exc}")
                log.warning("snapshot entry skipped", dataset=dataset_id, part=part, error=str(exc))
            return None

    # -- derived files -----------------------------------------------------

    def _index(self, summaries: list[dict[str, Any]]) -> dict[str, Any]:
        """The search index the browser filters over.

        The full summaries, not a reduced projection. A reduced one would be
        smaller and would mean the static list view rendered from different
        fields than the server-rendered one — which is how two views of the same
        record come to disagree.
        """
        return {"total": len(summaries), "results": summaries}

    def _facets(self, client: Any) -> dict[str, Any]:
        page = self._get(
            client,
            "/v1/datasets",
            params={"limit": 1, "facets": ",".join(FACETS)},
        )
        return page.get("facets", {})

    # -- plumbing ----------------------------------------------------------

    def _get(self, client: Any, path: str, params: dict[str, Any] | None = None) -> Any:
        response = client.get(path, params=params or {})
        if response.status_code >= 400:
            raise RuntimeError(f"{path} -> {response.status_code}")
        return response.json()

    def _write(self, relative: str, payload: Any) -> None:
        target = self.directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, separators=(",", ":"), default=str)
        target.write_text(text)
        self.result.files += 1
        self.result.bytes_written += len(text.encode("utf-8"))


def export(directory: Path) -> SnapshotResult:
    return Snapshot(directory).export()


__all__ = ["FACETS", "Snapshot", "SnapshotResult", "export"]
