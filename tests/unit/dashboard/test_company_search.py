"""Tests for company search view — thread safety and render path."""

from __future__ import annotations

import ast
import inspect
import threading

import pytest


class TestThreadSafeProgress:
    def test_search_progress_dict_is_thread_safe(self):
        """Concurrent read/write to progress dict must not raise or corrupt state."""
        from dashboard.views.company_search import ThreadSafeDict

        progress = ThreadSafeDict()
        errors: list[Exception] = []

        def writer():
            try:
                for i in range(100):
                    progress.update({
                        "count": i,
                        "results": list(range(i % 10)),
                        "ticker": f"T{i}",
                    })
                    progress["action"] = f"Processing T{i}"
            except Exception as exc:
                errors.append(exc)

        def reader():
            try:
                for _ in range(100):
                    progress.get("count", 0)
                    progress.get("results", [])
                    progress.get("ticker", "")
                    progress.get("action", "")
            except Exception as exc:
                errors.append(exc)

        threads = []
        for _ in range(4):
            threads.append(threading.Thread(target=writer))
            threads.append(threading.Thread(target=reader))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread safety errors: {errors}"
        # Verify final state is coherent
        assert isinstance(progress.get("count"), int)


class TestNoSleepInRenderProgress:
    def test_no_sleep_in_render_progress(self):
        """The render/polling path must not call time.sleep."""
        from dashboard.views import company_search

        source = inspect.getsource(company_search._render_progress)
        tree = ast.parse(source)

        sleep_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "sleep"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "time"
                ):
                    sleep_calls.append(node.lineno)

        assert not sleep_calls, (
            f"time.sleep found in _render_progress at line(s): {sleep_calls}"
        )
