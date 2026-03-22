"""Paper Trading page — Alpaca paper-trading integration hub.

Serves as the entry point for all paper-trading features.  Shows the
auth component and, once connected, the account overview, portfolio
positions, order entry, and watchlists.  Additional sub-features
(options, analytics) will be added by other worktrees.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.alpaca_auth import render_alpaca_auth
from dashboard.alpaca_client import AlpacaClient
from dashboard.alpaca_errors import handle_alpaca_error
from dashboard.views import order_entry
from dashboard.views.account_portfolio import (
    render_account_overview,
    render_positions_table,
)
from dashboard.views.analytics import render_analytics
from dashboard.watchlist_manager import (
    add_symbol,
    create_watchlist,
    delete_watchlist,
    fetch_quotes,
    get_watchlists,
    remove_symbol,
    rename_watchlist,
)


def render() -> None:
    """Render the Paper Trading page."""
    st.title("Paper Trading")
    st.caption("Alpaca paper-trading — practice trades with virtual money, real market data.")

    client = render_alpaca_auth()

    if client is None:
        st.info(
            "Enter your Alpaca paper-trading API keys above to get started. "
            "Keys are stored only in your browser session and never saved to disk."
        )
        return

    st.divider()

    render_account_overview(client)

    st.divider()

    render_positions_table(client)

    st.divider()

    order_entry.render(client)

    st.divider()

    _render_watchlists(client)

    st.divider()

    render_analytics(client)


# ── Watchlists tab ────────────────────────────────────────────────────────


def _render_watchlists(client: AlpacaClient) -> None:
    """Render the full watchlists management UI."""
    st.subheader("Watchlists")

    # Fetch watchlists
    try:
        watchlists = get_watchlists(client)
    except Exception as exc:
        handle_alpaca_error(exc)
        return

    # ── Create / Rename / Delete controls ─────────────────────────────
    col_create, col_rename, col_delete = st.columns(3)

    with col_create:
        with st.popover("New Watchlist", use_container_width=True):
            new_name = st.text_input("Name", key="wl_new_name", placeholder="e.g. Tech Stocks")
            new_symbols = st.text_input(
                "Initial symbols (optional)",
                key="wl_new_symbols",
                placeholder="AAPL, MSFT, GOOG",
            )
            if st.button("Create", key="wl_create_btn", use_container_width=True):
                if not new_name or not new_name.strip():
                    st.error("Name is required.")
                else:
                    try:
                        syms = (
                            [s.strip().upper() for s in new_symbols.split(",") if s.strip()]
                            if new_symbols
                            else []
                        )
                        create_watchlist(client, new_name.strip(), syms)
                        st.success(f"Created '{new_name.strip()}'")
                        st.rerun()
                    except ValueError as ve:
                        st.error(str(ve))
                    except Exception as exc:
                        handle_alpaca_error(exc)

    with col_rename:
        with st.popover("Rename", use_container_width=True):
            if not watchlists:
                st.info("No watchlists to rename.")
            else:
                wl_to_rename = st.selectbox(
                    "Watchlist",
                    watchlists,
                    format_func=lambda w: w.name,
                    key="wl_rename_select",
                )
                rename_val = st.text_input("New name", key="wl_rename_name")
                if st.button("Rename", key="wl_rename_btn", use_container_width=True):
                    if not rename_val or not rename_val.strip():
                        st.error("Name is required.")
                    else:
                        try:
                            rename_watchlist(
                                client, wl_to_rename.watchlist_id, rename_val.strip()
                            )
                            st.success(f"Renamed to '{rename_val.strip()}'")
                            st.rerun()
                        except ValueError as ve:
                            st.error(str(ve))
                        except Exception as exc:
                            handle_alpaca_error(exc)

    with col_delete:
        with st.popover("Delete", use_container_width=True):
            if not watchlists:
                st.info("No watchlists to delete.")
            else:
                wl_to_delete = st.selectbox(
                    "Watchlist",
                    watchlists,
                    format_func=lambda w: w.name,
                    key="wl_delete_select",
                )
                st.warning(f"This will permanently delete '{wl_to_delete.name}'.")
                if st.button(
                    "Delete", key="wl_delete_btn", type="primary", use_container_width=True
                ):
                    try:
                        delete_watchlist(client, wl_to_delete.watchlist_id)
                        st.success(f"Deleted '{wl_to_delete.name}'")
                        st.rerun()
                    except Exception as exc:
                        handle_alpaca_error(exc)

    st.divider()

    # ── Watchlist selector + content ──────────────────────────────────
    if not watchlists:
        st.info("No watchlists yet. Create one above to get started.")
        return

    selected = st.selectbox(
        "Select watchlist",
        watchlists,
        format_func=lambda w: f"{w.name} ({len(w.symbols)} symbols)",
        key="wl_active_select",
    )

    # ── Add / Remove symbol ───────────────────────────────────────────
    add_col, remove_col, _ = st.columns([1, 1, 2])

    with add_col:
        with st.popover("Add Symbol", use_container_width=True):
            sym_input = st.text_input(
                "Symbol", key="wl_add_sym", placeholder="e.g. AAPL"
            )
            if st.button("Add", key="wl_add_btn", use_container_width=True):
                if not sym_input or not sym_input.strip():
                    st.error("Enter a symbol.")
                else:
                    try:
                        add_symbol(client, selected.watchlist_id, sym_input.strip())
                        st.success(f"Added {sym_input.strip().upper()}")
                        st.rerun()
                    except ValueError as ve:
                        st.error(str(ve))
                    except Exception as exc:
                        handle_alpaca_error(exc)

    with remove_col:
        with st.popover("Remove Symbol", use_container_width=True):
            if not selected.symbols:
                st.info("No symbols to remove.")
            else:
                sym_to_remove = st.selectbox(
                    "Symbol", selected.symbols, key="wl_remove_sym_select"
                )
                if st.button("Remove", key="wl_remove_btn", use_container_width=True):
                    try:
                        remove_symbol(client, selected.watchlist_id, sym_to_remove)
                        st.success(f"Removed {sym_to_remove}")
                        st.rerun()
                    except Exception as exc:
                        handle_alpaca_error(exc)

    # ── Quote table ───────────────────────────────────────────────────
    if not selected.symbols:
        st.info(f"'{selected.name}' is empty. Add symbols above.")
        return

    quotes = fetch_quotes(selected.symbols, ttl_seconds=30)

    rows = []
    for sym in selected.symbols:
        q = quotes.get(sym, {})
        rows.append(
            {
                "Symbol": sym,
                "Last Price": q.get("price"),
                "Change": q.get("change"),
                "Change %": q.get("change_pct"),
                "Volume": q.get("volume"),
            }
        )

    df = pd.DataFrame(rows)
    tbl_height = min(len(rows) * 35 + 38, 400)

    column_config = {
        "Symbol": st.column_config.TextColumn("Symbol", width="small"),
        "Last Price": st.column_config.NumberColumn("Last Price", format="$%.2f"),
        "Change": st.column_config.NumberColumn("Change", format="$%.2f"),
        "Change %": st.column_config.NumberColumn("Change %", format="%.2f%%"),
        "Volume": st.column_config.NumberColumn("Volume", format="%d"),
    }

    with st.container(border=True):
        st.dataframe(
            df,
            column_config=column_config,
            use_container_width=True,
            hide_index=True,
            height=tbl_height,
        )
