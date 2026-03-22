"""E2E regression tests.

Scenarios 20-22: Verify existing functionality is unbroken after changes.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Scenario 20: All 8 dashboard pages load without import errors
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestAllPagesLoad:
    PAGES = [
        "dashboard.views.watchlist",
        "dashboard.views.signals",
        "dashboard.views.portfolio",
        "dashboard.views.letters",
        "dashboard.views.cost_tracker",
        "dashboard.views.feedback_loop",
        "dashboard.views.upload_analysis",
        "dashboard.views.company_search",
    ]

    @pytest.mark.parametrize("module_path", PAGES)
    def test_page_importable(self, module_path):
        """Every registered page module can be imported without errors."""
        mod = importlib.import_module(module_path)
        assert hasattr(mod, "render"), f"{module_path} missing render()"

    def test_app_module_imports(self):
        """The main app module imports without errors."""
        import dashboard.app  # noqa: F401

    def test_sidebar_module_imports(self):
        """The sidebar module imports without errors."""
        import dashboard.sidebar  # noqa: F401

    def test_page_count(self):
        """Exactly 8 pages are registered."""
        from dashboard.app import _PAGE_MODULES

        assert len(_PAGE_MODULES) == 8


# ---------------------------------------------------------------------------
# Scenario 21: Existing unit test suite passes
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestExistingTestSuite:
    def test_unit_tests_pass(self):
        """Run the full unit test suite as a subprocess — all must pass."""
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/unit/", "-q", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=1200,  # 20 min max
        )
        assert result.returncode == 0, (
            f"Unit tests failed (exit code {result.returncode}):\n"
            f"{result.stdout[-2000:]}\n{result.stderr[-1000:]}"
        )


# ---------------------------------------------------------------------------
# Scenario 22: No new warnings in test output
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestNoNewWarnings:
    def test_no_deprecation_warnings_in_new_modules(self):
        """Import all new modules and check for deprecation warnings."""
        import warnings

        new_modules = [
            "dashboard.search_config",
            "dashboard.candidate_generator",
            "dashboard.search_runner",
            "dashboard.views.company_search",
            "dashboard.upload_validator",
            "dashboard.upload_storage",
            "dashboard.analysis_runner",
            "dashboard.views.upload_analysis",
        ]

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for mod_path in new_modules:
                importlib.import_module(mod_path)

        deprecations = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert deprecations == [], (
            "Deprecation warnings found:\n"
            + "\n".join(f"  {w.filename}:{w.lineno}: {w.message}" for w in deprecations)
        )
