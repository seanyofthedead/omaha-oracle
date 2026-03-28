"""Order Entry & Order History — Alpaca paper-trading orders.

Pure business-logic functions (validation, filtering, formatting) are
defined at module level for easy testing.  The ``render()`` function
builds the Streamlit UI and is called from ``paper_trading.py``.
"""

from __future__ import annotations

from dashboard.alpaca_client import AlpacaClient
from dashboard.alpaca_errors import classify_alpaca_error
from dashboard.alpaca_models import OrderInfo

# ── Constants ─────────────────────────────────────────────────────────────

_OPEN_STATUSES = frozenset(
    {"new", "partially_filled", "accepted", "pending_new", "pending_replace"}
)

# ── Validation ────────────────────────────────────────────────────────────


def validate_order_params(
    *,
    symbol: str,
    qty: str,
    side: str,
    order_type: str,
    time_in_force: str,
    limit_price: str,
    stop_price: str,
) -> list[str]:
    """Validate raw form inputs and return a list of error strings (empty = valid)."""
    errors: list[str] = []

    if not symbol.strip():
        errors.append("Symbol is required.")

    # Quantity
    try:
        q = float(qty)
        if q <= 0:
            errors.append("Quantity must be greater than zero.")
    except (ValueError, TypeError):
        errors.append("Quantity must be a valid number.")

    # Limit price (required for limit and stop_limit)
    if order_type in ("limit", "stop_limit"):
        if not limit_price.strip():
            errors.append("Limit price is required for this order type.")
        else:
            try:
                lp = float(limit_price)
                if lp <= 0:
                    errors.append("Limit price must be greater than zero.")
            except (ValueError, TypeError):
                errors.append("Limit price must be a valid number.")

    # Stop price (required for stop and stop_limit)
    if order_type in ("stop", "stop_limit"):
        if not stop_price.strip():
            errors.append("Stop price is required for this order type.")
        else:
            try:
                sp = float(stop_price)
                if sp <= 0:
                    errors.append("Stop price must be greater than zero.")
            except (ValueError, TypeError):
                errors.append("Stop price must be a valid number.")

    return errors


# ── Filtering ─────────────────────────────────────────────────────────────


def filter_orders_by_status(orders: list[OrderInfo], status: str) -> list[OrderInfo]:
    """Filter *orders* by *status* label.

    Accepted values: ``"all"``, ``"open"``, ``"filled"``, ``"canceled"``, ``"rejected"``.
    """
    if status == "all":
        return list(orders)
    if status == "open":
        return [o for o in orders if o.status in _OPEN_STATUSES]
    return [o for o in orders if o.status == status]


# ── Formatting ────────────────────────────────────────────────────────────

_DASH = "\u2014"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return _DASH
    return f"${value:,.2f}"


def format_order_row(order: OrderInfo) -> dict[str, object]:
    """Convert an ``OrderInfo`` into a display-ready dict for the order history table."""
    from dashboard.fmt import fmt_datetime

    return {
        "Symbol": order.symbol,
        "Side": order.side.upper(),
        "Qty": order.qty,
        "Type": order.order_type.upper(),
        "TIF": order.time_in_force.upper(),
        "Limit": _fmt_price(order.limit_price),
        "Stop": _fmt_price(order.stop_price),
        "Status": order.status.upper(),
        "Fill Price": _fmt_price(order.filled_avg_price),
        "Submitted": fmt_datetime(order.created_at),
        "Filled": fmt_datetime(order.filled_at) if order.filled_at else _DASH,
    }


# ── Safe client wrappers (return tuple, no st.* calls) ───────────────────


def submit_order_safe(
    client: AlpacaClient,
    *,
    symbol: str,
    qty: float,
    side: str,
    order_type: str,
    time_in_force: str,
    limit_price: float | None = None,
    stop_price: float | None = None,
) -> tuple[bool, OrderInfo | str]:
    """Submit an order, returning ``(True, OrderInfo)`` or ``(False, error_message)``."""
    try:
        result = client.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            time_in_force=time_in_force,
            limit_price=limit_price,
            stop_price=stop_price,
        )
        return True, result
    except Exception as exc:
        classified = classify_alpaca_error(exc)
        return False, classified.user_message


def cancel_order_safe(
    client: AlpacaClient,
    order_id: str,
) -> tuple[bool, str]:
    """Cancel an order, returning ``(True, message)`` or ``(False, error_message)``."""
    try:
        client.cancel_order(order_id)
        return True, f"Order {order_id[:8]}… canceled."
    except Exception as exc:
        classified = classify_alpaca_error(exc)
        return False, classified.user_message


