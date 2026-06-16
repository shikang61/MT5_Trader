"""Live performance + strategy-breakdown dashboard for the MT5 portfolio bot.

    uv run --with streamlit --with plotly streamlit run dashboard/app.py

Pulls broker truth + the decision log off the VPS each refresh (read-only SSH).
If SSH times out the NSG pin went stale: bash scripts/provision_azure.sh ip
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd  # noqa: F401 — eager import: polars .to_pandas()/Styler runs in the
import plotly.graph_objects as go  # fragment thread, where a lazy first import races (circular init)
import polars as pl
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))   # import sibling sources.py
import sources as S

# --- palette (gold = XAU, the asset; green/red kept conventional for P&L) ------
BG, PANEL, INK, MUTED = "#0E1116", "#161B22", "#E6E1D6", "#8A8578"
GOLD, UP, DOWN = "#C9A24B", "#3FB950", "#E5534B"

st.set_page_config(page_title="MT5 · XAUUSD desk", layout="wide", page_icon="◆")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=JetBrains+Mono:wght@400;500;700&display=swap');
#MainMenu, footer, header[data-testid="stHeader"] {visibility:hidden; height:0;}
.block-container {padding-top:1.2rem; max-width:1500px;}
html, body, [class*="css"] {font-family:'Space Grotesk',sans-serif;}
.mono {font-family:'JetBrains Mono',monospace; font-variant-numeric:tabular-nums;}

/* header band */
.hdr {display:flex; justify-content:space-between; align-items:baseline;
      border-bottom:1px solid #2A2F38; padding-bottom:.5rem; margin-bottom:1.1rem;}
.hdr-mark {font-weight:700; font-size:1.05rem; letter-spacing:.14em; text-transform:uppercase;}
.gold {color:#C9A24B;}
.hdr-status {font-family:'JetBrains Mono',monospace; font-size:.78rem; color:#8A8578; letter-spacing:.1em;}
.pulse {display:inline-block; width:7px; height:7px; border-radius:50%; background:#3FB950;
        margin-right:.45rem; box-shadow:0 0 0 0 rgba(63,185,80,.6); animation:p 1.8s infinite;}
@keyframes p {0%{box-shadow:0 0 0 0 rgba(63,185,80,.55);}70%{box-shadow:0 0 0 7px rgba(63,185,80,0);}100%{box-shadow:0 0 0 0 rgba(63,185,80,0);}}
@media (prefers-reduced-motion){.pulse{animation:none;}}

/* equity hero — the signature */
.hero {padding:.2rem 0 .3rem;}
.hero-label {font-size:.72rem; letter-spacing:.22em; text-transform:uppercase; color:#8A8578;}
.hero-val {font-family:'JetBrains Mono',monospace; font-variant-numeric:tabular-nums;
           font-size:3.4rem; font-weight:500; line-height:1.05; color:#E6E1D6;
           display:inline-block; border-bottom:2px solid #C9A24B; padding-bottom:.1rem;}
.hero-sub {font-family:'JetBrains Mono',monospace; font-size:.95rem; margin-top:.5rem; letter-spacing:.02em;}
.up {color:#3FB950;} .down {color:#E5534B;} .flat {color:#8A8578;}

/* section eyebrows */
h3 {font-size:.74rem !important; letter-spacing:.2em; text-transform:uppercase;
    color:#8A8578 !important; font-weight:500 !important; border-left:2px solid #C9A24B;
    padding-left:.6rem !important; margin:.4rem 0 .5rem !important;}

/* metric cards */
[data-testid="stMetric"] {background:#161B22; border:1px solid #232A33; border-radius:8px;
                          padding:.7rem .9rem;}
[data-testid="stMetricLabel"] p {font-size:.68rem !important; letter-spacing:.16em; text-transform:uppercase; color:#8A8578 !important;}
[data-testid="stMetricValue"] {font-family:'JetBrains Mono',monospace !important; font-variant-numeric:tabular-nums; font-size:1.5rem !important;}

/* tables */
[data-testid="stDataFrame"] {font-family:'JetBrains Mono',monospace; border:1px solid #232A33; border-radius:8px;}
.stPlotlyChart {border:1px solid #232A33; border-radius:8px; background:#12161C; padding:.3rem;}
</style>
""", unsafe_allow_html=True)

# --- controls -----------------------------------------------------------------
since = st.sidebar.text_input("deals since (UTC date)", "2026-06-12")
refresh = st.sidebar.number_input("auto-refresh (s)", 10, 300, 30)
start_equity = st.sidebar.number_input("account start equity", value=100_000.0, step=1000.0)
kill_pct = st.sidebar.number_input("kill switch (% of round start)", value=4.0, step=0.5)
st.sidebar.caption("read-only SSH to mt5-vps each refresh")


def _cls(x: float) -> str:
    return "up" if x > 0 else "down" if x < 0 else "flat"


