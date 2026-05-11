"""Shared pytest fixtures.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-302, REQ-R-305)

The ``mongo_container`` fixture spins up a single-node MongoDB replica
set via testcontainers and exposes a connection URL.  Replica-set mode
is required because the application's ACID transfer code uses
``client.start_session()`` and ``with_transaction``, which PyMongo
refuses on standalone deployments.

Tests that depend on Docker SHOULD declare the ``mongo_container``
fixture; if Docker is unavailable they will be skipped, not failed.
"""

from __future__ import annotations

import os
import time

import pytest
import pytest_asyncio


def _docker_available() -> bool:
    try:
        import docker  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - docker SDK in dev deps
        return False
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


_DOCKER_OK = _docker_available()


@pytest.fixture(scope="session")
def mongo_container():
    """A MongoDB 7 single-node replica set without auth.

    We use a plain ``DockerContainer`` rather than the bundled
    ``MongoDbContainer`` because the latter sets ``MONGO_INITDB_ROOT_*``
    env vars that fight with ``--replSet`` (auth requires a keyfile in
    replica-set mode). Running auth-less is fine for ephemeral tests.
    """
    if not _DOCKER_OK:
        pytest.skip("Docker is not available; skipping integration tests")

    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = (
        DockerContainer("mongo:7.0")
        .with_exposed_ports(27017)
        .with_command("--replSet rs0 --bind_ip_all")
    )
    container.start()
    try:
        wait_for_logs(container, "Waiting for connections", timeout=60)

        # Initiate the replica set once mongod is up.
        deadline = time.time() + 30
        last_exc: Exception | None = None
        while time.time() < deadline:
            try:
                ec, out = container.exec(
                    [
                        "mongosh", "--quiet", "--eval",
                        "rs.initiate({_id: 'rs0', members: [{_id: 0, host: 'localhost:27017'}]})",
                    ]
                )
                # ec is 0 on success; some mongo images return non-zero with
                # "already initialized" — accept that too.
                out_str = out if isinstance(out, str) else out.decode("utf-8", errors="replace")
                if ec == 0 or "already initialized" in out_str:
                    last_exc = None
                    break
                last_exc = RuntimeError(f"rs.initiate exit={ec}: {out_str!r}")
            except Exception as exc:
                last_exc = exc
            time.sleep(1)
        if last_exc is not None:
            raise last_exc

        # Wait for primary election.
        deadline = time.time() + 60
        while time.time() < deadline:
            ec, out = container.exec(
                [
                    "mongosh", "--quiet", "--eval",
                    "db.hello().isWritablePrimary",
                ]
            )
            text = out if isinstance(out, str) else out.decode("utf-8", errors="replace")
            if ec == 0 and "true" in text:
                break
            time.sleep(0.5)

        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def mongo_uri(mongo_container):
    """Connection URI for the test MongoDB. Auth is disabled."""
    host = mongo_container.get_container_host_ip()
    port = mongo_container.get_exposed_port(27017)
    return (
        f"mongodb://{host}:{port}/"
        "?directConnection=true&replicaSet=rs0"
    )


@pytest.fixture
def mongo_db(mongo_uri, monkeypatch):
    """A clean database per test, with the application's config wired to it.

    Drops the test DB before each test so per-test state is isolated.
    """
    from pymongo import MongoClient

    db_name = "test_coverage_db"
    # Wire app config to point at the test container BEFORE any code under
    # test reads config.MONGODB_URI / config.MONGODB_DB_NAME.
    from utils.config import config

    monkeypatch.setattr(config, "MONGODB_URI", mongo_uri, raising=True)
    monkeypatch.setattr(config, "MONGODB_DB_NAME", db_name, raising=True)

    sync_client = MongoClient(mongo_uri)
    sync_client.drop_database(db_name)

    # Reset the lazy global sync client in database.connection so the next
    # call to get_sync_db() picks up the patched URI rather than a stale
    # client pointing at the prior URI.
    from database import connection as db_conn

    db_conn._sync_client = None

    try:
        yield sync_client[db_name]
    finally:
        sync_client.drop_database(db_name)
        sync_client.close()
        db_conn._sync_client = None


@pytest_asyncio.fixture
async def async_mongo(mongo_uri, monkeypatch):
    """Async AsyncMongoClient + clean DB. Used for async repository tests."""
    from pymongo import AsyncMongoClient

    db_name = "test_coverage_async_db"
    from utils.config import config

    monkeypatch.setattr(config, "MONGODB_URI", mongo_uri, raising=True)
    monkeypatch.setattr(config, "MONGODB_DB_NAME", db_name, raising=True)

    # Connect via the application's connection helpers so behaviour matches
    # production (connect_to_mongo also wires indexes).
    from database import connection as db_conn

    db_conn.db.client = None
    db_conn.db.database = None
    db_conn._sync_client = None

    await db_conn.connect_to_mongo()
    # Drop and recreate by re-running create_indexes on a fresh DB.
    await db_conn.db.client.drop_database(db_name)
    db_conn.db.database = db_conn.db.client[db_name]
    await db_conn.create_indexes()
    try:
        yield db_conn.db.database
    finally:
        await db_conn.db.client.drop_database(db_name)
        await db_conn.close_mongo_connection()
        db_conn._sync_client = None
