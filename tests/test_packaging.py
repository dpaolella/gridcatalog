"""The packaging contract from ADR-0003, asserted rather than trusted."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _declared_packages() -> set[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return set(data["tool"]["setuptools"]["packages"])


def _discovered_packages() -> set[str]:
    services = REPO_ROOT / "services"
    found = {"datahub"}
    for init in services.rglob("__init__.py"):
        rel = init.parent.relative_to(services)
        if rel == Path():
            continue
        found.add("datahub." + ".".join(rel.parts))
    return found


def test_every_subpackage_is_declared() -> None:
    """A new subpackage that is not declared would be missing from the wheel."""
    missing = _discovered_packages() - _declared_packages()
    assert not missing, (
        f"add these to [tool.setuptools] packages in pyproject.toml: {sorted(missing)}"
    )


def test_no_declared_package_is_absent() -> None:
    stale = _declared_packages() - _discovered_packages()
    assert not stale, f"declared but absent from services/: {sorted(stale)}"


def test_the_sdk_is_packaged_separately() -> None:
    """`opengrid` is its own distribution, not a subpackage of `datahub`.

    Deliberate: a modeller installing the SDK should not pull in Fuseki
    clients, SHACL, harvest adapters and a FastAPI app. The two share nothing
    but the REST contract, which is the boundary that makes the split honest
    rather than cosmetic.
    """
    import tomllib

    sdk = REPO_ROOT / "sdk" / "python"
    data = tomllib.loads((sdk / "pyproject.toml").read_text())

    assert data["project"]["name"] == "opengrid-datahub"
    assert data["tool"]["setuptools"]["packages"] == ["opengrid"]
    assert data["project"]["dependencies"] == ["httpx>=0.27"], (
        "the base SDK install is a search client; readers are extras"
    )
    assert "datahub" not in str(data["project"]["dependencies"])


def test_services_is_importable_as_datahub() -> None:
    import datahub
    import datahub.graph

    assert Path(datahub.__file__).parent.name == "services"
    assert datahub.graph.__name__ == "datahub.graph"
