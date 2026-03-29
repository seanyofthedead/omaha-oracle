"""Tests for code deduplication fixes across the dashboard and analysis layers.

Bug 1: Three independent closed-order fetch functions
Bug 2: page_toast_shown shared key across pages
Bug 3: Two ThreadSafe dict wrappers
Bug 4: Moat threshold inconsistency (>=6 vs >=7)
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src"
DASHBOARD = SRC / "dashboard"
ANALYSIS = SRC / "analysis"


# ── Bug 1: Single _fetch_closed_orders in data.py ──────────────────────


class TestClosedOrdersSingleSource:
    """All views must import _fetch_closed_orders from dashboard.data, not define their own."""

    def test_data_module_exports_fetch_closed_orders(self):
        source = (DASHBOARD / "data.py").read_text()
        assert "def _fetch_closed_orders" in source or "def fetch_closed_orders" in source, (
            "dashboard/data.py must define _fetch_closed_orders (or fetch_closed_orders)."
        )

    def test_performance_imports_from_data(self):
        source = (DASHBOARD / "views" / "performance.py").read_text()
        assert "def _fetch_closed_orders" not in source, (
            "performance.py must not define its own _fetch_closed_orders — "
            "it should import from dashboard.data."
        )

    def test_trade_history_imports_from_data(self):
        source = (DASHBOARD / "views" / "trade_history.py").read_text()
        assert "def _fetch_closed_orders" not in source, (
            "trade_history.py must not define its own _fetch_closed_orders — "
            "it should import from dashboard.data."
        )

    def test_analytics_no_inline_get_orders_closed(self):
        source = (DASHBOARD / "views" / "analytics.py").read_text()
        assert 'get_orders(status="closed"' not in source, (
            "analytics.py must not call get_orders(status='closed') inline — "
            "it should use the shared _fetch_closed_orders from dashboard.data."
        )


# ── Bug 2: Toast keys namespaced per page ───────────────────────────────


class TestToastKeysNamespaced:
    """Each page must use a unique session-state key for toast deduplication."""

    def test_signals_uses_namespaced_key(self):
        source = (DASHBOARD / "views" / "signals.py").read_text()
        assert "signals_toast_shown" in source, (
            "signals.py must use 'signals_toast_shown' instead of generic 'page_toast_shown'."
        )
        assert '"page_toast_shown"' not in source, (
            "signals.py must NOT use the generic 'page_toast_shown' key."
        )

    def test_cost_tracker_uses_namespaced_key(self):
        source = (DASHBOARD / "views" / "cost_tracker.py").read_text()
        assert "cost_tracker_toast_shown" in source, (
            "cost_tracker.py must use 'cost_tracker_toast_shown' instead of generic 'page_toast_shown'."
        )
        assert '"page_toast_shown"' not in source, (
            "cost_tracker.py must NOT use the generic 'page_toast_shown' key."
        )

    def test_no_key_collision(self):
        """Signals and cost_tracker must use different toast keys."""
        sig_src = (DASHBOARD / "views" / "signals.py").read_text()
        ct_src = (DASHBOARD / "views" / "cost_tracker.py").read_text()
        # Extract all *_toast_shown keys
        import re

        sig_keys = set(re.findall(r'"(\w+_toast_shown)"', sig_src))
        ct_keys = set(re.findall(r'"(\w+_toast_shown)"', ct_src))
        assert sig_keys.isdisjoint(ct_keys), (
            f"Toast keys collide between signals ({sig_keys}) and cost_tracker ({ct_keys})."
        )


# ── Bug 3: Single ThreadSafe dict wrapper ───────────────────────────────


class TestSingleThreadSafeWrapper:
    """Only ThreadSafeProgress should exist; ThreadSafeDict must be removed."""

    def test_no_threadsafe_dict_in_company_search(self):
        source = (DASHBOARD / "views" / "company_search.py").read_text()
        assert "class ThreadSafeDict" not in source, (
            "company_search.py must not define ThreadSafeDict — "
            "it should import ThreadSafeProgress from search_runner."
        )

    def test_company_search_imports_threadsafe_progress(self):
        source = (DASHBOARD / "views" / "company_search.py").read_text()
        assert "ThreadSafeProgress" in source, (
            "company_search.py must import and use ThreadSafeProgress from search_runner."
        )

    def test_search_runner_still_has_threadsafe_progress(self):
        source = (DASHBOARD / "search_runner.py").read_text()
        assert "class ThreadSafeProgress" in source, (
            "search_runner.py must keep its ThreadSafeProgress class."
        )


# ── Bug 4: Moat threshold consistency (>=7) ─────────────────────────────


class TestMoatThresholdConsistency:
    """The moat_analysis handler must use >=7, matching guardrails and search_config."""

    def test_handler_uses_threshold_7(self):
        source = (ANALYSIS / "moat_analysis" / "handler.py").read_text()
        # Must NOT contain >= 6 for moat_score
        assert 'moat_score", 0) >= 6' not in source, (
            "moat_analysis/handler.py still uses >= 6 threshold — must be >= 7."
        )

    def test_handler_stores_passed_with_7(self):
        source = (ANALYSIS / "moat_analysis" / "handler.py").read_text()
        assert 'moat_score", 0) >= 7' in source, (
            "moat_analysis/handler.py must use >= 7 for the passed flag."
        )

    def test_search_config_moat_min_is_7(self):
        source = (DASHBOARD / "search_config.py").read_text()
        assert "MOAT_MIN = 7" in source

    def test_thesis_generator_moat_min_is_7(self):
        source = (ANALYSIS / "thesis_generator" / "handler.py").read_text()
        assert "MOAT_MIN = 7" in source
