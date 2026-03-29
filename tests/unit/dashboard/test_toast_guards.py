"""Tests for toast deduplication — toasts must fire only once per page session."""

from __future__ import annotations

from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parents[3] / "src" / "dashboard"


class TestToastNotRepeatedOnRerun:
    """st.toast calls must be guarded so they don't fire on every rerun."""

    def test_signals_toast_guarded(self):
        source = (DASHBOARD_DIR / "views" / "signals.py").read_text()
        assert "signals_toast_shown" in source, (
            "views/signals.py does not guard st.toast with signals_toast_shown — "
            "toast will fire on every rerun."
        )

    def test_cost_tracker_toast_guarded(self):
        source = (DASHBOARD_DIR / "views" / "cost_tracker.py").read_text()
        assert "cost_tracker_toast_shown" in source, (
            "views/cost_tracker.py does not guard st.toast with cost_tracker_toast_shown — "
            "toast will fire on every rerun."
        )

    def test_toast_not_repeated_on_rerun(self):
        """Simulate two consecutive loads — toast guard must prevent double-fire.

        This checks the structural pattern: the toast must be inside
        a conditional block checking session_state.
        """
        for view_name in ("signals.py", "cost_tracker.py"):
            source = (DASHBOARD_DIR / "views" / view_name).read_text()
            lines = source.split("\n")
            for i, line in enumerate(lines):
                if "st.toast(" in line:
                    # Find the preceding non-blank line
                    for j in range(i - 1, -1, -1):
                        stripped = lines[j].strip()
                        if stripped:
                            assert "_toast_shown" in stripped, (
                                f"{view_name}: st.toast at line {i + 1} is not "
                                f"guarded by a *_toast_shown check "
                                f"(preceding line: {stripped!r})."
                            )
                            break
