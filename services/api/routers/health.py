"""``/v1/health`` — is this deployment working (WP-4.3).

Three questions an operator and a load balancer ask differently:

* ``/health`` — is the process up? Cheap, no dependencies, suitable for a
  liveness probe. A liveness probe that checked the database restarts the API
  every time the database blinks, which is the wrong remedy for the wrong
  fault.
* ``/health/ready`` — can it serve? Checks the store, the index and the
  operational database, and reports **degraded** rather than failing when a
  non-essential one is down. A catalog whose database is unreachable can still
  serve public search, and taking it out of the load balancer for that would
  turn a partial outage into a total one.
* ``/health/status`` — what is the state of the data? Record counts, projector
  lag, vocabulary checksum. For a human, not a probe.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any, Literal

from datahub.api import deps
from datahub.api.deps import SessionDep, SettingsDep
from datahub.api.schemas import HealthResponse
from datahub.graph.graphs import NamedGraph
from datahub.graph.records import RecordStore
from datahub.logging import get_logger
from fastapi import APIRouter
from sqlalchemy import text

log = get_logger(__name__)

router = APIRouter(tags=["service"])


def _version() -> str:
    try:
        return package_version("opengrid-datahub")
    except PackageNotFoundError:
        return "0.0.0+unknown"


@router.get("/health", response_model=HealthResponse, summary="Liveness")
def health(settings: SettingsDep) -> HealthResponse:
    """Is the process up. Nothing else.

    Deliberately touches no dependency: a liveness probe that checked the
    database would restart the API every time the database blinked, and
    restarting the API does not fix a database.
    """
    return HealthResponse(
        status="ok",
        version=_version(),
        graph_backend=str(settings.graph_backend),
        search_backend=str(settings.search_backend),
    )


@router.get("/health/ready", response_model=HealthResponse, summary="Readiness")
def ready(
    settings: SettingsDep,
    session: SessionDep,
) -> HealthResponse:
    """Can this instance serve requests, and how well.

    ``unhealthy`` only when the graph is unreachable, because without it there
    is no catalog. A missing index or an unreachable database is ``degraded``:
    search falls back, writes fail, reads still work — and a deployment removed
    from rotation for a degraded dependency turns a partial outage into a total
    one.

    **No ``StoreDep``, deliberately** (#77). Declaring it would have FastAPI
    resolve the store before this function runs, and resolving it on a cold
    process *builds* it — parsing the whole catalog. A readiness endpoint that
    loads the catalog in order to report whether the catalog is loaded is one
    that cannot answer during the only window where the answer is interesting.
    The state is read instead, and a store still warming says so.
    """
    checks: dict[str, str] = {}
    state: Literal["ok", "degraded", "unhealthy"] = "ok"

    store = deps.store_if_built()
    if store is None:
        # Not an error and not readiness either: the process is fine and the
        # catalog is still loading. `degraded` keeps it out of rotation while
        # that is true, which is exactly what a readiness probe is for.
        checks["graph"] = "warming: catalog still loading"
        state = "degraded"
    else:
        try:
            store.count(NamedGraph.CATALOG)
            checks["graph"] = "ok"
        except Exception as exc:
            checks["graph"] = f"unreachable: {type(exc).__name__}"
            state = "unhealthy"

    backend = deps.backend_if_built()
    if backend is None:
        checks["search"] = "warming: index still loading"
        state = "degraded" if state == "ok" else state
    else:
        try:
            indexed = backend.count()
            checks["search"] = "ok"
            if indexed == 0:
                checks["search"] = "empty: nothing indexed; run `datahub index reindex`"
                state = "degraded" if state == "ok" else state
        except Exception as exc:
            checks["search"] = f"unreachable: {type(exc).__name__}"
            state = "degraded" if state == "ok" else state

    if session is None:
        checks["database"] = "unreachable"
        state = "degraded" if state == "ok" else state
    else:
        # Execute something. SQLAlchemy connects lazily, so the Session object
        # is non-None whether or not the database is answering — this branch
        # used to test only that dependency injection had produced an object,
        # and so reported "ok" through a total database outage. A readiness
        # probe that cannot go unready is the one thing a readiness probe must
        # not be.
        try:
            session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"unreachable: {type(exc).__name__}"
            state = "degraded" if state == "ok" else state

    return HealthResponse(
        status=state,
        version=_version(),
        graph_backend=str(settings.graph_backend),
        search_backend=str(settings.search_backend),
        checks=checks,
    )


@router.get("/health/status", response_model=HealthResponse, summary="Data state")
def status(
    settings: SettingsDep,
    session: SessionDep,
) -> HealthResponse:
    """What is loaded, what is indexed, how far behind the index is.

    The projector lag is the number that matters here: the index is derived
    state, and a lag that grows without bound means a confirmed record change
    is not reaching search — which looks to a user exactly like the change
    never happened.

    **That number must never be behind the catalog's size** (#77). It comes
    from the operational database and costs a primary-key read; the record
    counts come from the graph. Taking `RecordsDep` made the graph a
    precondition of the whole response, so on a cold process this endpoint
    parsed 29 MB of N-Quads before it would say anything — and the one signal
    nothing else reports was the one signal unavailable. Dependencies are
    resolved here, in order, and a graph that is still warming costs the counts
    rather than the answer.
    """
    checks: dict[str, str] = {}
    catalog = drafts = None
    lag = healthy = None

    store = deps.store_if_built()
    if store is None:
        checks["graph"] = "warming: catalog still loading"
    else:
        try:
            records = RecordStore(store)
            catalog = records.count(graph=NamedGraph.CATALOG)
            drafts = records.count(graph=NamedGraph.DRAFT)
            checks["catalog_records"] = str(catalog)
            checks["draft_records"] = str(drafts)
        except Exception as exc:
            checks["graph"] = f"unreachable: {type(exc).__name__}"

    # PRD §F3 asks for a CAPTCHA on the intake path, and "we thought it was on"
    # is the failure mode a config-driven control has. Reported here so the
    # answer does not require reading the deployment's environment.
    from datahub.api import captcha

    checks["intake_challenge"] = "configured" if captcha.is_configured(settings) else "off"

    backend = deps.backend_if_built()
    if backend is None:
        checks["search"] = "warming: index still loading"
    else:
        try:
            checks["indexed_documents"] = str(backend.count())
        except Exception as exc:
            checks["search"] = f"unreachable: {type(exc).__name__}"

    if session is not None:
        try:
            from datahub.api.models.repositories import Repositories

            repos = Repositories(session)
            lag = repos.projector.lag_seconds()
            healthy = lag is None or lag <= settings.projector_lag_budget_s
            checks["review_queue"] = str(repos.review.counts_by_state())
        except Exception as exc:
            checks["database"] = f"error: {type(exc).__name__}"

    state: Any = "ok"
    reported = " ".join(checks.values())
    if "unreachable" in reported:
        # A store that answers "unreachable" is a catalog that is not there.
        # A store that answers "warming" is one that is not there *yet*, which
        # is a different claim and must not read as the same failure.
        state = "unhealthy" if checks.get("graph", "").startswith("unreachable") else "degraded"
    elif healthy is False or "warming" in reported:
        state = "degraded"

    return HealthResponse(
        status=state,
        version=_version(),
        graph_backend=str(settings.graph_backend),
        search_backend=str(settings.search_backend),
        catalog_records=catalog,
        projector_lag_seconds=lag,
        projector_healthy=healthy,
        checks=checks,
    )
