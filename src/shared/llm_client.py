"""
Anthropic LLM client for Omaha Oracle.

Responsibilities
----------------
* Route calls to the right Claude model based on a semantic *tier*.
* Enforce the monthly spend budget via CostTracker (skipped for "thesis"
  tier so a high-conviction write-up is never silently suppressed).
* Prepend a style-drift guardrail to every system prompt so that even
  a caller-supplied prompt cannot accidentally enable momentum trading,
  technical analysis, market-timing, short-selling, options, or crypto.
* Retry transient rate-limit / API errors with exponential back-off.
* Optionally parse the model response as JSON (strips markdown fences).
* Log all usage to DynamoDB via CostTracker.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Literal

import anthropic

from shared.config import get_config
from shared.cost_tracker import CostTracker
from shared.logger import get_logger

_log = get_logger(__name__)

# ------------------------------------------------------------------ #
# Public types                                                        #
# ------------------------------------------------------------------ #

Tier = Literal["thesis", "analysis", "bulk"]


class BudgetExhaustedError(Exception):
    """Raised when the monthly LLM budget has been fully consumed."""


# ------------------------------------------------------------------ #
# Constants                                                           #
# ------------------------------------------------------------------ #

_MAX_RETRIES: int = 3
_BASE_DELAY: float = 2.0  # seconds; doubles on each retry

# Prepended verbatim to every system prompt, regardless of caller input.
# The double-newline separator keeps it visually distinct from the
# caller's actual instruction when debugging prompts.
_GUARDRAIL: str = (
    "You are a disciplined Graham-Dodd-Buffett value-investing analyst. "
    "You MUST NOT discuss, recommend, or reference any of the following: "
    "momentum trading, technical analysis (charts, moving averages, RSI, "
    "MACD, candlesticks, etc.), market timing, short selling, put/call "
    "options or any derivatives, leveraged/inverse ETFs, or "
    "cryptocurrency / digital assets. "
    "If asked about any prohibited topic, decline and redirect to "
    "fundamental analysis."
    "\n\n"
)

_JSON_INSTRUCTION: str = (
    "\n\nYou MUST respond with a single valid JSON object only. "
    "Do not include any text, explanation, or markdown formatting outside "
    "the JSON object. The response must be parseable by json.loads()."
)

# ------------------------------------------------------------------ #
# LLMClient                                                           #
# ------------------------------------------------------------------ #


class LLMClient:
    """
    Thin wrapper around the Anthropic ``messages`` API.

    Parameters
    ----------
    cost_tracker:
        Optional pre-constructed ``CostTracker`` instance.  When *None* a
        new one is created from settings.  Inject a mock in tests.
    """

    def __init__(self, cost_tracker: CostTracker | None = None) -> None:
        cfg = get_config()
        self._client = anthropic.Anthropic(api_key=cfg.get_anthropic_key())
        self._tracker = cost_tracker or CostTracker()
        self._tier_model: dict[Tier, str] = {
            "thesis":   cfg.llm_analysis_model,  # Opus
            "analysis": cfg.llm_sonnet_model,     # Sonnet
            "bulk":     cfg.llm_fast_model,       # Haiku
        }

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def invoke(
        self,
        tier: Tier,
        user_prompt: str,
        system_prompt: str = "",
        module: str = "unknown",
        ticker: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.2,
        require_json: bool = False,
    ) -> dict[str, Any]:
        """
        Call the appropriate Claude model for *tier* and return a result dict.

        Parameters
        ----------
        tier:
            "thesis" → Opus (budget check bypassed).
            "analysis" → Sonnet.
            "bulk" → Haiku.
        user_prompt:
            The human-turn message sent to the model.
        system_prompt:
            Caller-supplied system instruction.  The anti-style-drift
            guardrail is always prepended; a JSON enforcement instruction
            is appended when *require_json* is True.
        module:
            Logical owner of this call (used for cost attribution).
        ticker:
            Equity ticker symbol, or "" if not applicable.
        max_tokens:
            Maximum tokens in the completion.
        temperature:
            Sampling temperature (0 – 1).
        require_json:
            When True, the system prompt is augmented to demand a JSON
            response, the output is parsed, and ``result["content"]`` is
            a ``dict``.

        Returns
        -------
        dict with keys:
            content      – parsed ``dict`` (require_json=True) or ``str``
            model        – model ID that was used
            input_tokens – int
            output_tokens – int
            cost_usd     – float

        Raises
        ------
        BudgetExhaustedError
            When the monthly budget is exhausted and tier != "thesis".
        anthropic.RateLimitError / anthropic.APIError
            Re-raised after all retries are exhausted.
        json.JSONDecodeError
            When require_json=True and the model returns unparseable JSON
            even after fence-stripping.
        """
        model = self._tier_model[tier]
        effective_system = self._build_system_prompt(system_prompt, require_json)

        if tier != "thesis":
            self._assert_budget()

        raw_text, usage = self._call_with_retry(
            model=model,
            user_prompt=user_prompt,
            system_prompt=effective_system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        cost_usd = self._tracker.log_usage(
            model=model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            module=module,
            ticker=ticker,
        )

        content: dict[str, Any] | str
        if require_json:
            content = _parse_json(raw_text)
        else:
            content = raw_text

        return {
            "content": content,
            "model": model,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cost_usd": cost_usd,
        }

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _build_system_prompt(self, caller_prompt: str, require_json: bool) -> str:
        prompt = _GUARDRAIL + caller_prompt
        if require_json:
            prompt += _JSON_INSTRUCTION
        return prompt

    def _assert_budget(self) -> None:
        status = self._tracker.check_budget()
        if status["exhausted"]:
            raise BudgetExhaustedError(
                f"Monthly LLM budget exhausted: "
                f"${status['spent_usd']:.2f} spent of "
                f"${status['budget_usd']:.2f} budget."
            )

    def _call_with_retry(
        self,
        model: str,
        user_prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, dict[str, int]]:
        """
        Call the Anthropic API with exponential back-off retry.

        Returns ``(response_text, {"input_tokens": int, "output_tokens": int})``.
        """
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                response = self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                text = _extract_text(response)
                usage = {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
                _log.debug(
                    "Anthropic call succeeded",
                    extra={
                        "model": model,
                        "attempt": attempt,
                        "input_tokens": usage["input_tokens"],
                        "output_tokens": usage["output_tokens"],
                    },
                )
                return text, usage

            except (anthropic.RateLimitError, anthropic.APIError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = _BASE_DELAY * (2**attempt)
                    _log.warning(
                        "Anthropic API error — retrying",
                        extra={
                            "attempt": attempt + 1,
                            "max_retries": _MAX_RETRIES,
                            "delay_s": delay,
                            "error": str(exc),
                            "model": model,
                        },
                    )
                    time.sleep(delay)
                else:
                    _log.error(
                        "Anthropic API error — all retries exhausted",
                        extra={"error": str(exc), "model": model},
                    )

        raise last_exc  # type: ignore[misc]


# ------------------------------------------------------------------ #
# Module-level utilities                                              #
# ------------------------------------------------------------------ #


def _extract_text(response: anthropic.types.Message) -> str:
    """Return the text from the first TextBlock in the response."""
    for block in response.content:
        if hasattr(block, "text"):
            return str(block.text)
    return ""


def _parse_json(text: str) -> dict[str, Any]:
    """
    Parse *text* as JSON, stripping optional markdown code fences first.

    Handles patterns like:
        ```json\\n{...}\\n```
        ```\\n{...}\\n```
        {... bare JSON ...}
    """
    cleaned = text.strip()

    # Strip opening fence (```json or ```)
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned, flags=re.IGNORECASE)
    # Strip closing fence
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    cleaned = cleaned.strip()
    result: dict[str, Any] = json.loads(cleaned)
    return result
