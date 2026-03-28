"""Prompt Lab — A/B test analysis prompt variants."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"

_EDITABLE_PROMPTS = {
    "Moat Analysis": "moat_analysis.md",
    "Management Quality": "management_assessment.md",
    "Thesis Generation": "thesis_generation.md",
    "Owners Letter": "owners_letter.md",
    "Lesson Extraction": "lesson_extraction.md",
    "System Prompt Base": "system_prompt_base.md",
}


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    path = _PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"# Prompt file not found: {filename}"


def render() -> None:
    st.header("Prompt Lab")
    st.caption(
        "Compare and test prompt variants for analysis stages. "
        "Edit prompts side-by-side to experiment with improvements."
    )

    # Select which prompt to work with
    st.sidebar.subheader("Prompt Lab Settings")
    selected_prompt = st.sidebar.selectbox(
        "Analysis Stage",
        list(_EDITABLE_PROMPTS.keys()),
        key="prompt_lab_stage",
    )

    filename = _EDITABLE_PROMPTS[selected_prompt]
    current_prompt = _load_prompt(filename)

    tab_compare, tab_test, tab_history = st.tabs(
        ["Compare Variants", "Live Test", "Prompt Library"]
    )

    with tab_compare:
        st.subheader(f"Prompt Variants — {selected_prompt}")

        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("**Variant A (Current)**")
            variant_a = st.text_area(
                "Edit Variant A",
                value=current_prompt,
                height=400,
                key="variant_a",
                label_visibility="collapsed",
            )

        with col_b:
            st.markdown("**Variant B (Experimental)**")
            # Initialize variant B with the same content
            if "variant_b_init" not in st.session_state:
                st.session_state.variant_b_init = current_prompt
            variant_b = st.text_area(
                "Edit Variant B",
                value=st.session_state.variant_b_init,
                height=400,
                key="variant_b",
                label_visibility="collapsed",
            )

        # Show diff stats
        lines_a = variant_a.strip().split("\n")
        lines_b = variant_b.strip().split("\n")
        st.caption(
            f"Variant A: {len(lines_a)} lines, {len(variant_a)} chars | "
            f"Variant B: {len(lines_b)} lines, {len(variant_b)} chars"
        )

        if variant_a.strip() != variant_b.strip():
            st.info(f"Variants differ — {abs(len(lines_a) - len(lines_b))} line difference")

    with tab_test:
        st.subheader("Live Test")
        st.caption("Run both prompt variants against a ticker to compare outputs.")

        with st.form("test_form"):
            test_col1, test_col2 = st.columns(2)
            with test_col1:
                test_ticker = st.text_input("Ticker to test", placeholder="e.g., AAPL")
            with test_col2:
                test_tier = st.selectbox("Model tier", ["analysis", "bulk"], index=0)
            run_test = st.form_submit_button("Run A/B Test", width="stretch")

        if run_test and test_ticker:
            st.warning(
                "Live A/B testing requires API credits and an active Anthropic API key. "
                "Each test runs TWO Claude API calls."
            )

            try:
                from shared.llm_client import LLMClient

                client = LLMClient()

                col_res_a, col_res_b = st.columns(2)

                with col_res_a:
                    st.markdown("**Variant A Result**")
                    with st.spinner("Running Variant A..."):
                        try:
                            result_a = client.invoke(
                                system_prompt=variant_a,
                                user_prompt=(
                                    f"Analyze {test_ticker.upper()} for investment potential."
                                ),
                                tier=test_tier,
                            )
                            st.markdown(result_a)
                        except Exception as e:
                            st.error(f"Variant A failed: {e}")

                with col_res_b:
                    st.markdown("**Variant B Result**")
                    with st.spinner("Running Variant B..."):
                        try:
                            result_b = client.invoke(
                                system_prompt=variant_b,
                                user_prompt=(
                                    f"Analyze {test_ticker.upper()} for investment potential."
                                ),
                                tier=test_tier,
                            )
                            st.markdown(result_b)
                        except Exception as e:
                            st.error(f"Variant B failed: {e}")

            except ImportError:
                st.error("LLM client not available. Check your configuration.")
            except Exception as e:
                st.error(f"Test failed: {e}")

        elif run_test:
            st.warning("Please enter a ticker symbol.")

    with tab_history:
        st.subheader("Prompt Library")
        st.caption("View all available prompt templates.")

        for name, fname in _EDITABLE_PROMPTS.items():
            with st.expander(f"{name} ({fname})"):
                content = _load_prompt(fname)
                st.code(content, language="markdown")
                st.caption(f"{len(content)} characters, {len(content.split(chr(10)))} lines")

        # Also show the anti-style-drift guardrail
        with st.expander("Anti-Style-Drift Guardrail (prepended to all prompts)"):
            guardrail = _load_prompt("anti_style_drift_guardrail.md")
            st.code(guardrail, language="markdown")
