"""Every name the deployment configures is a name the code reads (#13).

`ops/docker-compose.yml` is how this project is run in a production shape, and
it is the one place where configuration is written down without a compiler or a
type checker looking at it. It set two variables — `NEXT_PUBLIC_API_BASE_URL`
and `DATAHUB_API_INTERNAL_URL` — that **nothing in the tree read**, for months.
Dead configuration is worse than no configuration, because it reads as
configured: the compose file looked like it had told the web container where the
public API was, so nobody went looking when the sign-in button 404'd.

Two checks, one for each half of the mistake:

* a name set here must be one something reads, and
* it must be settable *at the time it is read* — which for a `NEXT_PUBLIC_`
  value in a Next build means before `npm run build`, not in `environment:`.

Both are cheap, both run on every commit, and neither needs a container.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "ops" / "docker-compose.yml"
WEB_DOCKERFILE = ROOT / "web" / "Dockerfile"

#: Names consumed by an image rather than by this codebase. Each one belongs to
#: the upstream container's own configuration, so grepping our tree for it would
#: correctly find nothing.
THIRD_PARTY = {
    "ADMIN_PASSWORD",
    "JVM_ARGS",
    "FUSEKI_DATASET_1",
    "discovery.type",
    "DISABLE_SECURITY_PLUGIN",
    "OPENSEARCH_JAVA_OPTS",
    "bootstrap.memory_lock",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
}


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def _environment(service: dict) -> dict[str, str]:
    """Compose accepts a mapping or a `KEY=value` list. Read both."""
    env = service.get("environment") or {}
    if isinstance(env, list):
        return dict(item.split("=", 1) for item in env if "=" in item)
    return {name: str(value) for name, value in env.items()}


@pytest.fixture(scope="module")
def settings_names() -> set[str]:
    from datahub.config import Settings

    prefix = Settings.model_config["env_prefix"]
    return {f"{prefix}{field}".upper() for field in Settings.model_fields}


@pytest.fixture(scope="module")
def web_reads() -> set[str]:
    """Environment names `web/src` actually reads."""
    names: set[str] = set()
    for path in (ROOT / "web" / "src").rglob("*.ts*"):
        names.update(re.findall(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)", path.read_text()))
    for config in ("next.config.ts",):
        names.update(
            re.findall(
                r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)", (ROOT / "web" / config).read_text()
            )
        )
    return names


def test_every_name_compose_sets_is_one_something_reads(compose, settings_names, web_reads) -> None:
    known = settings_names | web_reads | THIRD_PARTY
    unread = {
        f"{name} (service {service_name})": name
        for service_name, service in compose["services"].items()
        for name in _environment(service)
        if name not in known
    }
    assert not unread, (
        "these names are configured and read by nothing, so setting them does "
        f"nothing and changing them fixes nothing: {sorted(unread)}"
    )


def test_the_web_container_is_told_both_addresses(compose, web_reads) -> None:
    """The positive form: the bug was not a stray name, it was a missing value.

    A container that fetches the API over the compose network cannot also
    render that address for a browser, so a deployment where the two differ has
    to say so twice. Asserted against what the code reads rather than against
    two literals, so renaming the variable moves this test rather than
    duplicating the decision.
    """
    env = _environment(compose["services"]["web"])
    for name in ("DATAHUB_API_URL", "DATAHUB_PUBLIC_API_URL"):
        assert name in web_reads, f"{name} is asserted here and read nowhere"
        assert name in env, (
            f"the web service does not set {name}, so it falls back to the "
            "image default — which for the public address means rendering a "
            "compose service name as a link a browser is meant to follow"
        )
    assert env["DATAHUB_API_URL"] != env["DATAHUB_PUBLIC_API_URL"], (
        "both addresses are the same, which defeats the point of having two"
    )


def test_no_build_time_name_is_configured_as_a_runtime_one(compose) -> None:
    """`NEXT_PUBLIC_*` in `environment:` is a value that never arrives.

    Next replaces every `process.env.NEXT_PUBLIC_X` reference with a literal
    while `npm run build` runs, so the variable has to exist then. Compose's
    `environment:` block applies when the container *starts*, long after the
    image was built — the setting is accepted, ignored, and looks fine.
    """
    offenders = {
        f"{name} (service {service_name})"
        for service_name, service in compose["services"].items()
        for name in _environment(service)
        if name.startswith("NEXT_PUBLIC_")
    }
    assert not offenders, (
        "a NEXT_PUBLIC_ value is inlined at image build time and cannot be set "
        f"at container start; pass it as a build argument instead: {sorted(offenders)}"
    )


def test_any_build_time_name_the_web_image_sets_is_set_before_the_build() -> None:
    """And the other half: set as a build argument, but too late to matter."""
    lines = WEB_DOCKERFILE.read_text().splitlines()
    build_at = next(
        (i for i, line in enumerate(lines) if re.match(r"\s*RUN\s+npm run build", line)), None
    )
    assert build_at is not None, "no `RUN npm run build` in web/Dockerfile — has it moved?"

    late = [
        line.strip()
        for line in lines[build_at + 1 :]
        if re.match(r"\s*(ENV|ARG)\s+NEXT_PUBLIC_", line)
    ]
    assert not late, (
        "these are declared after `npm run build`, which has already inlined "
        f"whatever they were not: {late}"
    )
