"""
Centralised configuration for Omaha Oracle.

Resolution order for every field:
  1. Environment variable (set directly or loaded from .env locally)
  2. AWS SSM Parameter Store at /omaha-oracle/{env}/{param}
  3. Pydantic default (where applicable)

The module exposes a single cached instance via `get_config()`.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import ClientError
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ssm_cache: dict[str, str] = {}
_log = logging.getLogger(__name__)


def _ssm_get(path: str, region: str) -> str | None:
    """Fetch a single SecureString/String from SSM; returns None on any error."""
    if path in _ssm_cache:
        return _ssm_cache[path]
    try:
        client = boto3.client("ssm", region_name=region)
        resp = client.get_parameter(Name=path, WithDecryption=True)
        value: str = resp["Parameter"]["Value"]
        _ssm_cache[path] = value
        return value
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code != "ParameterNotFound":
            _log.warning("SSM lookup failed for %s: %s", path, code)
        return None
    except Exception as exc:  # noqa: BLE001
        _log.warning("SSM lookup error for %s: %s", path, exc)
        return None


class Settings(BaseSettings):
    """Application-wide settings resolved from env vars with SSM fallback."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Core                                                                 #
    # ------------------------------------------------------------------ #
    environment: str = Field(default="dev", alias="ENVIRONMENT")
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ------------------------------------------------------------------ #
    # DynamoDB table names                                                 #
    # Defaulted to the canonical naming convention; override via env var  #
    # if the infra uses a different name in a given environment.          #
    # ------------------------------------------------------------------ #
    table_universe: str = Field(default="", alias="TABLE_UNIVERSE")
    table_companies: str = Field(default="", alias="TABLE_COMPANIES")
    table_financials: str = Field(default="", alias="TABLE_FINANCIALS")
    table_analysis: str = Field(default="", alias="TABLE_ANALYSIS")
    table_portfolio: str = Field(default="", alias="TABLE_PORTFOLIO")
    table_decisions: str = Field(default="", alias="TABLE_DECISIONS")
    table_trades: str = Field(default="", alias="TABLE_TRADES")
    table_macro: str = Field(default="", alias="TABLE_MACRO")
    table_cost_tracking: str = Field(default="", alias="TABLE_COST_TRACKING")
    table_config: str = Field(default="", alias="TABLE_CONFIG")
    table_watchlist: str = Field(default="", alias="TABLE_WATCHLIST")
    table_lessons: str = Field(default="", alias="TABLE_LESSONS")

    # ------------------------------------------------------------------ #
    # S3                                                                   #
    # ------------------------------------------------------------------ #
    s3_bucket: str = Field(default="", alias="S3_BUCKET")

    # ------------------------------------------------------------------ #
    # Anthropic                                                            #
    # ------------------------------------------------------------------ #
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # ------------------------------------------------------------------ #
    # LLM model IDs                                                        #
    # Defaults use versioned IDs so pricing lookups always match.         #
    # ------------------------------------------------------------------ #
    llm_analysis_model: str = Field(
        default="claude-opus-4-20250514",
        alias="LLM_ANALYSIS_MODEL",
    )
    llm_sonnet_model: str = Field(
        default="claude-sonnet-4-20250514",
        alias="LLM_SONNET_MODEL",
    )
    llm_fast_model: str = Field(
        default="claude-haiku-4-5-20251001",
        alias="LLM_FAST_MODEL",
    )

    # ------------------------------------------------------------------ #
    # LLM budget                                                           #
    # ------------------------------------------------------------------ #
    monthly_llm_budget_usd: float = Field(
        default=50.0,
        alias="MONTHLY_LLM_BUDGET_USD",
    )
    # Legacy env var (cents); kept for backwards compat
    monthly_llm_budget_cents: int = Field(
        default=0,
        alias="MONTHLY_LLM_BUDGET_CENTS",
    )

    # ------------------------------------------------------------------ #
    # Alpaca                                                               #
    # ------------------------------------------------------------------ #
    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_secret_key: str = Field(default="", alias="ALPACA_SECRET_KEY")
    alpaca_base_url: str = Field(
        default="https://paper-api.alpaca.markets",
        alias="ALPACA_BASE_URL",
    )

    # ------------------------------------------------------------------ #
    # FRED                                                                 #
    # ------------------------------------------------------------------ #
    fred_api_key: str = Field(default="", alias="FRED_API_KEY")

    # ------------------------------------------------------------------ #
    # SEC EDGAR                                                            #
    # ------------------------------------------------------------------ #
    sec_user_agent: str = Field(
        default="OmahaOracle contact@example.com",
        alias="SEC_USER_AGENT",
    )

    # ------------------------------------------------------------------ #
    # Alerts / SNS                                                         #
    # ------------------------------------------------------------------ #
    alert_email: str = Field(default="", alias="ALERT_EMAIL")
    sns_topic_arn: str = Field(default="", alias="SNS_TOPIC_ARN")

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #

    @field_validator("environment")
    @classmethod
    def _normalise_env(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"dev", "staging", "prod"}:
            raise ValueError(f"environment must be dev | staging | prod, got '{v}'")
        return v

    @model_validator(mode="after")
    def _apply_defaults(self) -> Settings:
        """
        Fill in convention-based defaults for table names and bucket that
        were not supplied via env var.  Also honour the legacy
        MONTHLY_LLM_BUDGET_CENTS variable.
        """
        env = self.environment

        table_defaults: dict[str, str] = {
            "table_universe": f"omaha-oracle-{env}-universe",
            "table_companies": f"omaha-oracle-{env}-companies",
            "table_financials": f"omaha-oracle-{env}-financials",
            "table_analysis": f"omaha-oracle-{env}-analysis",
            "table_portfolio": f"omaha-oracle-{env}-portfolio",
            "table_decisions": f"omaha-oracle-{env}-decisions",
            "table_trades": f"omaha-oracle-{env}-trades",
            "table_macro": f"omaha-oracle-{env}-macro",
            "table_cost_tracking": f"omaha-oracle-{env}-cost-tracking",
            "table_config": f"omaha-oracle-{env}-config",
            "table_watchlist": f"omaha-oracle-{env}-watchlist",
            "table_lessons": f"omaha-oracle-{env}-lessons",
        }
        for attr, default in table_defaults.items():
            if not getattr(self, attr):
                object.__setattr__(self, attr, default)

        if not self.s3_bucket:
            object.__setattr__(self, "s3_bucket", f"omaha-oracle-{env}-data")

        # Legacy cents → USD (cents wins when both set, avoids surprises)
        if self.monthly_llm_budget_cents > 0:
            object.__setattr__(
                self,
                "monthly_llm_budget_usd",
                self.monthly_llm_budget_cents / 100.0,
            )

        return self

    # ------------------------------------------------------------------ #
    # SSM-aware secret accessors                                           #
    # ------------------------------------------------------------------ #

    def _ssm_path(self, param: str) -> str:
        return f"/omaha-oracle/{self.environment}/{param}"

    def get_anthropic_key(self) -> str:
        """Return the Anthropic API key, falling back to SSM if not set."""
        if self.anthropic_api_key:
            return self.anthropic_api_key
        value = _ssm_get(self._ssm_path("anthropic-api-key"), self.aws_region)
        if value is None:
            raise RuntimeError(
                "Anthropic API key not found in environment or SSM "
                f"({self._ssm_path('anthropic-api-key')})"
            )
        return value

    def get_alpaca_keys(self) -> tuple[str, str]:
        """Return (api_key, secret_key) for Alpaca, falling back to SSM."""
        api_key = self.alpaca_api_key
        secret_key = self.alpaca_secret_key

        if not api_key:
            api_key = _ssm_get(self._ssm_path("alpaca-api-key"), self.aws_region) or ""
        if not secret_key:
            secret_key = (
                _ssm_get(self._ssm_path("alpaca-secret-key"), self.aws_region) or ""
            )

        if not api_key or not secret_key:
            raise RuntimeError(
                "Alpaca keys not found in environment or SSM "
                f"({self._ssm_path('alpaca-api-key')}, {self._ssm_path('alpaca-secret-key')})"
            )
        return api_key, secret_key

    def get_fred_key(self) -> str:
        """Return the FRED API key, falling back to SSM if not set."""
        if self.fred_api_key:
            return self.fred_api_key
        value = _ssm_get(self._ssm_path("fred-api-key"), self.aws_region)
        if value is None:
            raise RuntimeError(
                "FRED API key not found in environment or SSM "
                f"({self._ssm_path('fred-api-key')})"
            )
        return value

    # ------------------------------------------------------------------ #
    # Convenience                                                          #
    # ------------------------------------------------------------------ #

    def is_prod(self) -> bool:
        return self.environment == "prod"

    def monthly_budget_usd(self) -> float:
        """Canonical budget in USD regardless of which env var was set."""
        return self.monthly_llm_budget_usd

    def all_table_names(self) -> list[str]:
        """Return every DynamoDB table name this application manages."""
        return [
            self.table_universe,
            self.table_financials,
            self.table_analysis,
            self.table_portfolio,
            self.table_decisions,
            self.table_trades,
            self.table_macro,
            self.table_cost_tracking,
        ]

    def __repr__(self) -> str:
        return (
            f"Settings(environment={self.environment!r}, "
            f"aws_region={self.aws_region!r}, "
            f"llm_analysis_model={self.llm_analysis_model!r})"
        )


@lru_cache(maxsize=1)
def get_config(**_kwargs: Any) -> Settings:
    """
    Return the cached application-wide Settings singleton.

    ``**_kwargs`` is accepted (and ignored) so callers can force a fresh
    instance in tests by calling ``get_config.cache_clear()`` first.
    """
    return Settings()


# Backwards-compatible alias
get_settings = get_config
