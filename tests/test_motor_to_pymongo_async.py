"""Regression fences for the motor → pymongo async migration.

Spec: .specs/motor-to-pymongo-async/refactor.md

Motor is deprecated. PyMongo's native ``AsyncMongoClient`` (4.16+) is
the supported async API. These tests prevent silent regression to motor.

Traceability:
  REQ-R-201 -> test_async_mongo_client_used,
               test_no_motor_imports_anywhere
  REQ-R-202 -> test_motor_not_in_dependencies
  REQ-R-203 -> test_pymongo_min_version_async_capable
  REQ-R-204 -> test_aggregate_calls_are_awaited
  REQ-R-205 -> test_close_mongo_awaits_client_close,
               test_setup_awaits_client_close
  REQ-R-206 -> test_list_indexes_uses_async_cursor
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
CONNECTION = (ROOT / "database" / "connection.py").read_text()
REPOSITORIES = (ROOT / "database" / "repositories.py").read_text()
SETUP_MONGODB = (ROOT / "scripts" / "setup_mongodb.py").read_text()
API_MAIN = (ROOT / "api" / "main.py").read_text()

PYTHON_SOURCE_FILES = [
    p
    for p in ROOT.rglob("*.py")
    if "/.venv/" not in str(p)
    and "/site-packages/" not in str(p)
    and "/.specs/" not in str(p)
]


# ---------------------------------------------------------------------------
# REQ-R-201: AsyncMongoClient replaces AsyncIOMotorClient
# ---------------------------------------------------------------------------

def test_async_mongo_client_used():
    """REQ-R-201: connection.py constructs pymongo.AsyncMongoClient."""
    assert "AsyncMongoClient(" in CONNECTION, (
        "database/connection.py must construct an AsyncMongoClient"
    )
    assert "AsyncIOMotorClient" not in CONNECTION, (
        "database/connection.py still references the deprecated "
        "AsyncIOMotorClient"
    )


def test_no_motor_imports_anywhere():
    """REQ-R-201: no Python source file imports motor.

    Excludes the spec/test files themselves, which document the old name.
    """
    motor_import_re = re.compile(
        r"^\s*(?:from\s+motor[\w.]*\s+import|import\s+motor)", re.MULTILINE
    )
    offenders = []
    for path in PYTHON_SOURCE_FILES:
        # The migration tests reference motor in string literals only;
        # exclude them so this fence doesn't trigger on its own assertions.
        rel = path.relative_to(ROOT)
        if str(rel).startswith("tests/"):
            continue
        text = path.read_text()
        if motor_import_re.search(text):
            offenders.append(str(rel))
    assert not offenders, (
        f"motor is imported by: {offenders}. The migration to "
        f"pymongo.AsyncMongoClient should have removed every motor import."
    )


# ---------------------------------------------------------------------------
# REQ-R-202: motor dropped from runtime dependencies
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_motor_not_in_dependencies(pyproject):
    """REQ-R-202: motor is not declared as a runtime dependency."""
    deps = pyproject["project"]["dependencies"]
    declared = {re.split(r"[<>=!~ \[]", d, maxsplit=1)[0].strip().lower() for d in deps}
    assert "motor" not in declared, (
        "motor should be removed from [project].dependencies; "
        "pymongo's native async API replaces it."
    )


def test_motor_not_in_uv_lock():
    """REQ-R-202: uv.lock should not contain a motor package entry."""
    lock = (ROOT / "uv.lock").read_text()
    # A package entry in uv.lock looks like: [[package]]\nname = "motor"
    motor_entry = re.search(r'\[\[package\]\][^\[]*name\s*=\s*"motor"', lock)
    assert motor_entry is None, (
        "uv.lock still contains a motor [[package]] entry — run `uv lock` "
        "after removing motor from pyproject.toml"
    )


# ---------------------------------------------------------------------------
# REQ-R-203: PyMongo pinned to a version with native async
# ---------------------------------------------------------------------------

def test_pymongo_min_version_async_capable(pyproject):
    """REQ-R-203: pymongo>=4.16 is required for AsyncMongoClient."""
    deps = pyproject["project"]["dependencies"]
    pymongo_spec = next(
        (d for d in deps if d.lower().startswith("pymongo")), None
    )
    assert pymongo_spec, "pymongo missing from [project].dependencies"
    # Accept any spec >=4.16 — anything older lacks AsyncMongoClient parity.
    match = re.search(r">=\s*(\d+)\.(\d+)", pymongo_spec)
    assert match, f"pymongo spec {pymongo_spec!r} must include a >= bound"
    major, minor = int(match.group(1)), int(match.group(2))
    assert (major, minor) >= (4, 16), (
        f"pymongo must be pinned to >=4.16 for AsyncMongoClient parity; "
        f"got {pymongo_spec!r}"
    )


# ---------------------------------------------------------------------------
# REQ-R-204: aggregate() coroutines are awaited
# ---------------------------------------------------------------------------

def _strip_comments_and_strings(src: str) -> str:
    """Remove triple-quoted docstrings and # comments to avoid false matches."""
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"'''[\s\S]*?'''", "", src)
    src = re.sub(r"(?m)#.*$", "", src)
    return src


