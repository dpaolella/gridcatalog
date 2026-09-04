"""The model seam (WP-3.6).

One place constructs an LLM client, so nothing else in the codebase needs to
know which provider is configured, and every model call in the system can be
switched off with one setting.

Three implementations:

* :class:`AnthropicClient` — the real one, used when a key is configured.
* :class:`DisabledClient` — the default. Raises, and the callers treat that as
  "no enrichment happened" rather than as an error. Enrichment is off unless
  someone turns it on, because an enricher that runs by default is a bill and a
  third-party dependency that nobody chose.
* :class:`ScriptedClient` — returns canned structured output. Not a mock: it is
  how the enricher's *rules* are tested, and the rules are the interesting part.
  ADR-0005 requires that the guardrails hold against a model that ignores its
  instructions, so the tests need a model that ignores its instructions.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from datahub.config import Settings, get_settings
from datahub.errors import DataHubError
from datahub.logging import get_logger

log = get_logger(__name__)


class EnrichmentUnavailable(DataHubError):
    """No model could be reached, or none is configured.

    Not an error condition for the pipeline: a record that is not enriched is a
    record with fewer fields, which is a completeness level, not a failure.
    """

    status_code = 503
    code = "enrichment_unavailable"


@dataclass(slots=True)
class Completion:
    """A model's structured answer, plus what produced it.

    ``model`` and ``prompt_version`` travel with the content because every
    drafted value has to record them (ADR-0005, and a SHACL constraint): without
    both, a bad prompt's output cannot be identified or revoked in bulk.
    """

    data: dict[str, Any]
    model: str
    prompt_version: str
    raw: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class LlmClient(Protocol):
    def complete(self, prompt: str, *, schema: dict[str, Any], system: str = "") -> Completion: ...


class DisabledClient:
    """The default. Every call raises, and every caller treats that as "no
    enrichment happened"."""

    def complete(
        self,
        prompt: str,  # noqa: ARG002 - the protocol's shape, deliberately ignored
        *,
        schema: dict[str, Any],  # noqa: ARG002
        system: str = "",  # noqa: ARG002
    ) -> Completion:
        raise EnrichmentUnavailable(
            "enrichment is disabled; set DATAHUB_ENRICHMENT_ENABLED and an API key to turn it on"
        )


class ScriptedClient:
    """Returns prepared answers, in order, and records what it was asked.

    Deliberately willing to return output that breaks every rule — a fabricated
    licence, a made-up access URL, a field the allow-list forbids. That is the
    point: ADR-0005 says no guardrail may depend on the model's cooperation, and
    the only way to test that is with a model that does not cooperate.
    """

    def __init__(
        self,
        answers: Sequence[dict[str, Any]],
        *,
        model: str = "scripted-model",
        prompt_version: str = "test.1",
    ) -> None:
        self.answers = list(answers)
        self.model = model
        self.prompt_version = prompt_version
        self.prompts: list[str] = []
        self.systems: list[str] = []
        self.schemas: list[dict[str, Any]] = []

    def complete(self, prompt: str, *, schema: dict[str, Any], system: str = "") -> Completion:
        self.prompts.append(prompt)
        self.systems.append(system)
        self.schemas.append(schema)
        if not self.answers:
            raise EnrichmentUnavailable("the scripted client ran out of answers")
        data = self.answers.pop(0)
        return Completion(
            data=data,
            model=self.model,
            prompt_version=self.prompt_version,
            raw=json.dumps(data),
        )


class AnthropicClient:
    """The real client. Structured output via a tool schema.

    A tool schema rather than "please reply with JSON": the API validates the
    shape, so a malformed answer is a client-side error rather than a parse
    failure three layers down, and a model that wanders off-format cannot put
    prose into a field the merge is about to write into a record.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - dependency is optional
                raise EnrichmentUnavailable(
                    "the anthropic package is not installed; install the [enrich] extra"
                ) from exc
            if not self.settings.anthropic_api_key:
                raise EnrichmentUnavailable("no API key configured")
            self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        return self._client

    def complete(self, prompt: str, *, schema: dict[str, Any], system: str = "") -> Completion:
        try:
            response = self.client.messages.create(
                model=self.settings.enrichment_model,
                max_tokens=self.settings.enrichment_max_tokens,
                system=system or None,
                messages=[{"role": "user", "content": prompt}],
                tools=[
                    {
                        "name": "record_enrichment",
                        "description": "Return the drafted fields.",
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "tool", "name": "record_enrichment"},
            )
        except Exception as exc:
            raise EnrichmentUnavailable(f"model call failed: {type(exc).__name__}: {exc}") from exc

        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                return Completion(
                    data=dict(block.input),
                    model=response.model,
                    prompt_version=self.settings.enrichment_prompt_version,
                    usage={
                        "input_tokens": getattr(response.usage, "input_tokens", 0),
                        "output_tokens": getattr(response.usage, "output_tokens", 0),
                    },
                )
        raise EnrichmentUnavailable("the model returned no structured output")


def make_client(settings: Settings | None = None) -> LlmClient:
    """The one place a model client is constructed.

    Off unless switched on. An enricher that ran by default would be a bill and
    a third-party dependency nobody chose, and it would make every test that
    touches the pipeline depend on a network.
    """
    settings = settings or get_settings()
    if not settings.enrichment_enabled:
        return DisabledClient()
    return AnthropicClient(settings)


__all__ = [
    "AnthropicClient",
    "Completion",
    "DisabledClient",
    "EnrichmentUnavailable",
    "LlmClient",
    "ScriptedClient",
    "make_client",
]
