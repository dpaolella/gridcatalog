"""Runtime configuration.

One settings object, read from the environment, passed explicitly. No module
reads ``os.environ`` directly and no module decides its own backend by import
site (ADR-0002).
"""

from __future__ import annotations

import functools
from enum import StrEnum
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class GraphBackend(StrEnum):
    RDFLIB = "rdflib"
    FUSEKI = "fuseki"


class SearchBackend(StrEnum):
    MEMORY = "memory"
    OPENSEARCH = "opensearch"


class QueueBackend(StrEnum):
    EAGER = "eager"
    CELERY = "celery"


class Settings(BaseSettings):
    """Every knob the running system has. Prefix ``DATAHUB_``."""

    model_config = SettingsConfigDict(
        env_prefix="DATAHUB_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # ---- identity -------------------------------------------------------
    environment: str = "development"
    catalog_base_url: str = "https://catalog.opengrid.org"
    api_base_url: str = "http://localhost:8000"

    # ---- graph ----------------------------------------------------------
    graph_backend: GraphBackend = GraphBackend.RDFLIB
    fuseki_url: str = "http://localhost:3030"
    fuseki_dataset: str = "datahub"
    fuseki_user: str | None = None
    fuseki_password: str | None = None
    #: Where the rdflib backend persists. ``None`` keeps it in memory.
    graph_store_path: Path | None = None
    graph_query_timeout_s: float = 30.0

    # ---- search ---------------------------------------------------------
    search_backend: SearchBackend = SearchBackend.MEMORY
    opensearch_url: str = "http://localhost:9200"
    opensearch_index: str = "datahub-datasets"
    opensearch_user: str | None = None
    opensearch_password: str | None = None
    #: Where the in-memory search backend persists between processes.
    search_store_path: Path | None = None
    #: Projector lag above this many seconds is reported unhealthy (PRD §3.1).
    projector_lag_budget_s: float = 60.0

    # ---- operational store ----------------------------------------------
    database_url: str = "sqlite+pysqlite:///./var/datahub.sqlite3"

    # ---- queue ----------------------------------------------------------
    queue_backend: QueueBackend = QueueBackend.EAGER
    redis_url: str = "redis://localhost:6379/0"

    # ---- schema and vocabulary assets -----------------------------------
    repo_root: Path = REPO_ROOT
    vocab_dir: Path = REPO_ROOT / "vocab"
    shapes_path: Path = REPO_ROOT / "shapes" / "opengrid-datahub.ttl"
    context_path: Path = REPO_ROOT / "schemas" / "opengrid-datahub.jsonld"
    seed_sources_path: Path = REPO_ROOT / "data" / "seed-sources.yaml"
    golden_set_dir: Path = REPO_ROOT / "data" / "golden-set"

    # ---- harvest --------------------------------------------------------
    harvest_user_agent: str = (
        "OpenGrid-DataHub/1.0 (+https://opengrid.org; catalog harvester; "
        "contact: data@opengrid.org)"
    )
    harvest_default_rate_per_s: float = 1.0
    #: Where adapters that clone or download keep their working copies. Under
    #: var/ rather than a temp dir so a shallow clone survives between runs —
    #: re-cloning 400 registry files daily is rude to a source we do not own.
    harvest_work_dir: Path = REPO_ROOT / "var" / "harvest"
    harvest_timeout_s: float = 30.0
    harvest_max_retries: int = 3

    # ---- enrichment -----------------------------------------------------
    enrichment_enabled: bool = False
    anthropic_api_key: str | None = None
    enrichment_model: str = "claude-sonnet-5"
    enrichment_max_tokens: int = 2048
    #: Bumped whenever a prompt template changes; recorded on every drafted value.
    enrichment_prompt_version: str = "2026-09-04.1"

    # ---- semantic layer -------------------------------------------------
    #: Below this, concept resolution emits a gap marker instead of guessing.
    concept_match_threshold: float = 0.82
    #: Cadence of the self-contained (currency) recompute batch.
    currency_batch_cron: str = "0 3 * * *"

    # ---- link service ---------------------------------------------------
    link_weights_path: Path = REPO_ROOT / "config" / "link-weights.yaml"
    link_top_n: int = 12

    # ---- broker ---------------------------------------------------------
    access_plan_ttl_s: int = 900
    probe_timeout_s: float = 15.0
    probe_failure_threshold: int = 3

    # ---- auth -----------------------------------------------------------
    secret_key: str = "dev-only-not-a-secret-change-me"
    token_ttl_s: int = 3600
    oidc_providers: str = "github,google,microsoft"
    rate_limit_human_per_min: int = 120
    rate_limit_agent_per_min: int = 600
    rate_limit_anonymous_per_min: int = 60

    # ---- mcp ------------------------------------------------------------
    mcp_payload_cap_bytes: int = 100 * 1024
    mcp_preview_max_rows: int = 100
    mcp_preview_max_bytes: int = 256 * 1024

    # ---- observability --------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("graph_store_path", "search_store_path", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if v in ("", "none", "None") else v

    @property
    def fuseki_query_endpoint(self) -> str:
        return f"{self.fuseki_url.rstrip('/')}/{self.fuseki_dataset}/query"

    @property
    def fuseki_update_endpoint(self) -> str:
        return f"{self.fuseki_url.rstrip('/')}/{self.fuseki_dataset}/update"

    @property
    def fuseki_gsp_endpoint(self) -> str:
        """Graph Store Protocol endpoint, used for whole-graph PUT and GET."""
        return f"{self.fuseki_url.rstrip('/')}/{self.fuseki_dataset}/data"

    @property
    def oidc_provider_list(self) -> list[str]:
        return [p.strip() for p in self.oidc_providers.split(",") if p.strip()]


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached; call :func:`reset_settings` in tests."""
    return Settings()


def reset_settings() -> None:
    get_settings.cache_clear()
