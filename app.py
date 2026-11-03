"""Dashboard för US Trading Platform. Starta med:  streamlit run app.py"""
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import streamlit as st

from trading_platform.backtest import run_backtest
from trading_platform.brokers.paper import PaperBroker
from trading_platform.config import PROJECT_ROOT, load_settings
from trading_platform.data import YahooResearchProvider
from trading_platform.paper_trader import latest_prices, run_paper_cycle
from trading_platform.risk import sek_to_usd, usd_to_sek
from trading_platform.scanner import scan
from trading_platform.strategies import day, swing
from trading_platform.universe import get_symbols

PAPER_FILE = PROJECT_ROOT / "paper_account.json"
STRATEGIES = {"Swingtrading": swing, "Daytrading": day}

COLUMN_NAMES = {
    "symbol": "Aktie", "strategy": "Strategi", "signal": "Signal", "price": "Pris (USD)",
    "rsi": "RSI", "atr": "ATR", "stop_reference": "Stop loss", "target_reference": "Kursmål",
    "conditions_met": "Villkor", "shares": "Antal aktier", "position_usd": "Position (USD)",
    "position_sek": "Position (SEK)", "risk_usd": "Risk (USD)", "risk_sek": "Risk (SEK)",
    "conditions": "Detaljer", "note": "Notering", "reason": "Orsak",
}
SIGNAL_NAMES = {"CANDIDATE": "✅ Köpkandidat", "WATCH": "👀 Bevaka", "LOW_LIQUIDITY": "💧 För låg omsättning",
                "INSUFFICIENT_DATA": "… För lite data", "DATA_ERROR": "⚠️ Datafel"}

st.set_page_config(page_title="US Trading Platform", layout="wide")
provider = YahooResearchProvider()


@st.cache_data(ttl=3600, show_spinner=False)
def get_usd_sek(fallback):
    rate = provider.usd_sek()
    return (rate, "aktuell kurs") if rate else (fallback, "reservkurs från config.json")


@st.cache_data(ttl=900, show_spinner=False)
def get_history(symbol, period, interval):
    return provider.history(symbol, period, interval)


def parse_symbols(text):
    return list(dict.fromkeys(x.strip().upper() for x in text.replace("\n", ",").split(",") if x.strip()))


# ---------------------------------------------------------------- sidopanel
defaults = load_settings()
with st.sidebar:
    st.header("Kapital och risk")
    capital = st.number_input("Totalt kapital (SEK)", min_value=1000, value=int(defaults.total_capital), step=1000)
    day_pct = st.slider("Andel till daytrading (%)", 0, 100, int(defaults.day_allocation_pct), 5)
    st.caption(f"Andel till swingtrading: {100 - day_pct} %")
    risk = st.select_slider("Risk per affär (% av strategins kapital)", options=[0.25, 0.5, 0.75, 1.0, 1.5, 2.0],
                            value=float(defaults.risk_per_trade_pct))
    max_pos = st.slider("Max antal positioner per strategi", 1, 10, int(defaults.max_positions_per_strategy))
    max_pos_pct = st.slider("Max storlek per position (% av strategins kapital)", 5, 100,
                            int(defaults.max_position_pct_of_strategy_capital), 5)
    fractional = st.checkbox("Tillåt delaktier", value=defaults.allow_fractional_shares,
                             help="T.ex. 0,35 aktie. Behövs ofta när kapitalet är litet.")
    settings = replace(defaults, total_capital=float(capital), day_allocation_pct=float(day_pct),
                       swing_allocation_pct=float(100 - day_pct), risk_per_trade_pct=float(risk),
                       max_positions_per_strategy=max_pos, max_position_pct_of_strategy_capital=float(max_pos_pct),
                       allow_fractional_shares=fractional)
    usd_sek, fx_source = get_usd_sek(settings.usd_sek_fallback)
    st.divider()
    st.metric("USD/SEK", f"{usd_sek:.2f}", help=f"Källa: {fx_source}")
    st.caption("Inställningarna här gäller bara tills sidan laddas om. Startvärdena finns i config.json.")

st.title("US Trading Platform")
st.caption("Scanner · Backtest · Simulerad handel — inga riktiga order skickas")