def _async_aggregate_call_sites(text: str):
    """Return offsets of ``.aggregate(`` outside of *_sync paths."""
    cleaned = _strip_comments_and_strings(text)
    return [m.start() for m in re.finditer(r"\.aggregate\s*\(", cleaned)]


def test_aggregate_calls_are_awaited():
    """REQ-R-204: every async aggregate(...) call is preceded by await.

    PyMongo's AsyncMongoClient returns a coroutine from .aggregate(); motor
    returned a cursor directly. We scan every async function in the
    project's three async-using files and require `await` immediately
    before the .aggregate( token. Sync aggregate calls (inside *_sync
    methods that use db_sync) are still allowed.
    """
    for label, text in (
        ("database/repositories.py", REPOSITORIES),
        ("api/main.py", API_MAIN),
    ):
        cleaned = _strip_comments_and_strings(text)
        for m in re.finditer(r"([\w_\[\]\.\s]+)\.aggregate\s*\(", cleaned):
            # Skip sync paths: db_sync[...] is the synchronous client.
            preceding = cleaned[max(0, m.start() - 80) : m.start()]
            if "db_sync" in preceding:
                continue
            # Ensure `await` appears in the same statement before .aggregate(
            stmt_start = max(
                cleaned.rfind("\n", 0, m.start()),
                cleaned.rfind(";", 0, m.start()),
            )
            stmt = cleaned[stmt_start : m.end()]
            assert "await" in stmt, (
                f"{label}: async .aggregate(...) call is not awaited:\n  "
                f"{stmt.strip()}"
            )


# ---------------------------------------------------------------------------
# REQ-R-205: close() is awaited
# ---------------------------------------------------------------------------

def test_close_mongo_awaits_client_close():
    """REQ-R-205: close_mongo_connection awaits db.client.close()."""
    # The function body must call `await db.client.close()`. PyMongo's
    # AsyncMongoClient.close() is a coroutine — the prior motor version
    # was synchronous.
    match = re.search(
        r"async\s+def\s+close_mongo_connection\s*\([^)]*\)\s*:.*?(?=\n(?:async\s+)?def\s|\Z)",
        CONNECTION,
        re.DOTALL,
    )
    assert match, "close_mongo_connection function not found"
    body = match.group(0)
    assert re.search(r"await\s+db\.client\.close\s*\(", body), (
        "close_mongo_connection must `await db.client.close()` — "
        "AsyncMongoClient.close() is a coroutine"
    )


def test_setup_awaits_client_close():
    """REQ-R-205: setup_mongodb's finally block awaits client.close()."""
    # Find the `client.close()` call in setup and confirm `await` precedes it.
    cleaned = _strip_comments_and_strings(SETUP_MONGODB)
    match = re.search(r"(\bawait\s+)?client\.close\s*\(", cleaned)
    assert match, "setup_mongodb does not call client.close()"
    assert match.group(1), (
        "setup_mongodb must `await client.close()` — AsyncMongoClient.close() "
        "is a coroutine"
    )


# ---------------------------------------------------------------------------
# REQ-R-206: list_indexes / list_search_indexes use the async cursor pattern
# ---------------------------------------------------------------------------

def test_list_indexes_uses_async_cursor():
    """REQ-R-206: list_indexes()/list_search_indexes() are awaited.

    PyMongo's AsyncMongoClient returns a coroutine from list_indexes()
    and list_search_indexes(); motor returned a chainable cursor. The
    inline form ``await coll.list_indexes().to_list(...)`` only works
    under motor.
    """
    targets = [
        ("database/connection.py", CONNECTION),
        ("scripts/setup_mongodb.py", SETUP_MONGODB),
    ]
    for label, text in targets:
        cleaned = _strip_comments_and_strings(text)
        # The pattern motor allowed (chained, single await on .to_list)
        # is forbidden:
        forbidden = re.search(
            r"await\s+\w[\w\.\[\]]*\.list_(?:search_)?indexes\s*\([^)]*\)\s*\.to_list",
            cleaned,
        )
        assert not forbidden, (
            f"{label}: motor-style chained `await coll.list_*indexes(...).to_list(...)` "
            f"is not valid under AsyncMongoClient. Split into two awaits: "
            f"`cursor = await coll.list_indexes(...); rows = await cursor.to_list(...)`."
        )
