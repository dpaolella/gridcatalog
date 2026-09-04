"""AWS Registry of Open Data — a git repository of YAML files (WP-3.3).

The seed file: *"Every dataset is a YAML file with Description, Documentation,
License, Resources (bucket, region, type). Resources map almost one-to-one onto
our Distribution object. Also the route to AWS Open Data Sponsorship for
anything OpenGrid hosts."*

**It reads a checkout, not the API.** GitHub's contents API would need 400
requests and a token; a shallow clone is one request and gives the whole
registry, and the registry is a git repository whose whole point is that it can
be cloned. The clone is the caller's to arrange — this adapter takes a path —
which also makes it the one network adapter that is fully testable offline.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from datahub.config import Settings
from datahub.errors import SourceUnavailable
from datahub.harvest.adapters.base import Adapter, HarvestedRecord, slugify
from datahub.logging import get_logger

log = get_logger(__name__)


class YamlRepoAdapter(Adapter):
    name = "yaml_repo"

    def __init__(
        self,
        source_id: str,
        settings: Settings | None = None,
        *,
        checkout: Path | None = None,
        path_glob: str = "datasets/*.yaml",
        **kwargs: Any,
    ) -> None:
        super().__init__(source_id, settings, rate_per_second=0, **kwargs)
        self.checkout = Path(checkout) if checkout else None
        self.path_glob = self.config.get("path_glob", path_glob)

    def iter_records(
        self, *, limit: int | None = None, checkpoint: dict[str, Any] | None = None
    ) -> Iterator[HarvestedRecord]:
        root = self.checkout or self._clone()
        after = (checkpoint or {}).get("after")
        started = after is None
        emitted = 0

        for path in sorted(root.glob(self.path_glob)):
            identifier = path.stem
            if not started:
                started = identifier == after
                continue
            try:
                entry = yaml.safe_load(path.read_text())
            except yaml.YAMLError as exc:
                # One malformed file is not a failed harvest. Skipping it
                # loudly beats aborting a 400-record run.
                log.warning("unreadable registry file", path=str(path), error=str(exc))
                continue
            if not isinstance(entry, dict):
                continue
            yield HarvestedRecord(
                source_id=f"{self.source_id}:{identifier}",
                source=self.name,
                payload=self._prepare(entry, identifier),
                source_url=f"https://registry.opendata.aws/{identifier}/",
            )
            emitted += 1
            if limit is not None and emitted >= limit:
                return

    def _clone(self) -> Path:
        """A shallow clone into the configured working directory."""
        target = self.settings.harvest_work_dir / self.source_id
        url = str(self.endpoint or "")
        if not url:
            raise SourceUnavailable(f"{self.source_id} has no endpoint and no checkout")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if (target / ".git").exists():
                subprocess.run(
                    ["git", "-C", str(target), "pull", "--ff-only", "--depth", "1"],
                    check=True,
                    capture_output=True,
                    timeout=300,
                )
            else:
                subprocess.run(
                    ["git", "clone", "--depth", "1", url, str(target)],
                    check=True,
                    capture_output=True,
                    timeout=600,
                )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise SourceUnavailable(f"could not fetch {url}: {exc}") from exc
        return target

    def _prepare(self, entry: dict[str, Any], identifier: str) -> dict[str, Any]:
        prepared = dict(entry)
        prepared["Slug"] = slugify(identifier)
        prepared["_landing_page"] = f"https://registry.opendata.aws/{identifier}/"

        resources = []
        requester_pays_anywhere = False
        for resource in entry.get("Resources") or []:
            if not isinstance(resource, dict):
                continue
            url = self._resource_url(resource)
            if not url:
                continue
            pays = bool(resource.get("RequesterPays"))
            requester_pays_anywhere = requester_pays_anywhere or pays
            resources.append({**resource, "_url": url})
        prepared["Resources"] = resources
        # The programme's definition is anonymous read; RequesterPays is the
        # documented exception, and where it applies the caller pays to read,
        # which is a commercial barrier rather than open access.
        prepared["_anonymous"] = not requester_pays_anywhere
        return prepared

    @staticmethod
    def _resource_url(resource: dict[str, Any]) -> str | None:
        """An https endpoint from an S3 ARN.

        The conversion needs the region and the bucket-naming rules, which is
        adapter knowledge; a field mapping cannot express it. A resource that
        already carries a URL keeps it.
        """
        if isinstance(resource.get("URL"), str):
            return resource["URL"]
        arn = resource.get("ARN")
        if not isinstance(arn, str) or not arn.startswith("arn:aws:s3:::"):
            return None
        path = arn.removeprefix("arn:aws:s3:::")
        bucket, _, prefix = path.partition("/")
        region = resource.get("Region") or "us-east-1"
        # Virtual-hosted style. A bucket name containing a dot breaks TLS on
        # that form, so those fall back to path style rather than producing a
        # URL that cannot be fetched.
        if "." in bucket:
            return f"https://s3.{region}.amazonaws.com/{bucket}/{prefix}"
        return f"https://{bucket}.s3.{region}.amazonaws.com/{prefix}"


__all__ = ["YamlRepoAdapter"]
