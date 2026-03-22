"""Options trading UI — chain viewer, order entry, and positions display.

Integrated into the Paper Trading page as a tab.
"""

from __future__ import annotations

import streamlit as st

from dashboard.alpaca_client import AlpacaClient
from dashboard.alpaca_errors import handle_alpaca_error
from dashboard.alpaca_models import OptionContractInfo
from dashboard.fmt import fmt_currency
from dashboard.options_chain import (
    contracts_to_dataframe,
    filter_contracts,
    get_expirations,
    get_strikes,
)
from dashboard.options_orders import (
    build_straddle_legs,
    build_strangle_legs,
    build_vertical_spread_legs,
    validate_option_order,
)


def render_options(client: AlpacaClient) -> None:
    """Render the full options trading section."""
    tab_chain, tab_order, tab_multi = st.tabs(
        ["Options Chain", "Single-Leg Order", "Multi-Leg Order"]
    )

    with tab_chain:
        _render_chain_viewer(client)
    with tab_order:
        _render_single_leg_order(client)
    with tab_multi:
        _render_multi_leg_order(client)


# ── Chain Viewer ─────────────────────────────────────────────────────────


def _render_chain_viewer(client: AlpacaClient) -> None:
    """Options chain lookup and display."""
    st.subheader("Options Chain")

    col1, col2 = st.columns([2, 1])
    with col1:
        underlying = st.text_input(
            "Underlying Symbol",
            placeholder="e.g. AAPL",
            key="options_underlying",
        ).upper().strip()
    with col2:
        fetch = st.button(
            "Load Chain",
            use_container_width=True,
            key="options_fetch",
            disabled=not underlying,
        )

    if not underlying:
        st.info("Enter an underlying symbol to view the options chain.")
        return

    if fetch or st.session_state.get("options_contracts"):
        contracts = _fetch_chain(client, underlying)
        if contracts is None:
            return
        st.session_state["options_contracts"] = contracts
    else:
        return

    contracts = st.session_state.get("options_contracts", [])
    if not contracts:
        st.warning(f"No options contracts found for {underlying}.")
        return

    # Filters
    expirations = get_expirations(contracts)
    strikes = get_strikes(contracts)

    col_exp, col_type, col_strike = st.columns(3)
    with col_exp:
        sel_exp = st.selectbox(
            "Expiration",
            ["All"] + expirations,
            key="options_exp_filter",
        )
    with col_type:
        sel_type = st.selectbox(
            "Type",
            ["All", "call", "put"],
            key="options_type_filter",
        )
    with col_strike:
        if len(strikes) >= 2:
            strike_range = st.slider(
                "Strike Range",
                min_value=min(strikes),
                max_value=max(strikes),
                value=(min(strikes), max(strikes)),
                key="options_strike_range",
            )
        else:
            strike_range = None

    filtered = filter_contracts(
        contracts,
        contract_type=sel_type if sel_type != "All" else None,
        expiration_date=sel_exp if sel_exp != "All" else None,
        strike_min=strike_range[0] if strike_range else None,
        strike_max=strike_range[1] if strike_range else None,
    )

    st.caption(f"Showing {len(filtered)} of {len(contracts)} contracts")
    if filtered:
        df = contracts_to_dataframe(filtered)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Strike": st.column_config.NumberColumn(format="$%.2f"),
                "Last Price": st.column_config.NumberColumn(format="$%.2f"),
                "Open Interest": st.column_config.NumberColumn(format="%d"),
            },
        )

        st.caption(
            "Greeks (delta, gamma, theta, vega, IV) require a market data subscription "
            "and are not available through the Alpaca Trading API."
        )


def _fetch_chain(
    client: AlpacaClient, underlying: str
) -> list[OptionContractInfo] | None:
    """Fetch options chain, returning None on error."""
    try:
        with st.spinner(f"Loading options chain for {underlying}..."):
            return client.get_option_contracts(underlying)
    except Exception as exc:
        handle_alpaca_error(exc)
        return None


# ── Single-Leg Order ─────────────────────────────────────────────────────


