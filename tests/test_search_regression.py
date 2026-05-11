"""Regression fences for vector-search behaviour.

Spec: .specs/search-regression-fences/refactor.md

These tests prevent silent regressions of two fixes that previously caused
fresh deployments to return 0 similar transactions in the workflow:

  REQ-R-101: DecisionRepository._DECIDED_STATUSES is the single source of truth
  REQ-R-102: Decided-statuses includes the historically-decided seed statuses
  REQ-R-103: Every seed status is in _DECIDED_STATUSES
  REQ-R-104: combined_score is renormalised by _active_weight
  REQ-R-105: setup_mongodb runs a post-seed vector-search self-check
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOSITORIES_SRC = (ROOT / "database" / "repositories.py").read_text()
SETUP_SRC = (ROOT / "scripts" / "setup_mongodb.py").read_text()

REQUIRED_DECIDED_STATUSES = {
    "approved",
    "rejected",
    "completed",
    "escalated",
    "pending_review",
    "pending_manager_approval",
}


def _decided_statuses_from_module():
    """Import DecisionRepository fresh and return its _DECIDED_STATUSES."""
    import importlib
    import sys
    sys.modules.pop("database.repositories", None)
    mod = importlib.import_module("database.repositories")
    return set(mod.DecisionRepository._DECIDED_STATUSES)


def test_decided_statuses_constant_exists():
    """REQ-R-101: the constant is defined and non-empty."""
    statuses = _decided_statuses_from_module()
    assert statuses, "DecisionRepository._DECIDED_STATUSES is empty or missing"


def test_decided_statuses_minimum_set():
    """REQ-R-102: the allowlist must include every historically-decided status."""
    statuses = _decided_statuses_from_module()
    missing = REQUIRED_DECIDED_STATUSES - statuses
    assert not missing, (
        f"DecisionRepository._DECIDED_STATUSES is missing required statuses: {missing}"
    )


def test_seed_statuses_subset_of_decided():
    """REQ-R-103: every status literal in seed data must be in the allowlist.

    Catches future seed drift — if someone introduces a new seed status,
    they must also widen the search filter (or pick a different status)
    rather than silently shipping data the workflow can't find.
    """
    # Match any literal "status": "<value>" pair in the seed script.
    seed_statuses = set(re.findall(r'"status":\s*"([a-z_]+)"', SETUP_SRC))
    # Some entries are user-review states (e.g. "pending", "in_progress",
    # "completed") that belong to the human_reviews / notifications collections,
    # not to transactions. Restrict the check to status values that are actually
    # set on transaction documents — `create_comprehensive_test_transactions`
    # is the source of those.
    txn_block = re.search(
        r"async def create_comprehensive_test_transactions\(.*?\n    return transactions\n",
        SETUP_SRC,
        re.DOTALL,
    )
    assert txn_block, "create_comprehensive_test_transactions block not found"
    txn_statuses = set(re.findall(r'"status":\s*"([a-z_]+)"', txn_block.group(0)))

    decided = _decided_statuses_from_module()
    out_of_set = txn_statuses - decided
    assert not out_of_set, (
        f"Seed transactions use status values that aren't in _DECIDED_STATUSES: "
        f"{out_of_set}. Either add them to the allowlist or change the seed."
    )


def test_hybrid_combined_score_renormalised():
    """REQ-R-104: hybrid pipeline renormalises by _active_weight.

    Without normalisation the max combined score for a vector-only candidate
    is 0.8, which collides with the SIMILARITY_THRESHOLD = 0.75 filter and
    causes near-perfect matches to be dropped.
    """
    # The aggregation pipeline must compute an _active_weight field and
    # divide the raw weighted sum by it to produce combined_score.
    assert "_active_weight" in REPOSITORIES_SRC, (
        "hybrid pipeline no longer computes _active_weight — REQ-R-104 regressed"
    )
    assert '"$divide": ["$_raw_score", "$_active_weight"]' in REPOSITORIES_SRC, (
        "combined_score must be `_raw_score / _active_weight` — REQ-R-104 regressed"
    )


def test_setup_has_post_seed_search_check():
    """REQ-R-105: setup_mongodb runs a vector-search probe after seeding."""
    # The check must call vector_search_similar_transactions (or the hybrid
    # equivalent) against the seeded data and warn if 0 results come back.
    assert "_seed_search_self_check" in SETUP_SRC, (
        "scripts/setup_mongodb.py must define a _seed_search_self_check helper"
    )
    # And the helper must actually be invoked from insert_sample_data
    invoked = re.search(r"await\s+_seed_search_self_check\(", SETUP_SRC)
    assert invoked, (
        "_seed_search_self_check must be awaited from insert_sample_data"
    )


def test_setup_is_idempotent_per_collection():
    """REQ-R-105 (companion): insert_sample_data uses per-collection emptiness
    checks rather than a single early-out, so a partial-state DB recovers on
    re-run instead of crashing on duplicate-key errors.
    """
    # The legacy single early-out string must be gone
    assert "Sample data already exists, skipping..." not in SETUP_SRC, (
        "insert_sample_data must not early-out on a single existing collection. "
        "Use per-collection emptiness checks so re-runs are idempotent."
    )
    # And there must be a per-collection empty check helper
    assert "_is_empty" in SETUP_SRC, (
        "insert_sample_data should use a per-collection `_is_empty` helper for idempotency"
    )
