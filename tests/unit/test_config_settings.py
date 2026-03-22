"""
Unit tests for Settings (shared.config).

Coverage:
  - environment validator: rejects invalid values, normalises to lowercase
  - table name derivation: convention-based defaults, explicit overrides
  - legacy budget conversion: MONTHLY_LLM_BUDGET_CENTS → USD
  - secret accessors: raises RuntimeError when keys absent; returns env value
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

# ------------------------------------------------------------------ #
# TestEnvironmentValidator                                             #
# ------------------------------------------------------------------ #


class TestEnvironmentValidator:
    def test_invalid_environment_raises(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        from shared.config import Settings

        with pytest.raises(ValidationError):
            Settings()

    def test_dev_environment_accepted(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        from shared.config import Settings

        cfg = Settings()
        assert cfg.environment == "dev"

    def test_staging_environment_accepted(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "staging")
        from shared.config import Settings

        cfg = Settings()
        assert cfg.environment == "staging"

    def test_prod_environment_accepted(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "prod")
        from shared.config import Settings

        cfg = Settings()
        assert cfg.environment == "prod"

    def test_environment_normalised_to_lowercase(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "DEV")
        from shared.config import Settings

        cfg = Settings()
        assert cfg.environment == "dev"


# ------------------------------------------------------------------ #
# TestTableNameDerivation                                              #
# ------------------------------------------------------------------ #


class TestTableNameDerivation:
    def test_table_names_use_environment_prefix(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "prod")
        monkeypatch.setenv("TABLE_COMPANIES", "")  # force default derivation
        from shared.config import Settings

        cfg = Settings()
        assert cfg.table_companies == "omaha-oracle-prod-companies"

    def test_all_12_table_names_derived(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "staging")
        for name in ["TABLE_COMPANIES", "TABLE_ANALYSIS", "TABLE_LESSONS"]:
            monkeypatch.setenv(name, "")
        from shared.config import Settings

        cfg = Settings()
        assert cfg.table_companies == "omaha-oracle-staging-companies"
        assert cfg.table_analysis == "omaha-oracle-staging-analysis"
        assert cfg.table_lessons == "omaha-oracle-staging-lessons"

    def test_explicit_table_env_var_not_overwritten(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("TABLE_COMPANIES", "custom-companies-table")
        from shared.config import Settings

        cfg = Settings()
        assert cfg.table_companies == "custom-companies-table"


# ------------------------------------------------------------------ #
# TestLegacyBudgetConversion                                           #
# ------------------------------------------------------------------ #


class TestLegacyBudgetConversion:
    def test_cents_converted_to_usd(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "5000")
        from shared.config import Settings

        cfg = Settings()
        assert cfg.monthly_llm_budget_usd == pytest.approx(50.0)

    def test_cents_wins_over_usd_when_both_set(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "3000")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")
        from shared.config import Settings

        cfg = Settings()
        # cents=3000 → $30.00 (overrides the USD=100 value)
        assert cfg.monthly_llm_budget_usd == pytest.approx(30.0)


# ------------------------------------------------------------------ #
# TestSecretAccessors                                                  #
# ------------------------------------------------------------------ #


class TestSecretAccessors:
    def test_get_alpaca_keys_raises_when_missing(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ALPACA_API_KEY", "")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "")
        # Patch _ssm_get so it never makes a real SSM call → returns None
        mock_ssm = MagicMock(return_value=None)
        mock_ssm.cache_clear = MagicMock()
        monkeypatch.setattr("shared.config._ssm_get", mock_ssm)

        from shared.config import Settings

        cfg = Settings()
        with pytest.raises(RuntimeError, match="Alpaca"):
            cfg.get_alpaca_keys()

    def test_get_anthropic_key_returns_env_value(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
        from shared.config import Settings

        cfg = Settings()
        assert cfg.get_anthropic_key() == "test-anthropic-key"
