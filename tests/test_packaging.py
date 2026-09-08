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


# ---------------------------------------------------------------------------
# Where the application finds its assets, which depends on how it was installed
# ---------------------------------------------------------------------------
#
# `vocab/`, `shapes/`, `schemas/`, `data/`, `config/` and `ops/` are top-level
# directories beside the source tree rather than package data, so locating them
# means knowing the install layout. Two exist and only one had ever been run:
#
#   editable — `datahub/config.py` is inside the checkout, so the module's
#              grandparent is the repository root and the assets sit beside it.
#              Every developer, every CI job and `pages.yml`.
#   wheel    — the module is in `site-packages`, whose grandparent holds no
#              assets. Every Dockerfile in this repository.
#
# The wheel case was broken and nothing noticed, because no workflow had ever
# built an image (issue #64, stage 0). `graph bootstrap` reported
# `files=0 triples=0` and then died on a shapes file that was not there, and
# `datahub db upgrade` asked alembic for `.../site-packages/ops/migrations`.


def _layout(root: Path, *names: str) -> Path:
    for name in names:
        (root / name).mkdir(parents=True)
    return root


def test_a_checkout_is_preferred_to_the_working_directory(tmp_path) -> None:
    """The editable case, and it must stay first.

    Running the CLI from a directory that happens to hold a `vocab/` should
    not redirect a checkout's own commands at it.
    """
    from datahub.config import _asset_root

    checkout = _layout(tmp_path / "checkout", "vocab", "shapes")
    elsewhere = _layout(tmp_path / "elsewhere", "vocab", "shapes")
    assert _asset_root((checkout, elsewhere)) == checkout


def test_a_wheel_falls_back_to_the_working_directory(tmp_path) -> None:
    """The regression test. `site-packages` has no assets; the container's
    working directory does, because that is where the Dockerfiles copy them."""
    from datahub.config import _asset_root

    site_packages = _layout(tmp_path / "site-packages")
    workdir = _layout(tmp_path / "app", "vocab", "shapes")
    assert _asset_root((site_packages, workdir)) == workdir


def test_half_a_layout_is_not_mistaken_for_a_deployment(tmp_path) -> None:
    """Two directories are required, not one. A lone `vocab/` in somebody's
    working directory is not this application."""
    from datahub.config import _asset_root

    half = _layout(tmp_path / "half", "vocab")
    whole = _layout(tmp_path / "whole", "vocab", "shapes")
    assert _asset_root((half, whole)) == whole


def test_finding_nothing_still_returns_a_path(tmp_path) -> None:
    """A settings module that raised during import would replace a legible
    "no such file" with an import error from somewhere unrelated. The first
    candidate is returned so the eventual error names a real path."""
    from datahub.config import _asset_root

    first = _layout(tmp_path / "first")
    assert _asset_root((first, _layout(tmp_path / "second"))) == first


def test_the_dockerfiles_ship_what_the_code_looks_for() -> None:
    """`.dockerignore` excluding an asset directory would build an image that
    starts, serves an empty catalog, and passes a health check.

    Checked against the literal entries rather than by implementing Docker's
    matching rules: the realistic mistake is somebody adding `data/` to the
    ignore file, not a subtle glob.
    """
    ignored = {
        line.strip().rstrip("/")
        for line in (REPO_ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.startswith(("#", "!"))
    }
    dockerfile = (REPO_ROOT / "ops" / "Dockerfile.mcp").read_text()
    copied = {
        line.split()[1].rstrip("/")
        for line in dockerfile.splitlines()
        if line.startswith("COPY ") and "--from=" not in line
    }
    collision = {name for name in copied if name.split("/")[0] in ignored}
    assert not collision, (
        f".dockerignore excludes something ops/Dockerfile.mcp copies: {sorted(collision)}"
    )


def test_a_missing_migrations_directory_names_the_way_out(tmp_path, capsys) -> None:
    """`ops/` is not in the runtime image at all — it ships an already-migrated
    database — so a migration attempted there should say what is missing and
    which setting moves it, rather than hand back alembic's traceback about a
    scripts folder that was never asked for.
    """
    import os

    import pytest
    import typer
    from datahub import cli
    from datahub.config import reset_settings

    os.environ["DATAHUB_REPO_ROOT"] = str(tmp_path)
    reset_settings()
    try:
        with pytest.raises(typer.Exit):
            cli._alembic_root()
    finally:
        del os.environ["DATAHUB_REPO_ROOT"]
        reset_settings()

    said = capsys.readouterr()
    assert "ops/alembic.ini" in said.err + said.out
    assert "DATAHUB_REPO_ROOT" in said.err + said.out


def test_the_migrations_are_found_in_a_checkout() -> None:
    from datahub import cli

    assert (cli._alembic_root() / "ops" / "migrations").is_dir()
