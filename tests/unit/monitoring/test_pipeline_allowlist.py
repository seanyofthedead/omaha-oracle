"""Tests for apply_threshold_adjustments parameter allowlist (security fix)."""

from __future__ import annotations

from unittest.mock import MagicMock

from monitoring.owners_letter.pipeline import (
    ALLOWED_THRESHOLD_PARAMS,
    apply_threshold_adjustments,
)


def _make_config_client(current_thresholds: dict | None = None):
    """Return a mock DynamoClient pre-loaded with screening_thresholds."""
    client = MagicMock()
    if current_thresholds is not None:
        client.get_item.return_value = {
            "config_key": "screening_thresholds",
            "value": current_thresholds,
        }
    else:
        client.get_item.return_value = None
    return client


class TestAllowlistEnforcement:
    """LLM-invented parameter names must be rejected."""

    def test_known_param_is_accepted(self):
        """A parameter on the allowlist should be auto-applied when severity=minor."""
        # Pick the first allowed param
        param = next(iter(ALLOWED_THRESHOLD_PARAMS))
        lessons = [
            {
                "severity": "minor",
                "threshold_adjustment": {
                    "parameter": param,
                    "proposed_value": 0.5,
                },
                "lesson_id": "Q1_2026_1",
            }
        ]
        client = _make_config_client({param: 0.4})
        auto_applied, flagged = apply_threshold_adjustments(lessons, client)
        assert len(auto_applied) == 1
        assert auto_applied[0]["parameter"] == param

    def test_unknown_param_is_rejected(self):
        """A parameter NOT on the allowlist must be silently dropped."""
        lessons = [
            {
                "severity": "minor",
                "threshold_adjustment": {
                    "parameter": "llm_invented_nonsense_param",
                    "proposed_value": 999,
                },
                "lesson_id": "Q1_2026_2",
            }
        ]
        client = _make_config_client({"min_piotroski_f": 5})
        auto_applied, flagged = apply_threshold_adjustments(lessons, client)
        assert len(auto_applied) == 0
        assert len(flagged) == 0
        # put_item should NOT have been called since nothing was applied
        client.put_item.assert_not_called()

    def test_unknown_param_with_moderate_severity_also_rejected(self):
        """Even flagged-for-review params must be on the allowlist."""
        lessons = [
            {
                "severity": "moderate",
                "threshold_adjustment": {
                    "parameter": "evil_param",
                    "proposed_value": 42,
                },
                "lesson_id": "Q1_2026_3",
            }
        ]
        client = _make_config_client({})
        auto_applied, flagged = apply_threshold_adjustments(lessons, client)
        assert len(auto_applied) == 0
        assert len(flagged) == 0

    def test_mix_of_valid_and_invalid_params(self):
        """Only valid params should pass through; invalid ones are dropped."""
        valid_param = next(iter(ALLOWED_THRESHOLD_PARAMS))
        lessons = [
            {
                "severity": "minor",
                "threshold_adjustment": {
                    "parameter": valid_param,
                    "proposed_value": 0.5,
                },
                "lesson_id": "L1",
            },
            {
                "severity": "minor",
                "threshold_adjustment": {
                    "parameter": "hallucinated_metric",
                    "proposed_value": 100,
                },
                "lesson_id": "L2",
            },
        ]
        client = _make_config_client({valid_param: 0.45})
        auto_applied, flagged = apply_threshold_adjustments(lessons, client)
        assert len(auto_applied) == 1
        assert auto_applied[0]["parameter"] == valid_param

    def test_allowlist_is_nonempty_frozenset(self):
        """The allowlist must exist and be non-empty."""
        assert isinstance(ALLOWED_THRESHOLD_PARAMS, frozenset)
        assert len(ALLOWED_THRESHOLD_PARAMS) > 0
