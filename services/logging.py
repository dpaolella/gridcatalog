"""Structured logging. Configured once, at process start."""

from __future__ import annotations

import logging
import sys

import structlog
from datahub.config import Settings, get_settings

_CONFIGURED = False


def configure_logging(settings: Settings | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    settings = settings or get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    # stderr, not stdout. Every CLI command that emits JSON writes it to
    # stdout, and a log line interleaved into that stream turns a parseable
    # document into garbage — `datahub graph bootstrap --json | jq` has to work.
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level)
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=processors,
        # Emit through stdlib logging rather than structlog's default
        # PrintLogger, which writes to stdout whatever `basicConfig` says. That
        # default put log lines into the CLI's data stream, so
        # `datahub graph bootstrap --json | jq` received a log line and a
        # document. Going through stdlib also means one place decides where
        # logs go — here, stderr — for the CLI, the API and the workers alike.
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    configure_logging()
    return structlog.get_logger(name)
