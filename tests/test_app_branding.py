"""Static smoke tests for app.py branding + behavior invariants.

These tests parse app.py as text — they do not run Streamlit. They guard:
1. Behavioral invariants (CHAR tests) that must hold before AND after the
   branding refresh — e.g., six tabs in order, review actions wired,
   Decimal128 conversion present.
2. Branding requirements (REQ-E-*) that the refresh introduces.

Run: `pytest tests/test_app_branding.py -v`
"""

from pathlib import Path

import pytest

APP_PY = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture(scope="module")
def app_source() -> str:
    return APP_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Characterization tests — must pass against UNCHANGED app.py
# ---------------------------------------------------------------------------

def test_CHAR_six_tabs_present(app_source: str) -> None:
    """INV-001: six tabs render in this exact order."""
    expected = [
        "Dashboard",
        "Active Workflows",
        "Scenario Results",
        "Guided Review",
        "Search Methods Demo",
        "Settings",
    ]
    # Find the st.tabs([...]) call
    start = app_source.find("st.tabs([")
    assert start != -1, "st.tabs([...]) call not found in app.py"
    end = app_source.find("])", start)
    tabs_block = app_source[start:end]
    last_idx = -1
    for label in expected:
        idx = tabs_block.find(label)
        assert idx != -1, f"Tab label not found in st.tabs(): {label!r}"
        assert idx > last_idx, f"Tab {label!r} appears out of order"
        last_idx = idx


def test_CHAR_review_actions_unchanged(app_source: str) -> None:
    """INV-004: Approve/Reject/Hold paths call the repository methods."""
    assert "HumanReviewRepository.complete_review_sync" in app_source, (
        "HumanReviewRepository.complete_review_sync call missing"
    )
    assert "TransactionRepository.update_status_sync" in app_source, (
        "TransactionRepository.update_status_sync call missing"
    )


def test_CHAR_decimal128_conversion_present(app_source: str) -> None:
    """INV-010: Decimal128 amounts are converted via from_decimal128."""
    assert "from_decimal128" in app_source, (
        "from_decimal128 import/usage missing — Decimal128 amounts will display wrong"
    )
    # Must be both imported and called
    assert "import" in app_source.split("from_decimal128")[0].splitlines()[-1] or \
           "from utils.decimal_utils import" in app_source, (
        "from_decimal128 not imported from utils.decimal_utils"
    )


# ---------------------------------------------------------------------------
# Branding tests — fail against UNCHANGED app.py, pass after the refresh
# ---------------------------------------------------------------------------

BRAND_TOKENS = {
    "spring_green": "#00ED64",
    "forest_green": "#00684A",
    "navy": "#001E2B",
    "surface": "#0C1117",
}


def test_brand_palette_in_css(app_source: str) -> None:
    """REQ-E-001 / REQ-E-003: brand palette tokens defined in CSS."""
    for name, hex_value in BRAND_TOKENS.items():
        assert hex_value in app_source, (
            f"Brand token {name} ({hex_value}) missing from app.py"
        )


def test_mongodb_fonts_loaded(app_source: str) -> None:
    """REQ-E-002: MongoDB fonts loaded via @font-face."""
    assert "Euclid Circular A" in app_source, "Euclid Circular A font not loaded"
    assert "MongoDB Value Serif" in app_source, "MongoDB Value Serif font not loaded"
    assert "@font-face" in app_source, "@font-face rule missing"


def test_favicon_set(app_source: str) -> None:
    """REQ-E-015: MongoDB favicon set in st.set_page_config."""
    assert "mongodb.com/assets/images/global/favicon.ico" in app_source, (
        "MongoDB favicon URL not set in st.set_page_config(page_icon=...)"
    )


def test_mongodb_logo_inlined(app_source: str) -> None:
    """REQ-E-004: MongoDB logo embedded as inline SVG (spring-green fill)."""
    assert "<svg" in app_source, "No inline <svg> found in app.py"
    # Spring-green-filled SVG path is the MongoDB logo signature
    assert 'fill="#00ED64"' in app_source, (
        "Inline MongoDB SVG with spring-green fill not found"
    )


def test_plotly_theme_helper_applied(app_source: str) -> None:
    """REQ-E-011: apply_mdb_theme defined and applied to >= 5 figures."""
    assert "def apply_mdb_theme(" in app_source, (
        "apply_mdb_theme helper not defined"
    )
    call_count = app_source.count("apply_mdb_theme(")
    # 1 def + at least 5 invocations = 6 occurrences minimum
    assert call_count >= 6, (
        f"apply_mdb_theme called only {call_count - 1} times; "
        f"expected at least 5 figure applications"
    )


def test_dark_theme_only(app_source: str) -> None:
    """Dark theme is the only theme; toggle removed."""
    assert "MONGODB_THEME_CSS" in app_source, "MONGODB_THEME_CSS constant missing"
    assert "MONGODB_THEME_CSS_LIGHT" not in app_source, (
        "Light theme should be removed"
    )
    assert 'st.radio(\n        "Theme"' not in app_source, (
        "Theme radio toggle should be removed"
    )
