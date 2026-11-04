"""📊 Diagram – stapeldiagram med indikatorer och robotens köp och sälj.

Visas som en egen sida i menyn (Streamlit hittar automatiskt filer i mappen pages/).
"""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
for _m in [m for m in sys.modules if m == "trading_platform" or m.startswith("trading_platform.")]:
    del sys.modules[_m]

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from trading_platform.brokers.paper import PaperBroker
from trading_platform.config import load_settings
from trading_platform.data import YahooResearchProvider
from trading_platform.indicators import enrich
from trading_platform.risk import usd_to_sek

NY = ZoneInfo("America/New_York")
st.set_page_config(page_title="Diagram – US Trading Platform", layout="wide")
provider = YahooResearchProvider()
import os  # noqa: E402
if os.environ.get("TP_FAKE_DATA"):  # automatiska tester
    from trading_platform.fakedata import FakeProvider
    provider = FakeProvider()
settings = load_settings()

def _robot_account():
    """Robotens konto direkt från GitHub (färskast), annars webbsidans egen kopia."""
    import tempfile
    import requests
    local = Path(tempfile.gettempdir()) / "robot_auto_account.json"
    try:
        r = requests.get("https://raw.githubusercontent.com/danielsorting/us_trading_platform_v2/main/paper/"
                         "auto_account.json", params={"t": datetime.now().strftime("%Y%m%d%H%M")}, timeout=10)
        r.raise_for_status()
        local.write_bytes(r.content)
        return local
    except Exception:
        return ROOT / "paper" / "auto_account.json"


ACCOUNTS = {
    "🤖 Automatisk handel (GitHub)": _robot_account(),
    "🧪 Simulerad handel (webbsidan)": ROOT / "paper_account.json",
}
INTERVALS = {
    "5 minuter (senaste 5 dagarna)": ("5d", "5m"),
    "Dag (1 år)": ("1y", "1d"),
    "Dag (5 år)": ("5y", "1d"),
}


@st.cache_data(ttl=300, show_spinner=False)
def get_bars(symbol, period, interval):
    return provider.history(symbol, period, interval)


@st.cache_data(ttl=3600, show_spinner=False)
def get_fx():
    return provider.usd_sek() or settings.usd_sek_fallback


def to_ny(iso):
    """Tid sparad av servern → New York-tid utan tidszon (samma som kursdatan)."""
    return datetime.fromisoformat(iso).astimezone(NY).replace(tzinfo=None)


from trading_platform.status import render_status_box
render_status_box(provider, ROOT / "paper" / "auto_state.json")

st.title("📊 Diagram")
st.caption("Kurser från Yahoo (kan vara något fördröjda). Gröna pilar = robotens köp, röda pilar = robotens sälj. "
           "Streckade linjer = stop loss (röd) och kursmål (grön) för öppna positioner.")

c1, c2, c3 = st.columns([2, 2, 2])
account_name = c1.selectbox("Konto", list(ACCOUNTS))
acc_file = ACCOUNTS[account_name]
broker = PaperBroker(state_file=acc_file) if acc_file.exists() else None
orders = broker.orders if broker else []
positions = broker.positions if broker else {}

open_syms = [s for s, p in positions.items() if p.get("strategy") != "park"]
traded = list(dict.fromkeys(o["symbol"] for o in reversed(orders) if o.get("strategy") != "park"))
choices = list(dict.fromkeys(open_syms + traded + list(settings.watchlist)))
labels = {s: (f"{s}  · öppen ({positions[s].get('strategy')})" if s in open_syms
              else f"{s}  · handlad" if s in traded else s) for s in choices}
_by_label = {labels[s]: s for s in choices}
symbol = _by_label[c2.selectbox("Aktie", list(_by_label))] if choices else None
custom = c2.text_input("…eller skriv ett kortnamn", "", placeholder="t.ex. TSLA").strip().upper()
if custom:
    symbol = custom
default_iv = 0 if (symbol and positions.get(symbol, {}).get("strategy") == "day") else 1
iv_name = c3.radio("Tidsupplösning", list(INTERVALS), index=default_iv)
period, interval = INTERVALS[iv_name]

if not symbol:
    st.info("Välj en aktie.")
    st.stop()

with st.spinner(f"Hämtar kurser för {symbol}…"):
    try:
        bars = get_bars(symbol, period, interval)
    except Exception as e:
        bars = None
        st.error(f"Kunde inte hämta kurser: {e}")
if bars is None or not len(bars):
    st.warning(f"Inga kurser hittades för {symbol}.")
    st.stop()

x = enrich(bars)
fx = get_fx()
daily = interval == "1d"

fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                    row_heights=[0.55, 0.15, 0.15, 0.15],
                    subplot_titles=(f"{symbol} – {iv_name}", "Volym", "RSI (14)", "MACD (12, 26, 9)"))
fig.add_trace(go.Candlestick(x=x.index, open=x.Open, high=x.High, low=x.Low, close=x.Close, name="Kurs",
                             increasing_line_color="#26a69a", decreasing_line_color="#ef5350"), row=1, col=1)
for col, color in (("EMA9", "#42a5f5"), ("EMA20", "#ffb300"), ("SMA50", "#ab47bc"), ("SMA200", "#8d6e63")):
    if x[col].notna().any():
        fig.add_trace(go.Scatter(x=x.index, y=x[col], name=col, line=dict(width=1, color=color)), row=1, col=1)

