"""Tests for auth hardening in app.py — constant-time comparison and rate limiting."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parents[3] / "src" / "dashboard"


class TestAuthUsesHmacCompare:
    """The login password check must use hmac.compare_digest to prevent timing attacks."""

    def test_auth_uses_hmac_compare(self):
        source = (DASHBOARD_DIR / "app.py").read_text()
        tree = ast.parse(source)

        # Find the _require_auth function
        func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_require_auth":
                func = node
                break

        assert func is not None, "_require_auth function not found in app.py"

        # Get the source lines of just this function
        func_source = ast.get_source_segment(source, func)
        assert func_source is not None, "Could not extract _require_auth source"

        assert "compare_digest" in func_source, (
            "_require_auth does not use hmac.compare_digest — "
            "password comparison is vulnerable to timing attacks."
        )

    def test_auth_has_rate_limiting(self):
        """_require_auth must track login attempts and enforce a lockout."""
        source = (DASHBOARD_DIR / "app.py").read_text()
        assert "login_attempts" in source, (
            "app.py does not track login_attempts in session_state — "
            "no rate limiting on failed logins."
        )

    def test_hmac_imported(self):
        """The hmac module must be imported in app.py."""
        source = (DASHBOARD_DIR / "app.py").read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        assert "hmac" in imports, "hmac module is not imported in app.py"
