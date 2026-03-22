"""Owner's Letters page — archive with markdown rendering."""

from __future__ import annotations

import re

import streamlit as st

from dashboard.data import (
    DataLoadError,
    load_letter_content,
    load_letter_keys,
)


def _format_letter_key(key: str) -> str:
    """Turn an S3 key like ``letters/2026-Q1-owners-letter.md`` into
    ``Q1 2026 — Owner's Letter``."""
    name = key.replace("letters/", "").replace(".md", "")
    # Try to parse "YYYY-QN-..." pattern
    m = re.match(r"(\d{4})-(Q\d)(.*)", name)
    if m:
        year, quarter, rest = m.groups()
        label = rest.strip("-").replace("-", " ").title()
        return f"{quarter} {year} — {label}" if label else f"{quarter} {year}"
    return name


def render() -> None:
    """Render the Owner's Letters page."""
    st.title("Owner's Letters")
    st.caption(
        "Quarterly reports summarizing portfolio performance, "
        "key decisions, and lessons learned — inspired by "
        "Buffett's shareholder letters."
    )

    try:
        with st.spinner("Loading letter archive from S3..."):
            keys = load_letter_keys()
    except DataLoadError as exc:
        st.error(str(exc))
        return

    # ── Tier 1: Hero metrics ──
    n = len(keys)
    latest = keys[0].replace("letters/", "").replace(".md", "") if keys else "—"

    col1, col2, _, _ = st.columns(4, gap="large", vertical_alignment="bottom")
    with col1:
        st.metric(
            "Total Letters",
            n,
            help="Number of quarterly owner's letters on file.",
        )
    with col2:
        st.metric(
            "Latest Letter",
            latest,
            help="Most recent owner's letter.",
        )

    st.divider()

    if not keys:
        st.info(
            "No owner's letters on record yet. The first letter "
            "will be generated after the quarterly postmortem "
            "review completes."
        )
        return

    # Letter selector in sidebar
    selected = st.sidebar.selectbox(
        "Select letter",
        keys,
        format_func=_format_letter_key,
        help="Choose a quarterly letter to read. Most recent is selected by default.",
    )

    # ── Tier 2: Primary content in tabs ──
    tab_letter, tab_archive = st.tabs(["Current Letter", "Archive Index"])

    with tab_letter:
        if selected:
            try:
                with st.spinner("Rendering letter..."):
                    content = load_letter_content(selected)
                with st.container(border=True):
                    st.markdown(content)
            except DataLoadError as exc:
                st.error(str(exc))

    with tab_archive:
        for i, key in enumerate(keys):
            label = _format_letter_key(key)
            is_current = key == selected
            prefix = "**>>** " if is_current else ""
            st.write(f"{prefix}{i + 1}. {label}")