c1, c2, c3 = st.columns(3)
c1.metric("Daytrading-budget", f"{settings.day_capital:,.0f} SEK", f"≈ {sek_to_usd(settings.day_capital, usd_sek):,.0f} USD", delta_color="off")
c2.metric("Swingtrading-budget", f"{settings.swing_capital:,.0f} SEK", f"≈ {sek_to_usd(settings.swing_capital, usd_sek):,.0f} USD", delta_color="off")
c3.metric("Max risk per affär (swing)", f"{settings.swing_capital * risk / 100:,.0f} SEK")

tab_scan, tab_bt, tab_paper, tab_info = st.tabs(["🔎 Scanner", "📈 Backtest", "🧪 Simulerad handel", "ℹ️ Om"])

# ---------------------------------------------------------------- scanner
with tab_scan:
    mode = st.radio("Vilka aktier?", ["Min bevakningslista", "Hela USA-marknaden"], horizontal=True)
    include_etfs = st.checkbox("Ta med ETF:er", value=settings.include_etfs)
    if mode == "Min bevakningslista":
        symbols = parse_symbols(st.text_area("Aktier (kommaseparerade)", ",".join(settings.watchlist), height=80))
    else:
        st.warning("Hela marknaden är tusentals aktier. Skanningen begränsas till det antal du väljer nedan "
                   "och kan ta flera minuter. Yahoo kan också begränsa många anrop.")
        if st.button("Hämta lista över USA-aktier"):
            try:
                uni = get_symbols("us_market", [], include_etfs)
                st.session_state["us_symbols"] = uni["symbol"].tolist()
            except Exception as e:
                st.error(f"Kunde inte hämta listan: {e}")
        all_syms = st.session_state.get("us_symbols", [])
        limit = st.slider("Max antal aktier att skanna", 10, 2000, settings.max_symbols_per_scan, 10)
        symbols = all_syms[:limit]
        st.write(f"Hämtade aktier: {len(all_syms):,} · skannas: {len(symbols):,}")

    chosen = st.multiselect("Strategier", list(STRATEGIES), default=list(STRATEGIES))
    if st.button("Kör skanning", type="primary", disabled=not symbols or not chosen):
        bar = st.progress(0.0, text="Startar…")
        res = scan(symbols, "Daytrading" in chosen, "Swingtrading" in chosen, provider=provider,
                   settings=settings, usd_sek=usd_sek,
                   progress=lambda i, n: bar.progress(i / n, text=f"Skannat {i} av {n} aktier"))
        bar.empty()
        st.session_state["scan_results"] = pd.DataFrame(res)

    if "scan_results" in st.session_state:
        df = st.session_state["scan_results"]
        only = st.checkbox("Visa bara köpkandidater", value=False)
        show = df[df.signal == "CANDIDATE"] if only and "signal" in df else df
        n_cand = int((df.get("signal") == "CANDIDATE").sum()) if len(df) else 0
        st.subheader(f"Resultat: {n_cand} köpkandidater")
        view = show.copy()
        if "signal" in view:
            view["signal"] = view["signal"].map(SIGNAL_NAMES).fillna(view["signal"])
        st.dataframe(view.rename(columns=COLUMN_NAMES), width="stretch", hide_index=True)
        st.download_button("Ladda ner som CSV", df.to_csv(index=False), "scan_results.csv", "text/csv")
        st.caption("Stop loss och kursmål är förslag från strategin. Antal aktier är räknat så att du förlorar "
                   "högst din valda risk om stop loss slår till (före courtage och slippage).")