# Robotens köp och sälj
sym_orders = [o for o in orders if o.get("symbol") == symbol]
first, last = x.index[0], x.index[-1]
for side, color, marker, label in (("buy", "#00c853", "triangle-up", "Köp"), ("sell", "#d50000", "triangle-down", "Sälj")):
    pts = []
    for o in sym_orders:
        if o.get("side") != side:
            continue
        t = pd.Timestamp(to_ny(o["time"]))
        t = t.normalize() if daily else t.floor("5min")
        if first <= t <= last + pd.Timedelta(days=1):
            pnl = "" if o.get("pnl") is None else f"<br>Resultat {usd_to_sek(o['pnl'], fx):+,.0f} kr"
            pts.append((t, o["price"], f"{label} {o['quantity']} st à {o['price']:.2f} USD"
                                        f"<br>{o.get('strategy', '')}{pnl}"))
    if pts:
        fig.add_trace(go.Scatter(x=[p[0] for p in pts], y=[p[1] for p in pts], mode="markers", name=label,
                                 marker=dict(symbol=marker, size=14, color=color, line=dict(width=1, color="white")),
                                 hovertext=[p[2] for p in pts], hoverinfo="text"), row=1, col=1)

# Öppen position: köpkurs, stop loss och kursmål
pos = positions.get(symbol)
if pos and pos.get("strategy") != "park":
    fig.add_hline(y=pos["avg_price"], line_dash="dot", line_color="gray", row=1, col=1,
                  annotation_text=f"Köpkurs {pos['avg_price']:.2f}", annotation_position="top left")
    if pos.get("stop") is not None:
        fig.add_hline(y=pos["stop"], line_dash="dash", line_color="#d50000", row=1, col=1,
                      annotation_text=f"Stop loss {pos['stop']:.2f}", annotation_position="bottom left")
    if pos.get("target") is not None:
        fig.add_hline(y=pos["target"], line_dash="dash", line_color="#00c853", row=1, col=1,
                      annotation_text=f"Kursmål {pos['target']:.2f}", annotation_position="top left")

vol_colors = ["#26a69a" if c >= o else "#ef5350" for o, c in zip(x.Open, x.Close)]
fig.add_trace(go.Bar(x=x.index, y=x.Volume, name="Volym", marker_color=vol_colors, showlegend=False), row=2, col=1)
fig.add_trace(go.Scatter(x=x.index, y=x.RSI14, name="RSI", line=dict(width=1.2, color="#7e57c2"),
                         showlegend=False), row=3, col=1)
fig.add_hline(y=70, line_dash="dot", line_color="gray", row=3, col=1)
fig.add_hline(y=30, line_dash="dot", line_color="gray", row=3, col=1)
fig.add_trace(go.Bar(x=x.index, y=x.MACD_HIST, name="Histogram", showlegend=False,
                     marker_color=["#26a69a" if v >= 0 else "#ef5350" for v in x.MACD_HIST.fillna(0)]), row=4, col=1)
fig.add_trace(go.Scatter(x=x.index, y=x.MACD, name="MACD", line=dict(width=1.2, color="#1e88e5"),
                         showlegend=False), row=4, col=1)
fig.add_trace(go.Scatter(x=x.index, y=x.MACD_SIGNAL, name="Signal", line=dict(width=1.2, color="#fb8c00"),
                         showlegend=False), row=4, col=1)

breaks = [dict(bounds=["sat", "mon"])]
if not daily:
    breaks.append(dict(bounds=[16, 9.5], pattern="hour"))  # dölj natten (New York-tid)
fig.update_xaxes(rangebreaks=breaks)
fig.update_layout(height=900, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=40, b=10),
                  legend=dict(orientation="h", y=1.02, x=0), hovermode="x unified")
st.plotly_chart(fig)

a = x.iloc[-1]
m = st.columns(4)
m[0].metric("Senaste kurs", f"{a.Close:.2f} USD", help=f"≈ {usd_to_sek(a.Close, fx):,.0f} kr")
m[1].metric("RSI", f"{a.RSI14:.0f}", help="Under 30 = översåld, över 70 = överköpt.")
m[2].metric("MACD", "stiger" if a.MACD_HIST > x.MACD_HIST.iloc[-2] else "faller")
m[3].metric("Trend", "upp" if a.Close > a.EMA20 else "ned", help="Kurs jämfört med EMA20.")

st.subheader(f"Robotens affärer i {symbol}")
if sym_orders:
    t = pd.DataFrame(sym_orders[::-1])
    t["Tid (svensk)"] = t["time"].map(lambda v: datetime.fromisoformat(v).astimezone(ZoneInfo("Europe/Stockholm"))
                                      .strftime("%Y-%m-%d %H:%M"))
    t["Resultat (SEK)"] = t.get("pnl", pd.Series(dtype=float)).map(
        lambda v: None if v is None or v != v else round(usd_to_sek(v, fx)))
    cols = [c for c in ["Tid (svensk)", "side", "quantity", "price", "strategy", "Resultat (SEK)", "reason"] if c in t]
    st.dataframe(t[cols].rename(columns={"side": "Köp/sälj", "quantity": "Antal", "price": "Kurs (USD)",
                                         "strategy": "Strategi", "reason": "Orsak"}), hide_index=True)
else:
    st.caption("Roboten har inte handlat den här aktien i det valda kontot.")
