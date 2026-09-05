"""Errors the SDK raises.

One class per thing a user can do about it, because that is the only
distinction an exception hierarchy earns. A caller catching :class:`NotFound`
retries with a different id; one catching :class:`NotEntitled` asks for access;
one catching :class:`AccessPlanUnusable` installs a reader.
"""

from __future__ import annotations

from typing import Any


class DataHubError(RuntimeError):
    """Base for everything this package raises."""

    def __init__(self, message: str, *, status: int | None = None, **context: Any) -> None:
        super().__init__(message)
        self.status = status
        self.context = context


class NotFound(DataHubError):
    """No such dataset — or none you may know about.

    Deliberately the same error for both. The API returns an identical 404 for
    a record that does not exist and one whose existence is restricted, and an
    SDK that distinguished them would reconstruct the existence oracle the API
    spent M6 removing.
    """


class NotEntitled(DataHubError):
    """You are authenticated and this is not yours to read."""


class AccessPlanUnusable(DataHubError):
    """The plan is valid and this process cannot execute it.

    Almost always a missing reader: a Zarr plan without ``xarray`` installed, a
    Parquet plan without ``pandas``. The message names the package, because
    "unusable" with no remedy is a dead end and the remedy is one pip install.
    """


__all__ = ["AccessPlanUnusable", "DataHubError", "NotEntitled", "NotFound"]
