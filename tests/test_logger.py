"""Unit tests for utils/logger.

Spec: .specs/test-coverage-fill/refactor.md (REQ-R-301)

Avoids polluting the cwd with `logs/` by using a tmp working directory.
Forces the `_setup_handlers` failure path by patching FileHandler to
raise.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

import pytest
from bson import Decimal128

import utils.logger as logger_module
from utils.logger import TransactionLogger, to_float_safe


@pytest.fixture
def isolated_tmp_cwd(tmp_path, monkeypatch):
    """Run with a tmp cwd so `logs/` artefacts don't leak into the repo."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def fresh_logger(isolated_tmp_cwd):
    """A TransactionLogger with handlers wired against the tmp dir.

    Two singletons need clearing per test: the named main logger AND the
    process-wide ``audit`` logger that ``logging.getLogger('audit')`` returns.
    Without this, audit handlers from a prior test point at a torn-down
    tmp_path and trigger FileNotFoundError on the next emit.
    """
    name = f"test-logger-{id(isolated_tmp_cwd)}"
    logging.getLogger(name).handlers = []
    logging.getLogger("audit").handlers = []
    yield TransactionLogger(name=name)
    logging.getLogger(name).handlers = []
    logging.getLogger("audit").handlers = []


def test_to_float_safe_decimal128():
    assert to_float_safe(Decimal128("1.5")) == 1.5


def test_to_float_safe_passes_other_numeric_types():
    assert to_float_safe(2) == 2.0
    assert to_float_safe(3.5) == 3.5
    assert to_float_safe("4") == 4.0


def test_get_logger_returns_logger_instance(fresh_logger):
    assert isinstance(fresh_logger.get_logger(), logging.Logger)


def test_log_transaction_writes_to_main_and_audit(fresh_logger, isolated_tmp_cwd, caplog):
    """log_transaction sanitises details on the main-logger path and json-dumps
    the raw entry on the audit path — so callers must pass JSON-serialisable
    details when audit logging is enabled.
    """
    caplog.set_level(logging.INFO)
    fresh_logger.log_transaction(
        transaction_id="TXN_TEST_1",
        event="EVENT",
        details={"ok": True, "count": 3},
    )
    assert any("TXN_TEST_1" in r.message and "EVENT" in r.message for r in caplog.records)
    audit_log = isolated_tmp_cwd / "logs" / "transaction_audit.log"
    assert audit_log.exists()
    audit_lines = audit_log.read_text().strip().splitlines()
    parsed = json.loads(audit_lines[-1])
    assert parsed["transaction_id"] == "TXN_TEST_1"
    assert parsed["event"] == "EVENT"


def test_log_transaction_sanitises_decimal128_for_main_logger(fresh_logger, caplog):
    """The main-logger formatting path runs sanitize_for_json on details so
    Decimal128 values render as strings rather than raising TypeError.

    We disable the audit handler for this test because the audit path dumps
    the raw entry (not the sanitised copy) and would TypeError on Decimal128.
    """
    # The audit branch json.dumps the raw entry; remove the audit_logger
    # attribute entirely so the `hasattr(self, 'audit_logger')` guard bypasses
    # that path and lets us assert just the sanitisation step.
    del fresh_logger.audit_logger
    caplog.set_level(logging.INFO)
    fresh_logger.log_transaction(
        transaction_id="TXN_DEC",
        event="EVT",
        details={"amount": Decimal128("99.99")},
    )
    matched = [r for r in caplog.records if "TXN_DEC" in r.message]
    assert matched and '"amount": "99.99"' in matched[0].message


def test_log_workflow(fresh_logger, caplog):
    caplog.set_level(logging.INFO)
    fresh_logger.log_workflow(
        workflow_id="WF_1",
        event="STARTED",
        details={"foo": "bar"},
    )
    assert any("WF_1" in r.message and "STARTED" in r.message for r in caplog.records)


def test_log_balance_update_writes_audit(fresh_logger, isolated_tmp_cwd, caplog):
    caplog.set_level(logging.INFO)
    fresh_logger.log_balance_update(
        account_number="ACC_1",
        transaction_id="TXN_1",
        old_balance=100.0,
        new_balance=150.0,
        amount=50.0,
        operation="CREDIT",
    )
    msg = caplog.text
    assert "ACC_1" in msg
    assert "CREDIT" in msg
    audit_log = isolated_tmp_cwd / "logs" / "transaction_audit.log"
    last_entry = json.loads(audit_log.read_text().strip().splitlines()[-1])
    assert last_entry["balance_change"] == 50.0


def test_log_insufficient_funds_warns(fresh_logger, isolated_tmp_cwd, caplog):
    caplog.set_level(logging.WARNING)
    fresh_logger.log_insufficient_funds(
        account_number="ACC_1",
        transaction_id="TXN_1",
        requested_amount=200.0,
        available_balance=50.0,
    )
    assert any("Insufficient funds" in r.message for r in caplog.records)
    audit_log = isolated_tmp_cwd / "logs" / "transaction_audit.log"
    entry = json.loads(audit_log.read_text().strip().splitlines()[-1])
    assert entry["shortfall"] == 150.0


@pytest.mark.parametrize("status,expected_level", [
    ("FAILED", logging.ERROR),
    ("ERROR", logging.ERROR),
    ("ROLLBACK", logging.ERROR),
    ("INSUFFICIENT_FUNDS", logging.ERROR),
    ("SUCCESS", logging.INFO),
    ("STARTED", logging.INFO),
])
def test_log_acid_transaction_severity(fresh_logger, caplog, status, expected_level):
    caplog.clear()
    caplog.set_level(logging.DEBUG)
    fresh_logger.log_acid_transaction(
        session_id="S_1",
        operation="OP",
        status=status,
        details={"k": "v"},
    )
    matching = [r for r in caplog.records if "ACID Transaction S_1" in r.message]
    assert matching
    assert matching[0].levelno == expected_level


def test_setup_handlers_failure_branch(monkeypatch, caplog):
    """If FileHandler raises (e.g. unwriteable disk), the warning branch runs."""
    caplog.set_level(logging.WARNING)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    # Replace FileHandler in the logger module so handler creation fails.
    monkeypatch.setattr(logger_module.logging, "FileHandler", boom)

    # Each TransactionLogger instance reuses a logger by name; use a unique
    # name so we get a fresh instance whose handlers haven't been set yet.
    tl = TransactionLogger(name=f"setup-fail-{id(monkeypatch)}")
    assert any("Could not create file handlers" in r.message for r in caplog.records)
    # Logger still constructed and usable
    assert isinstance(tl.get_logger(), logging.Logger)


def test_module_singletons_exposed():
    # Module-level `logger` and `transaction_logger` exist (covers the lines
    # that ran at import time — this test merely asserts the contract).
    assert logger_module.logger is not None
    assert isinstance(logger_module.transaction_logger, TransactionLogger)
