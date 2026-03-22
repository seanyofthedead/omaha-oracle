"""
Tests for the Omaha Oracle dashboard navigation fix.

The bug: Streamlit auto-generates sidebar nav for any directory literally named
`pages/` adjacent to the entrypoint file.  The app also implemented its own
manual radio-button nav, producing two conflicting menus in the sidebar.

The fix: rename `pages/` → `views/` so Streamlit never sees the directory and
update the import paths in `_PAGE_MODULES` accordingly.

These tests prove:
  1. No `pages/` directory exists next to app.py (Streamlit auto-nav is dead).
  2. A `views/` directory exists with exactly the six expected module files.
  3. Every entry in `_PAGE_MODULES` references `dashboard.views.*`, not `dashboard.pages.*`.
  4. Every module in `_PAGE_MODULES` is importable and exposes a callable `render`.

None of these tests require AWS credentials or a running Streamlit server.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DASHBOARD_DIR = Path(__file__).resolve().parents[2] / "src" / "dashboard"
APP_MODULE = "dashboard.app"
EXPECTED_PAGES = {
    "Portfolio Overview": "dashboard.views.portfolio",
    "Watchlist": "dashboard.views.watchlist",
    "Signals": "dashboard.views.signals",
    "Cost Tracker": "dashboard.views.cost_tracker",
    "Owner's Letters": "dashboard.views.letters",
    "Feedback Loop": "dashboard.views.feedback_loop",
    "Upload Analysis": "dashboard.views.upload_analysis",
    "Company Search": "dashboard.views.company_search",
}


# ---------------------------------------------------------------------------
# 1. Filesystem: no `pages/` directory — Streamlit auto-nav cannot trigger
# ---------------------------------------------------------------------------


class TestNoAutoNavTrigger:
    """Streamlit only auto-generates sidebar nav when it finds a directory
    literally named 'pages' adjacent to the running entrypoint.  If that
    directory does not exist, the feature is completely disabled."""

    def test_pages_directory_does_not_exist(self):
        """The `pages/` directory must not exist next to app.py."""
        pages_dir = DASHBOARD_DIR / "pages"
        assert not pages_dir.exists(), (
            f"{pages_dir} still exists — Streamlit will auto-generate a "
            "second navigation menu from its contents."
        )

    def test_views_directory_exists(self):
        """The replacement `views/` directory must be present."""
        views_dir = DASHBOARD_DIR / "views"
        assert views_dir.is_dir(), f"{views_dir} not found — page modules are missing."


# ---------------------------------------------------------------------------
# 2. Filesystem: all six page files are present in `views/`
# ---------------------------------------------------------------------------


class TestViewsDirectoryContents:
    EXPECTED_FILES = {
        "portfolio.py",
        "watchlist.py",
        "signals.py",
        "cost_tracker.py",
        "letters.py",
        "feedback_loop.py",
        "upload_analysis.py",
        "company_search.py",
    }

    def test_all_page_files_present(self):
        views_dir = DASHBOARD_DIR / "views"
        actual = {f.name for f in views_dir.glob("*.py") if f.name != "__init__.py"}
        missing = self.EXPECTED_FILES - actual
        assert not missing, f"Missing page files in views/: {missing}"

    def test_no_extra_unexpected_files(self):
        """Guard against stale copies left behind in views/."""
        views_dir = DASHBOARD_DIR / "views"
        actual = {f.name for f in views_dir.glob("*.py") if f.name != "__init__.py"}
        unexpected = actual - self.EXPECTED_FILES
        assert not unexpected, f"Unexpected files in views/: {unexpected}"


# ---------------------------------------------------------------------------
# 3. Source: _PAGE_MODULES uses `dashboard.views.*`, not `dashboard.pages.*`
# ---------------------------------------------------------------------------


class TestPageModuleMapping:
    def _get_page_modules(self) -> dict[str, str]:
        app = importlib.import_module(APP_MODULE)
        importlib.reload(app)  # ensure we see current source, not cached bytecode
        return app._PAGE_MODULES

    def test_page_modules_match_expected(self):
        assert self._get_page_modules() == EXPECTED_PAGES

    def test_no_pages_references_in_module_map(self):
        """Every value must reference `dashboard.views`, never `dashboard.pages`."""
        for label, module_path in self._get_page_modules().items():
            assert "dashboard.pages" not in module_path, (
                f"'{label}' still points to the old path '{module_path}'. "
                "This would import from the renamed directory and fail at runtime."
            )

    def test_all_eight_pages_registered(self):
        assert len(self._get_page_modules()) == 8


# ---------------------------------------------------------------------------
# 4. Runtime: every module in _PAGE_MODULES is importable with a render()
# ---------------------------------------------------------------------------


class TestPageModulesImportable:
    """Importing and calling render() is exactly what app.py does at runtime.
    If any module fails here, it would also fail when a user clicks that page."""

    @pytest.mark.parametrize("label,module_path", list(EXPECTED_PAGES.items()))
    def test_module_importable(self, label: str, module_path: str):
        try:
            mod = importlib.import_module(module_path)
        except ImportError as exc:
            pytest.fail(f"Could not import '{module_path}' for page '{label}': {exc}")
        assert mod is not None

    @pytest.mark.parametrize("label,module_path", list(EXPECTED_PAGES.items()))
    def test_module_has_render_callable(self, label: str, module_path: str):
        mod = importlib.import_module(module_path)
        assert callable(getattr(mod, "render", None)), (
            f"Module '{module_path}' (page '{label}') has no callable render(). "
            "app.py calls page.render() — this would raise AttributeError at runtime."
        )


# ---------------------------------------------------------------------------
# 5. Regression guard: what the bug looked like
# ---------------------------------------------------------------------------


class TestBugRegression:
    """Documents and enforces the invariant that broke before the fix.

    Before the fix:
      - src/dashboard/pages/ existed  →  Streamlit auto-generated nav #1
      - app.py also called st.sidebar.radio() →  manual nav #2
      - Result: two menus, one non-functional

    After the fix:
      - src/dashboard/pages/ does NOT exist  →  no auto-generated nav
      - app.py still calls st.sidebar.radio() →  one working menu
    """

    def test_sidebar_contains_radio_nav(self):
        """The sidebar module must contain a sidebar.radio call — the working manual nav.

        The radio was originally in app.py but was extracted to sidebar.py
        as part of the shared ``render_sidebar()`` function.
        """
        sidebar_source = (DASHBOARD_DIR / "sidebar.py").read_text()
        assert "st.sidebar.radio" in sidebar_source, (
            "sidebar.py no longer contains st.sidebar.radio. "
            "If the manual nav was removed without adding native multi-page nav, "
            "the sidebar will be empty."
        )

    def test_pages_dir_absence_is_the_fix(self):
        """The single invariant that prevents the duplicate-nav bug."""
        pages_dir = DASHBOARD_DIR / "pages"
        assert not pages_dir.exists(), (
            "REGRESSION: pages/ has reappeared. Streamlit will auto-generate a "
            "second navigation menu, restoring the original duplicate-nav bug."
        )