def _pnl_style(df: pl.DataFrame, cols: list[str]):
    """polars -> pandas Styler with green/red P&L cells, mono, 2dp; win% shaded
    against a 50% coin-flip."""
    pdf = df.to_pandas()
    num = [c for c in pdf.columns if pdf[c].dtype.kind in "fi"]
    sty = pdf.style.map(
        lambda v: f"color:{UP}" if v > 0 else f"color:{DOWN}" if v < 0 else f"color:{MUTED}",
        subset=[c for c in cols if c in pdf.columns])
    if "win%" in pdf.columns:
        sty = sty.map(lambda v: f"color:{UP}" if v >= 50 else f"color:{DOWN}", subset=["win%"])
    return sty.format({c: "{:,.2f}" for c in num if pdf[c].dtype.kind == "f"})


@st.fragment(run_every=refresh)
def view() -> None:
    now = f"{datetime.now(timezone.utc):%H:%M:%S}"
    st.markdown(
        f'<div class="hdr"><div class="hdr-mark">MT5 <span class="gold">◆</span> XAUUSD DESK</div>'
        f'<div class="hdr-status"><span class="pulse"></span>LIVE · {now} UTC</div></div>',
        unsafe_allow_html=True)

    if os.environ.get("MT5_DASH_DEMO"):          # offline preview from cached pull
        data = json.load(open(S.CACHE / "probe.json"))
        log = S.CACHE / "portfolio.jsonl"
    else:
        try:
            data = S.probe(since)
            log = S.pull_log()
        except S.SSHError as e:
            st.error(f"VPS unreachable — {e}")
            return

    acc, eq = data["account"], data["account"]["equity"]
    eqc = S.equity_curve(log)
    today = eqc.filter(pl.col("ts").dt.date() == eqc["ts"].max().date())
    day_pl = (eq / today["equity"][0] - 1) if len(today) else 0.0
    floating = eq - acc["balance"]

    hero, side = st.columns([2, 3])
    with hero:
        st.markdown(
            f'<div class="hero"><div class="hero-label">Account equity</div>'
            f'<div class="hero-val mono">{eq:,.2f}</div>'
            f'<div class="hero-sub {_cls(day_pl)}">{day_pl*100:+.2f}% today'
            f'<span class="flat"> · </span><span class="{_cls(floating)}">{floating:+,.2f} floating</span></div></div>',
            unsafe_allow_html=True)
    with side:
        m = st.columns(3)
        m[0].metric("balance", f"{acc['balance']:,.2f}")
        m[1].metric("vs start", f"{(eq/start_equity-1)*100:+.2f}%")
        m[2].metric("free margin", f"{acc['margin_free']:,.0f}")

    # equity curve — gold line on the panel
    fig = go.Figure(go.Scatter(
        x=eqc["ts"].to_list(), y=eqc["equity"].to_list(), mode="lines",
        line=dict(color=GOLD, width=1.6), fill="tozeroy",
        fillcolor="rgba(201,162,75,0.06)", hovertemplate="%{y:,.2f}<extra></extra>"))
    lo, hi = eqc["equity"].min(), eqc["equity"].max()
    pad = max((hi - lo) * 0.15, 5)
    fig.add_hline(y=start_equity, line=dict(color=MUTED, width=0.8, dash="dot"))
    fig.update_layout(
        height=230, margin=dict(l=0, r=0, t=6, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=MUTED, family="JetBrains Mono", size=11),
        xaxis=dict(showgrid=False, color=MUTED),
        yaxis=dict(range=[lo - pad, hi + pad], gridcolor="#1C222B", color=MUTED, side="right"))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # P&L blotter
    left, right = st.columns(2)
    with left:
        st.subheader("realized P&L · by strategy (broker)")
        st.dataframe(_pnl_style(S.deals_table(data), ["net", "gross", "swap"]),
                     hide_index=True, use_container_width=True)
    with right:
        st.subheader("gross attribution · by book (log)")
        st.dataframe(_pnl_style(S.attribution_table(log), ["gross_pnl"]),
                     hide_index=True, use_container_width=True)

    # positions + signal/regime
    left, right = st.columns([3, 2])
    with left:
        st.subheader("open positions")
        pdf = pl.DataFrame(data["positions"]) if data["positions"] else pl.DataFrame()
        st.dataframe(_pnl_style(pdf, ["profit"]) if len(pdf) else pdf,
                     hide_index=True, use_container_width=True)
    with right:
        st.subheader("ratio regime")
        reg = S.regime_state(data["bars"])
        rc = GOLD if reg == "RANGE" else UP if reg == "TREND_UP" else DOWN
        st.markdown(f'<div class="hero-val mono" style="font-size:1.6rem;border:none;color:{rc}">{reg}</div>',
                    unsafe_allow_html=True)

    st.subheader("latest book signals")
    st.dataframe(S.latest_signals(log), hide_index=True, use_container_width=True)

    # fill quality
    st.subheader("fill quality · live vs completed-bar backtest (≈100% = forming-bar fix holds)")
    fq = S.fill_quality(log, data["bars"])
    if len(fq):
        sty = fq.to_pandas().style.map(
            lambda v: f"color:{UP}" if v >= 99 else f"color:{DOWN}" if v < 90 else f"color:{GOLD}",
            subset=["agree_pct"]).format({"agree_pct": "{:.1f}", "live_active%": "{:.1f}", "bt_active%": "{:.1f}"})
        st.dataframe(sty, hide_index=True, use_container_width=True)
    else:
        st.caption("no overlapping bars yet")


view()
