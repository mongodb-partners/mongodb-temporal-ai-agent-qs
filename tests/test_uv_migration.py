"""Tests for the pip → uv packaging migration.

Spec: .specs/pip-to-uv/refactor.md

These tests are static: they verify the packaging artifacts that the
migration produces. The application's runtime regression guardrail is the
existing test suite (run via `uv run pytest`).

Traceability:
  REQ-R-001 -> test_pyproject_has_required_pep621_fields
  REQ-R-002 -> test_pyproject_dependencies_match_legacy_requirements
  REQ-R-003 -> test_pyproject_dev_extras_contain_pytest
  REQ-R-004 -> test_uv_lockfile_exists
  REQ-R-005 -> test_pytest_config_in_pyproject
  REQ-R-006 -> test_requirements_txt_removed
  REQ-R-007 -> test_dockerfile_uses_uv_sync
  REQ-R-008 -> test_quick_setup_uses_uv
  REQ-R-009 -> test_readme_uses_uv_sync
  INV-002   -> test_pyproject_requires_python_3_13
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

ROOT = Path(__file__).resolve().parent.parent

# Runtime dependency keys we need to keep, derived from the legacy requirements.txt.
LEGACY_RUNTIME_DEPS = {
    "streamlit",
    "fastapi",
    "uvicorn",
    "temporalio",
    # motor was dropped in the motor→pymongo refactor; pymongo's native
    # async API replaces it. See .specs/motor-to-pymongo-async/refactor.md.
    "pymongo",
    "boto3",
    "pydantic",
    "pandas",
    "plotly",
    "python-dotenv",
    "httpx",
    "networkx",
    "voyageai",
    "groq",
}

# Dev/test deps go into [project.optional-dependencies].dev
DEV_DEPS = {"pytest", "pytest-asyncio"}


@pytest.fixture(scope="module")
def pyproject():
    """Parse pyproject.toml; fails the whole module if absent (REQ-R-001)."""
    path = ROOT / "pyproject.toml"
    if not path.exists():
        pytest.fail("pyproject.toml is missing — REQ-R-001 not satisfied")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _dep_name(spec: str) -> str:
    """Extract the package name from a PEP 508 spec like 'fastapi>=0.116.2'."""
    return re.split(r"[<>=!~ \[]", spec, maxsplit=1)[0].strip().lower()


# ---------------------------------------------------------------------------
# REQ-R-001: PEP 621 metadata
# ---------------------------------------------------------------------------

def test_pyproject_has_required_pep621_fields(pyproject):
    proj = pyproject.get("project")
    assert proj, "pyproject.toml missing [project] table"
    assert proj.get("name"), "[project].name missing"
    assert proj.get("version"), "[project].version missing"
    assert proj.get("requires-python"), "[project].requires-python missing"
    assert isinstance(proj.get("dependencies"), list), (
        "[project].dependencies must be a list"
    )


# ---------------------------------------------------------------------------
# REQ-R-002: runtime deps preserved
# ---------------------------------------------------------------------------

def test_pyproject_dependencies_match_legacy_requirements(pyproject):
    deps = pyproject["project"]["dependencies"]
    declared = {_dep_name(d) for d in deps}
    missing = LEGACY_RUNTIME_DEPS - declared
    assert not missing, (
        f"runtime dependencies missing from pyproject.toml: {missing}"
    )


# ---------------------------------------------------------------------------
# REQ-R-003: dev extras
# ---------------------------------------------------------------------------

def test_pyproject_dev_extras_contain_pytest(pyproject):
    optional = pyproject["project"].get("optional-dependencies", {})
    dev = optional.get("dev", [])
    declared = {_dep_name(d) for d in dev}
    missing = DEV_DEPS - declared
    assert not missing, (
        f"dev extras missing from [project.optional-dependencies].dev: {missing}"
    )


# ---------------------------------------------------------------------------
# REQ-R-004: lockfile committed
# ---------------------------------------------------------------------------

def test_uv_lockfile_exists():
    lock = ROOT / "uv.lock"
    assert lock.exists(), "uv.lock missing — run `uv lock` and commit the result"
    # It should be non-trivial — uv.lock for a multi-dep project is large.
    assert lock.stat().st_size > 1024, "uv.lock looks suspiciously small"


# ---------------------------------------------------------------------------
# REQ-R-005: pytest config moved into pyproject.toml
# ---------------------------------------------------------------------------

def test_pytest_config_in_pyproject(pyproject):
    tool = pyproject.get("tool", {})
    pytest_cfg = tool.get("pytest", {}).get("ini_options", {})
    assert pytest_cfg, (
        "[tool.pytest.ini_options] missing — pytest config must move into pyproject.toml"
    )
    # testpaths must point at tests/ as before
    assert "tests" in pytest_cfg.get("testpaths", []), (
        "testpaths in [tool.pytest.ini_options] must include 'tests'"
    )


# ---------------------------------------------------------------------------
# REQ-R-006: requirements.txt removed
# ---------------------------------------------------------------------------

def test_requirements_txt_removed():
    assert not (ROOT / "requirements.txt").exists(), (
        "requirements.txt still exists — delete it after the uv migration"
    )


# ---------------------------------------------------------------------------
# REQ-R-007: Dockerfile uses uv
# ---------------------------------------------------------------------------

def test_dockerfile_uses_uv_sync():
    text = (ROOT / "Dockerfile").read_text()
    assert "uv sync" in text, "Dockerfile must install deps with `uv sync`"
    assert "pip install -r requirements.txt" not in text, (
        "Dockerfile still has `pip install -r requirements.txt`"
    )
    # The lockfile must be copied into the build context for `uv sync --frozen` to work
    assert "uv.lock" in text, "Dockerfile must COPY uv.lock"
    assert "pyproject.toml" in text, "Dockerfile must COPY pyproject.toml"


# ---------------------------------------------------------------------------
# REQ-R-008: quick_setup.sh uses uv
# ---------------------------------------------------------------------------

def test_quick_setup_uses_uv():
    text = (ROOT / "scripts" / "quick_setup.sh").read_text()
    assert "uv sync" in text, "quick_setup.sh must call `uv sync`"
    assert "pip install -r requirements.txt" not in text, (
        "quick_setup.sh still calls `pip install -r requirements.txt`"
    )


# ---------------------------------------------------------------------------
# REQ-R-009: README updated
# ---------------------------------------------------------------------------

def test_readme_uses_uv_sync():
    text = (ROOT / "README.md").read_text()
    assert "uv sync" in text, "README must show `uv sync` workflow"
    assert "pip install -r requirements.txt" not in text, (
        "README still references `pip install -r requirements.txt`"
    )


# ---------------------------------------------------------------------------
# INV-002: Python version stays at >= 3.13
# ---------------------------------------------------------------------------

def test_pyproject_requires_python_3_13(pyproject):
    requires = pyproject["project"]["requires-python"]
    # Accept ">=3.13" or ">=3.13,<4" — must include 3.13 as the floor.
    assert "3.13" in requires, (
        f"requires-python should keep Python 3.13 as the floor, got {requires!r}"
    )