# ---------------------------------------------------------------- backtest
with tab_bt:
    st.write("Testa hur en strategi hade gått historiskt. Köp sker alltid på stapeln **efter** signalen, "
             "och courtage och slippage räknas med.")
    strat_name = st.radio("Strategi", list(STRATEGIES), horizontal=True, key="bt_strat")
    module = STRATEGIES[strat_name]
    if module is swing:
        period = st.select_slider("Period", ["1y", "2y", "5y", "10y"], value="5y",
                                  format_func=lambda p: p.replace("y", " år"))
        interval = "1d"
    else:
        period = st.select_slider("Period", ["5d", "1mo", "60d"], value="60d",
                                  format_func=lambda p: {"5d": "5 dagar", "1mo": "1 månad", "60d": "60 dagar"}[p])
        interval = "5m"
        st.caption("Yahoo har bara 5-minutersdata cirka 60 dagar bakåt, så daytrading kan bara testas på kort period.")
    bt_symbols = parse_symbols(st.text_area("Aktier", ",".join(settings.watchlist), height=70, key="bt_syms"))
    default_cap = int(settings.swing_capital if module is swing else settings.day_capital)
    bt_cap_sek = st.number_input("Startkapital för testet (SEK)", min_value=1000, value=default_cap, step=1000)

    if st.button("Kör backtest", type="primary", disabled=not bt_symbols):
        bars = {}
        with st.spinner("Hämtar historiska kurser…"):
            for s in bt_symbols:
                try:
                    bars[s] = get_history(s, period, interval)
                except Exception:
                    bars[s] = None
        missing = [s for s, b in bars.items() if b is None or len(b) < module.MIN_BARS]
        if missing:
            st.warning("För lite data för: " + ", ".join(missing))
        with st.spinner("Kör backtest…"):
            st.session_state["bt"] = (strat_name, run_backtest(bars, module, sek_to_usd(bt_cap_sek, usd_sek), settings))

    if "bt" in st.session_state:
        name, r = st.session_state["bt"]
        s = r["stats"]
        st.subheader(f"Resultat – {name}")
        m = st.columns(4)
        m[0].metric("Avkastning", f"{s['total_return_pct']:+.1f} %")
        m[1].metric("Köp och behåll (jämförelse)", "–" if s["buy_and_hold_pct"] is None else f"{s['buy_and_hold_pct']:+.1f} %")
        m[2].metric("Största nedgång", f"{s['max_drawdown_pct']:.1f} %")
        m[3].metric("Antal affärer", s["trades"])
        m = st.columns(4)
        m[0].metric("Vinstandel", f"{s['win_rate_pct']:.0f} %")
        m[1].metric("Snitt per affär (R)", f"{s['avg_r']:+.2f}", help="R = risken per affär. +1R betyder att du vann lika mycket som du riskerade.")
        m[2].metric("Profit factor", "–" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}",
                    help="Summa vinster / summa förluster. Över 1 = lönsam.")
        m[3].metric("Courtage totalt", f"{usd_to_sek(s['fees_usd'], usd_sek):,.0f} SEK")
        if len(r["equity"]):
            st.line_chart((r["equity"] * usd_sek).rename("Kapital (SEK)"))
        if len(r["trades"]):
            st.write("**Varför affärerna avslutades:**", r["trades"].exit_reason.value_counts().to_dict())
            with st.expander("Alla affärer"):
                st.dataframe(r["trades"], width="stretch", hide_index=True)
        st.info("Ett bra historiskt resultat är ingen garanti för framtiden. Testa på många aktier och "
                "olika perioder, och jämför alltid med 'köp och behåll'.")

