"""Position drill-down — detailed view of a single holding."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
import streamlit as st

from dashboard.fmt import fmt_currency, fmt_null


def render_position_detail(position: dict[str, Any], portfolio_value: float) -> None:
    """Render detailed position view.

    Called from portfolio.py when a ticker is selected.
    """
    ticker = position.get("ticker", "Unknown")

    st.subheader(f"Position Detail: {ticker}")

    # Back button
    if st.button("\u2190 Back to Portfolio"):
        st.session_state.pop("selected_position", None)
        st.rerun()

    st.divider()

    # Hero metrics
    c1, c2, c3, c4 = st.columns(4)

    shares = position.get("quantity", position.get("shares", 0))
    entry_price = position.get("cost_basis", position.get("entry_price", 0))
    market_value = position.get("market_value", 0)
    current_price = (market_value / shares) if shares > 0 else 0
    gain_loss = market_value - (entry_price * shares) if shares > 0 else 0
    gain_pct = ((current_price / entry_price - 1) * 100) if entry_price > 0 else 0
    position_pct = (market_value / portfolio_value * 100) if portfolio_value > 0 else 0

    c1.metric("Shares", f"{shares:,.0f}")
    c2.metric("Avg Cost", fmt_currency(entry_price))
    c3.metric("Market Value", fmt_currency(market_value))
    c4.metric("Gain/Loss", fmt_currency(gain_loss), delta=f"{gain_pct:+.1f}%")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Current Price", fmt_currency(current_price))
    c6.metric("Portfolio Weight", f"{position_pct:.1f}%")
    c7.metric("Sector", position.get("sector", fmt_null(None)))
    c8.metric("Industry", position.get("industry", fmt_null(None)))

    st.divider()

    tab_thesis, tab_chart, tab_analysis = st.tabs(
        ["Investment Thesis", "Price Chart", "Analysis Data"]
    )

    with tab_thesis:
        st.info(f"No investment thesis found for {ticker}.")

    with tab_chart:
        # Fetch price chart using yfinance
        try:
            import yfinance as yf

            hist = yf.Ticker(ticker).history(period="1y")
            if not hist.empty:
                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(
                        x=[d.strftime("%Y-%m-%d") for d in hist.index],
                        y=hist["Close"].tolist(),
                        mode="lines",
                        name=ticker,
                        line=dict(color="#6C9EFF", width=2),
                    )
                )
                # Add entry price reference line
                if entry_price > 0:
                    fig.add_hline(
                        y=entry_price,
                        line_dash="dash",
                        line_color="rgba(255,255,255,0.4)",
                        annotation_text=f"Entry: ${entry_price:.2f}",
                    )
                fig.update_layout(
                    template="omaha_oracle",
                    yaxis_title="Price ($)",
                    xaxis_title="Date",
                    height=400,
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info(f"No price history available for {ticker}.")
        except ImportError:
            st.info("yfinance not installed \u2014 price chart unavailable.")
        except Exception as e:
            st.warning(f"Could not fetch price data: {e}")

    with tab_analysis:
        # Show raw position data
        st.json(position)
