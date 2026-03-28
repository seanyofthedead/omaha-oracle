"""Order Management tab — view open orders and submit new ones.

Combines the order entry form with open/recent order monitoring.
All data comes from the Alpaca paper trading API.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard.alpaca_errors import handle_alpaca_error
from dashboard.alpaca_session import get_alpaca_client
from dashboard.views.order_entry import (
    _OPEN_STATUSES,
    cancel_order_safe,
    format_order_row,
    submit_order_safe,
    validate_order_params,
)

# ── Data fetching ────────────────────────────────────────────────────────


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_open_orders() -> list[dict]:
    client = get_alpaca_client()
    orders = client.get_orders(status="open", limit=100)
    return [
        {
            "order_id": o.order_id,
            "symbol": o.symbol,
            "side": o.side,
            "qty": o.qty,
            "order_type": o.order_type,
            "time_in_force": o.time_in_force,
            "status": o.status,
            "created_at": o.created_at,
            "filled_at": o.filled_at,
            "filled_avg_price": o.filled_avg_price,
            "limit_price": o.limit_price,
            "stop_price": o.stop_price,
        }
        for o in orders
    ]


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_recent_orders() -> list[dict]:
    client = get_alpaca_client()
    orders = client.get_orders(status="all", limit=20)
    return [
        {
            "order_id": o.order_id,
            "symbol": o.symbol,
            "side": o.side,
            "qty": o.qty,
            "order_type": o.order_type,
            "time_in_force": o.time_in_force,
            "status": o.status,
            "created_at": o.created_at,
            "filled_at": o.filled_at,
            "filled_avg_price": o.filled_avg_price,
            "limit_price": o.limit_price,
            "stop_price": o.stop_price,
        }
        for o in orders
    ]


# ── Order entry form ─────────────────────────────────────────────────────

_ORDER_TYPES = ["market", "limit", "stop", "stop_limit"]
_SIDES = ["buy", "sell"]
_TIF_OPTIONS = ["day", "gtc", "ioc", "fok"]


def _render_order_form() -> None:
    """Render the order entry form with confirmation step."""
    client = get_alpaca_client()

    if "pending_order" not in st.session_state:
        st.session_state.pending_order = None

    if st.session_state.pending_order is not None:
        _render_confirmation(client)
        return

    with st.form("order_entry_form"):
        col1, col2 = st.columns(2)
        with col1:
            symbol = st.text_input("Symbol", placeholder="e.g. AAPL").upper().strip()
            side = st.selectbox("Side", _SIDES, format_func=str.upper)
            order_type = st.selectbox(
                "Order Type",
                _ORDER_TYPES,
                format_func=lambda x: x.upper().replace("_", " "),
            )

        with col2:
            qty = st.text_input("Quantity", placeholder="e.g. 10")
            time_in_force = st.selectbox(
                "Time in Force",
                _TIF_OPTIONS,
                format_func=str.upper,
                help="DAY = end of day, GTC = until canceled",
            )
            limit_price = st.text_input(
                "Limit Price",
                placeholder="Required for limit/stop-limit",
                disabled=order_type not in ("limit", "stop_limit"),
            )

        stop_price = ""
        if order_type in ("stop", "stop_limit"):
            stop_price = st.text_input("Stop Price", placeholder="Required for stop/stop-limit")

        submitted = st.form_submit_button("Review Order", use_container_width=True)

    if submitted:
        errors = validate_order_params(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            time_in_force=time_in_force,
            limit_price=limit_price,
            stop_price=stop_price,
        )
        if errors:
            for e in errors:
                st.error(e)
        else:
            st.session_state.pending_order = {
                "symbol": symbol,
                "qty": float(qty),
                "side": side,
                "order_type": order_type,
                "time_in_force": time_in_force,
                "limit_price": float(limit_price) if limit_price.strip() else None,
                "stop_price": float(stop_price) if stop_price.strip() else None,
            }
            st.rerun()


def _render_confirmation(client) -> None:
    """Show order details and confirm/cancel buttons."""
    order = st.session_state.pending_order
    st.subheader("Confirm Order")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Symbol", order["symbol"])
        st.metric("Side", order["side"].upper())
    with col2:
        st.metric("Quantity", order["qty"])
        st.metric("Type", order["order_type"].upper().replace("_", " "))
    with col3:
        st.metric("Time in Force", order["time_in_force"].upper())
        if order.get("limit_price"):
            st.metric("Limit Price", f"${order['limit_price']:,.2f}")
        if order.get("stop_price"):
            st.metric("Stop Price", f"${order['stop_price']:,.2f}")

    col_confirm, col_cancel = st.columns(2)
    with col_confirm:
        if st.button("Submit Order", type="primary", use_container_width=True):
            ok, result = submit_order_safe(
                client,
                symbol=order["symbol"],
                qty=order["qty"],
                side=order["side"],
                order_type=order["order_type"],
                time_in_force=order["time_in_force"],
                limit_price=order.get("limit_price"),
                stop_price=order.get("stop_price"),
            )
            st.session_state.pending_order = None
            if ok:
                st.success(
                    f"Order submitted - {result.symbol} {result.side.upper()} "
                    f"{result.qty} ({result.status.upper()})"
                )
                _fetch_open_orders.clear()
                _fetch_recent_orders.clear()
            else:
                st.error(result)
    with col_cancel:
        if st.button("Cancel", use_container_width=True):
            st.session_state.pending_order = None
            st.rerun()


# ── Render ───────────────────────────────────────────────────────────────


def render() -> None:
    """Render the Order Management tab."""
    st.header("Order Management")

    tab_entry, tab_open, tab_recent = st.tabs(["Place Order", "Open Orders", "Recent Orders"])

    with tab_entry:
        _render_order_form()

    with tab_open:
        try:
            open_orders = _fetch_open_orders()
        except Exception as exc:
            handle_alpaca_error(exc)
            return

        if not open_orders:
            st.info("No open orders.")
        else:
            from dashboard.alpaca_models import OrderInfo

            order_objects = [OrderInfo(**{k: v for k, v in o.items()}) for o in open_orders]
            rows = [format_order_row(o) for o in order_objects]
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # Cancel buttons
            st.subheader("Cancel Orders")
            client = get_alpaca_client()
            for o in open_orders:
                if o["status"] in _OPEN_STATUSES:
                    label = (
                        f"Cancel {o['symbol']} {o['side'].upper()} "
                        f"{o['qty']} ({o['order_id'][:8]}...)"
                    )
                    if st.button(label, key=f"cancel_{o['order_id']}"):
                        ok, msg = cancel_order_safe(client, o["order_id"])
                        if ok:
                            st.success(msg)
                            _fetch_open_orders.clear()
                            st.rerun()
                        else:
                            st.error(msg)

    with tab_recent:
        try:
            recent = _fetch_recent_orders()
        except Exception as exc:
            handle_alpaca_error(exc)
            return

        if not recent:
            st.info("No recent orders.")
        else:
            from dashboard.alpaca_models import OrderInfo

            order_objects = [OrderInfo(**{k: v for k, v in o.items()}) for o in recent]
            rows = [format_order_row(o) for o in order_objects]
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
