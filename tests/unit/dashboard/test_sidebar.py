"""Tests for sidebar refresh button."""

from __future__ import annotations

from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parents[3] / "src" / "dashboard"


class TestSidebarHasRefreshButton:
    """sidebar.py must contain a cache-refresh button that clears st.cache_data."""

    def test_sidebar_has_refresh_button(self):
        source = (DASHBOARD_DIR / "sidebar.py").read_text()
        assert "cache_data.clear" in source, (
            "sidebar.py does not contain cache_data.clear — "
            "there is no way for users to manually refresh cached data."
        )

    def test_sidebar_has_rerun_after_clear(self):
        """After clearing the cache, the app should rerun to reflect fresh data."""
        source = (DASHBOARD_DIR / "sidebar.py").read_text()
        assert "st.rerun()" in source, (
            "sidebar.py does not call st.rerun() — "
            "clearing the cache without a rerun won't refresh the page."
        )
