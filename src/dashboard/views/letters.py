"""Owner's Letters page — archive with markdown rendering."""

from __future__ import annotations

import streamlit as st

from dashboard.data import load_letter_content, load_letter_keys


def render() -> None:
    """Render the Owner's Letters page."""
    st.title("Owner's Letters")

    keys = load_letter_keys()
    if not keys:
        st.info("No letters on record.")
        return

    selected = st.selectbox("Select letter", keys, format_func=lambda k: k.replace("letters/", ""))
    if selected:
        content = load_letter_content(selected)
        st.markdown(content)