# ---------------------------------------------------------------- simulerad handel
with tab_paper:
    st.write("Handla med **låtsaspengar** enligt strategierna. Varje gång du trycker på *Kör handelsrunda* "
             "säljs positioner som nått stop/mål/tidsgräns och nya köpkandidater köps.")
    broker = PaperBroker(sek_to_usd(settings.total_capital, usd_sek), PAPER_FILE,
                         settings.commission_per_share_usd, settings.min_commission_usd,
                         settings.slippage_pct, usd_sek)
    prices = st.session_state.get("paper_prices", {})
    acc = broker.get_account(prices)
    m = st.columns(4)
    m[0].metric("Kontovärde", f"{usd_to_sek(acc['equity'], usd_sek):,.0f} SEK", f"{acc['equity']:,.2f} USD", delta_color="off")
    m[1].metric("Kontanter", f"{usd_to_sek(acc['cash'], usd_sek):,.0f} SEK")
    m[2].metric("Resultat sedan start", f"{(acc['equity'] / acc['starting_cash'] - 1) * 100:+.2f} %")
    m[3].metric("Öppna positioner", len(broker.positions))

    paper_syms = parse_symbols(st.text_area("Aktier som handelsrundan skannar", ",".join(settings.watchlist),
                                            height=70, key="paper_syms"))
    b1, b2 = st.columns(2)
    if b1.button("▶️ Kör handelsrunda", type="primary", disabled=not paper_syms):
        with st.spinner("Skannar och handlar…"):
            rows = scan(paper_syms, True, True, provider=provider, settings=settings, usd_sek=usd_sek)
            prices = latest_prices(provider, set(paper_syms) | set(broker.positions))
            st.session_state["paper_prices"] = prices
            st.session_state["paper_actions"] = run_paper_cycle(broker, rows, settings, prices)
        st.rerun()
    if b2.button("🔄 Uppdatera kurser", disabled=not broker.positions):
        st.session_state["paper_prices"] = latest_prices(provider, list(broker.positions))
        st.rerun()

    if st.session_state.get("paper_actions"):
        st.subheader("Senaste handelsrundan")
        st.dataframe(pd.DataFrame(st.session_state["paper_actions"]).rename(
            columns={"symbol": "Aktie", "action": "Händelse", "detail": "Detaljer"}), width="stretch", hide_index=True)

    st.subheader("Öppna positioner")
    if broker.positions:
        pos = pd.DataFrame([{"Aktie": s, "Strategi": p.get("strategy"), "Antal": p["quantity"],
                             "Köpkurs": round(p["avg_price"], 2), "Senaste kurs": prices.get(s),
                             "Stop loss": p.get("stop"), "Kursmål": None if p.get("target") is None else round(p["target"], 2),
                             "Orealiserat (USD)": None if s not in prices else round(p["quantity"] * (prices[s] - p["avg_price"]), 2),
                             "Öppnad": p["opened"]} for s, p in broker.positions.items()])
        st.dataframe(pos, width="stretch", hide_index=True)
    else:
        st.caption("Inga öppna positioner.")

    st.subheader("Affärshistorik")
    if broker.orders:
        st.dataframe(pd.DataFrame(broker.orders[::-1]), width="stretch", hide_index=True)
        closed = [o["pnl"] for o in broker.orders if o.get("pnl") is not None]
        if closed:
            st.caption(f"Avslutade affärer: {len(closed)} · vinstandel {sum(p > 0 for p in closed) / len(closed) * 100:.0f} % "
                       f"· summa {sum(closed):+.2f} USD")
    else:
        st.caption("Inga affärer ännu.")

    with st.expander("Spara, återställ eller börja om"):
        st.write("På webbversionen (Streamlit Cloud) kan kontot nollställas när appen startas om. "
                 "Ladda därför ner kontot ibland och ladda upp det igen vid behov.")
        st.download_button("Ladda ner kontot (JSON)", json.dumps(broker.to_dict(), indent=2, ensure_ascii=False),
                           "paper_account.json", "application/json")
        up = st.file_uploader("Ladda upp ett sparat konto", type="json")
        if up is not None and st.button("Återställ från filen"):
            broker.from_dict(json.loads(up.getvalue()))
            broker.save()
            st.success("Kontot är återställt.")
            st.rerun()
        if st.checkbox("Jag vill börja om med ett nytt konto") and st.button("Börja om", type="secondary"):
            broker.reset(sek_to_usd(settings.total_capital, usd_sek), usd_sek)
            st.session_state.pop("paper_actions", None)
            st.rerun()

# ---------------------------------------------------------------- om
with tab_info:
    st.markdown("""
**Så här fungerar det**
- **Scanner** bedömer varje aktie med strategiernas villkor. *Köpkandidat* betyder att alla villkor är uppfyllda.
- **Swingtrading** (dagsdata): upptrend (över SMA200, SMA50 > SMA200), nylig rekyl (RSI ≤ 45), RSI och MACD vänder upp, normal volym.
  Stop = pris − 2 × ATR, mål = 2 × risken, max 20 handelsdagar.
- **Daytrading** (5-minutersdata): kurs över EMA20, EMA9 > EMA20, MACD stiger, RSI 50–75, hög volym.
  Stop = pris − 1,5 × ATR, mål = 2 × risken, stängs senast vid dagens slut.
- **Positionsstorlek**: så många aktier att du förlorar högst *risk per affär* om stop loss slår till.

**Viktiga begränsningar**
- Kursdata kommer från Yahoo Finance: gratis men kan vara fördröjd och ibland ofullständig. Inte för riktig handel.
- Med litet kapital äter courtaget (ca 1 USD per köp och sälj) en stor del av varje affär. Backtestet visar hur mycket.
- Riktig handel är avstängd. Den byggs först efter en lång period med simulerad handel.
""")