# ── Streamlit UI ──────────────────────────────────────────────────────────

_ORDER_TYPES = ["market", "limit", "stop", "stop_limit"]
_SIDES = ["buy", "sell"]
_TIF_OPTIONS = ["day", "gtc", "ioc", "fok"]
_STATUS_FILTERS = ["all", "open", "filled", "canceled", "rejected"]


def render(client: AlpacaClient) -> None:
    """Render the order entry form and order history table."""
    import pandas as pd
    import streamlit as st

    tab_entry, tab_history = st.tabs(["Place Order", "Order History"])

    # ── Order Entry Tab ──────────────────────────────────────────────
    with tab_entry:
        _render_order_form(client, st)

    # ── Order History Tab ────────────────────────────────────────────
    with tab_history:
        _render_order_history(client, st, pd)


def _render_order_form(client: AlpacaClient, st) -> None:
    """Render the order entry form with confirmation step."""

    # Confirmation state
    if "pending_order" not in st.session_state:
        st.session_state.pending_order = None

    # If there's a pending order, show the confirmation dialog
    if st.session_state.pending_order is not None:
        _render_confirmation(client, st)
        return

    with st.form("order_entry_form"):
        col1, col2 = st.columns(2)
        with col1:
            symbol = (
                st.text_input(
                    "Symbol",
                    placeholder="e.g. AAPL",
                    help="Ticker symbol (stocks, ETFs, or crypto)",
                )
                .upper()
                .strip()
            )
            side = st.selectbox("Side", _SIDES, format_func=str.upper)
            order_type = st.selectbox(
                "Order Type",
                _ORDER_TYPES,
                format_func=lambda x: x.upper().replace("_", " "),
            )

        with col2:
            qty = st.text_input(
                "Quantity",
                placeholder="e.g. 10",
                help="Number of shares",
            )
            time_in_force = st.selectbox(
                "Time in Force",
                _TIF_OPTIONS,
                format_func=str.upper,
                help="DAY = end of day, GTC = until canceled, "
                "IOC = immediate or cancel, FOK = fill or kill",
            )
            limit_price = st.text_input(
                "Limit Price",
                placeholder="Required for limit/stop-limit",
                disabled=order_type not in ("limit", "stop_limit"),
            )

        stop_price = ""
        if order_type in ("stop", "stop_limit"):
            stop_price = st.text_input(
                "Stop Price",
                placeholder="Required for stop/stop-limit",
            )

        submitted = st.form_submit_button("Review Order", width="stretch")

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


def _render_confirmation(client: AlpacaClient, st) -> None:
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
        if st.button("Submit Order", type="primary", width="stretch"):
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
                    f"Order submitted — {result.symbol} {result.side.upper()} "
                    f"{result.qty} ({result.status.upper()})"
                )
            else:
                st.error(result)
    with col_cancel:
        if st.button("Cancel", width="stretch"):
            st.session_state.pending_order = None
            st.rerun()


def _render_order_history(client: AlpacaClient, st, pd) -> None:
    """Render the order history table with status filter and cancel buttons."""
    from dashboard.alpaca_errors import handle_alpaca_error

    col_filter, col_refresh = st.columns([3, 1])
    with col_filter:
        status_filter = st.selectbox(
            "Filter by status",
            _STATUS_FILTERS,
            format_func=str.upper,
            key="order_status_filter",
        )
    with col_refresh:
        st.write("")  # spacer
        st.button("Refresh", key="refresh_orders")

    try:
        orders = client.get_orders()
    except Exception as exc:
        handle_alpaca_error(exc)
        return

    filtered = filter_orders_by_status(orders, status_filter)

    if not filtered:
        st.info("No orders found." if not orders else f"No {status_filter} orders.")
        return

    # Sort by created_at descending
    filtered.sort(key=lambda o: o.created_at, reverse=True)

    # Build display rows
    rows = [format_order_row(o) for o in filtered]
    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True)

    # Cancel buttons for open orders
    open_orders = [o for o in filtered if o.status in _OPEN_STATUSES]
    if open_orders:
        st.subheader("Cancel Open Orders")
        for order in open_orders:
            label = (
                f"Cancel {order.symbol} {order.side.upper()} {order.qty} ({order.order_id[:8]}…)"
            )
            if st.button(label, key=f"cancel_{order.order_id}"):
                ok, msg = cancel_order_safe(client, order.order_id)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