def _render_single_leg_order(client: AlpacaClient) -> None:
    """Single-leg option order entry form."""
    st.subheader("Single-Leg Option Order")

    with st.form("option_single_leg_form"):
        col1, col2 = st.columns(2)
        with col1:
            symbol = st.text_input(
                "Option Symbol",
                placeholder="e.g. AAPL250418C00200000",
                key="opt_sl_symbol",
            ).upper().strip()
            qty = st.number_input(
                "Contracts",
                min_value=1,
                value=1,
                step=1,
                key="opt_sl_qty",
            )
            side = st.selectbox("Side", ["buy", "sell"], key="opt_sl_side")
        with col2:
            order_type = st.selectbox(
                "Order Type",
                ["limit", "market"],
                key="opt_sl_type",
            )
            limit_price = None
            if order_type == "limit":
                limit_price = st.number_input(
                    "Limit Price",
                    min_value=0.01,
                    value=1.00,
                    step=0.05,
                    format="%.2f",
                    key="opt_sl_limit",
                )
            intent = st.selectbox(
                "Position Intent",
                ["buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close"],
                key="opt_sl_intent",
            )
            tif = st.selectbox(
                "Time in Force",
                ["day", "gtc"],
                key="opt_sl_tif",
            )

        submitted = st.form_submit_button("Review Order", use_container_width=True)

    if submitted:
        errors = validate_option_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            limit_price=limit_price,
        )
        if errors:
            for e in errors:
                st.error(e)
            return

        # Confirmation
        st.markdown("---")
        st.markdown("**Order Summary**")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.metric("Symbol", symbol)
        with col_b:
            st.metric("Contracts", qty)
        with col_c:
            if limit_price:
                st.metric("Limit Price", fmt_currency(limit_price, 2))
            else:
                st.metric("Type", "MARKET")

        st.caption(f"{side.upper()} | {intent.replace('_', ' ').title()} | TIF: {tif.upper()}")

        if st.button("Confirm & Submit", key="opt_sl_confirm", type="primary"):
            try:
                result = client.submit_option_order(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                    order_type=order_type,
                    time_in_force=tif,
                    position_intent=intent,
                    limit_price=limit_price,
                )
                st.success(
                    f"Order submitted — ID: {result.order_id[:8]}... "
                    f"Status: {result.status}"
                )
            except Exception as exc:
                handle_alpaca_error(exc)


# ── Multi-Leg Order ──────────────────────────────────────────────────────


def _render_multi_leg_order(client: AlpacaClient) -> None:
    """Multi-leg option order entry (spreads, straddles, strangles)."""
    st.subheader("Multi-Leg Option Order")

    strategy = st.selectbox(
        "Strategy",
        ["Vertical Spread", "Straddle", "Strangle"],
        key="opt_ml_strategy",
    )

    with st.form("option_multi_leg_form"):
        if strategy == "Vertical Spread":
            col1, col2 = st.columns(2)
            with col1:
                long_sym = st.text_input(
                    "Long Leg (buy)",
                    placeholder="e.g. AAPL250418C00200000",
                    key="opt_ml_long",
                ).upper().strip()
            with col2:
                short_sym = st.text_input(
                    "Short Leg (sell)",
                    placeholder="e.g. AAPL250418C00210000",
                    key="opt_ml_short",
                ).upper().strip()
        else:
            col1, col2 = st.columns(2)
            with col1:
                call_sym = st.text_input(
                    "Call Symbol",
                    placeholder="e.g. AAPL250418C00200000",
                    key="opt_ml_call",
                ).upper().strip()
            with col2:
                put_sym = st.text_input(
                    "Put Symbol",
                    placeholder="e.g. AAPL250418P00200000",
                    key="opt_ml_put",
                ).upper().strip()

        col_qty, col_type, col_price, col_tif = st.columns(4)
        with col_qty:
            qty = st.number_input(
                "Contracts",
                min_value=1,
                value=1,
                step=1,
                key="opt_ml_qty",
            )
        with col_type:
            order_type = st.selectbox(
                "Order Type",
                ["limit", "market"],
                key="opt_ml_type",
            )
        with col_price:
            limit_price = None
            if order_type == "limit":
                limit_price = st.number_input(
                    "Net Debit/Credit",
                    min_value=0.01,
                    value=1.00,
                    step=0.05,
                    format="%.2f",
                    key="opt_ml_limit",
                )
        with col_tif:
            tif = st.selectbox(
                "Time in Force",
                ["day", "gtc"],
                key="opt_ml_tif",
            )

        submitted = st.form_submit_button("Review Order", use_container_width=True)

    if submitted:
        # Build legs
        if strategy == "Vertical Spread":
            if not long_sym or not short_sym:
                st.error("Both long and short leg symbols are required.")
                return
            legs = build_vertical_spread_legs(long_sym, short_sym)
        elif strategy == "Straddle":
            if not call_sym or not put_sym:
                st.error("Both call and put symbols are required.")
                return
            legs = build_straddle_legs(call_sym, put_sym)
        else:  # Strangle
            if not call_sym or not put_sym:
                st.error("Both call and put symbols are required.")
                return
            legs = build_strangle_legs(call_sym, put_sym)

        # Confirmation
        st.markdown("---")
        st.markdown(f"**{strategy} Order Summary**")
        for i, leg in enumerate(legs):
            st.caption(
                f"Leg {i + 1}: {leg['side'].upper()} {leg['symbol']} "
                f"(ratio: {leg['ratio_qty']})"
            )

        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("Contracts", qty)
        with col_b:
            if limit_price:
                st.metric("Net Price", fmt_currency(limit_price, 2))
            else:
                st.metric("Type", "MARKET")

        if st.button("Confirm & Submit", key="opt_ml_confirm", type="primary"):
            try:
                result = client.submit_multi_leg_order(
                    legs=legs,
                    qty=qty,
                    order_type=order_type,
                    time_in_force=tif,
                    limit_price=limit_price,
                )
                st.success(
                    f"Multi-leg order submitted — ID: {result.order_id[:8]}... "
                    f"Status: {result.status}"
                )
            except Exception as exc:
                handle_alpaca_error(exc)
