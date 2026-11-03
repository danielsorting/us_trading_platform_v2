"""Dashboard för US Trading Platform. Starta med:  streamlit run app.py"""
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import streamlit as st

from trading_platform.backtest import (buy_and_hold_by_symbol, compare_strategies, find_best, per_year,
                                      period_return, run_backtest)
from trading_platform.brokers.paper import PaperBroker
from trading_platform.config import PROJECT_ROOT, load_settings
from trading_platform.data import YahooResearchProvider
from trading_platform.paper_trader import latest_prices, run_paper_cycle
from trading_platform.risk import sek_to_usd, usd_to_sek
from trading_platform.scanner import scan
from trading_platform.strategies import day, swing
from trading_platform.strategies.variants import MARKET_SYMBOL, add_market, needs_market, swing_variants
from trading_platform.universe import get_symbols

PAPER_FILE = PROJECT_ROOT / "paper_account.json"
STRATEGIES = {"Swingtrading": swing, "Daytrading": day}
VARIANTS = swing_variants()
LARGE_US = ("AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AVGO,JPM,V,MA,UNH,XOM,JNJ,PG,"
            "HD,COST,LLY,ABBV,MRK,PEP,KO,WMT,BAC,CRM,ORCL,AMD,NFLX,ADBE,CSCO,IREN,NBIS")

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


def market_bars_for(strategy, period="2y"):
    """SPY-kurser om strategin behöver marknadsdata, annars None."""
    if not needs_market([strategy]):
        return None
    try:
        return get_history(MARKET_SYMBOL, period, "1d")
    except Exception:
        st.warning("Kunde inte hämta S&P 500 (SPY). Marknadsfiltret blockerar då alla köp.")
        return None


def parse_symbols(text):
    return list(dict.fromkeys(x.strip().upper() for x in text.replace("\n", ",").split(",") if x.strip()))


# ---------------------------------------------------------------- sidopanel
defaults = load_settings()
RISK_OPTIONS = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
st.session_state.setdefault("sb_risk", float(defaults.risk_per_trade_pct))
st.session_state.setdefault("sb_variant", list(VARIANTS)[0])


def use_best(variant, risk):
    st.session_state["sb_variant"] = variant
    st.session_state["sb_risk"] = float(risk)
    st.session_state["best_applied"] = f"{variant} med {risk} % risk per affär"


# Resultat från 🏆-knappen ställs in innan sidopanelen ritas (Streamlit kräver det).
if "pending_best" in st.session_state:
    use_best(*st.session_state.pop("pending_best"))


with st.sidebar:
    st.header("Kapital och risk")
    capital = st.number_input("Totalt kapital (SEK)", min_value=1000, value=int(defaults.total_capital), step=1000)
    day_pct = st.slider("Andel till daytrading (%)", 0, 100, int(defaults.day_allocation_pct), 5)
    st.caption(f"Andel till swingtrading: {100 - day_pct} %")
    risk = st.select_slider("Risk per affär (% av strategins kapital)", options=RISK_OPTIONS, key="sb_risk")
    max_pos = st.slider("Max antal positioner per strategi", 1, 10, int(defaults.max_positions_per_strategy))
    max_pos_pct = st.slider("Max storlek per position (% av strategins kapital)", 5, 100,
                            int(defaults.max_position_pct_of_strategy_capital), 5)
    fractional = st.checkbox("Tillåt delaktier", value=defaults.allow_fractional_shares,
                             help="T.ex. 0,35 aktie. Behövs ofta när kapitalet är litet.")
    park = st.checkbox("Parkera ledigt kapital i S&P 500 (SPY)", value=defaults.park_idle_in_index,
                       help="Pengar som inte används i affärer ligger i SPY och växer med börsen. "
                            "SPY säljs automatiskt när roboten behöver pengar till ett köp. Gäller swingtrading.")
    settings = replace(defaults, total_capital=float(capital), day_allocation_pct=float(day_pct),
                       swing_allocation_pct=float(100 - day_pct), risk_per_trade_pct=float(risk),
                       max_positions_per_strategy=max_pos, max_position_pct_of_strategy_capital=float(max_pos_pct),
                       allow_fractional_shares=fractional, park_idle_in_index=park)
    variant_name = st.selectbox("Strategi som används (ställs in automatiskt av 🏆-knappen)", list(VARIANTS),
                                key="sb_variant",
                                help="Ställs in automatiskt när du kör 🏆 Hitta bästa automatiskt under Backtest. "
                                     "Du kan också välja själv.")
    swing_strategy = VARIANTS[variant_name]
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
                   settings=settings, usd_sek=usd_sek, swing_strategy=swing_strategy,
                   market_bars=market_bars_for(swing_strategy),
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
    bt_mode = st.radio("Vad vill du göra?",
                       ["🏆 Hitta bästa automatiskt", "Jämför swing-varianter", "Testa en strategi i detalj"],
                       horizontal=True, key="bt_mode")
    if bt_mode == "🏆 Hitta bästa automatiskt":
        strat_name = "__best__"
        module = swing
        st.caption("Testar **alla** swing-varianter på tre risknivåer (0,5 %, 1 % och 2 % per affär) och väljer den "
                   "bästa. Godkänd = plus i både testdel och kontrolldel och minst 20 affärer. Poäng = avkastning per år "
                   "delat med största nedgång (belönar jämn tillväxt). Tar 1–3 minuter.")
    elif bt_mode == "Jämför swing-varianter":
        strat_name = None
        module = swing
        st.caption("Alla varianter testas på samma aktier och period. Perioden delas: de första 70 % är "
                   "*testdel* och de sista 30 % är *kontrolldel*. En bra strategi ska gå plus i båda.")
    else:
        choices = {**{k: v for k, v in VARIANTS.items()}, "Daytrading": day}
        strat_name = st.selectbox("Strategi", list(choices), key="bt_strat")
        module = choices[strat_name]
    if module is not day:
        period = st.select_slider("Period", ["1y", "2y", "5y", "10y"], value="5y",
                                  format_func=lambda p: p.replace("y", " år"))
        interval = "1d"
    else:
        period = st.select_slider("Period", ["5d", "1mo", "60d"], value="60d",
                                  format_func=lambda p: {"5d": "5 dagar", "1mo": "1 månad", "60d": "60 dagar"}[p])
        interval = "5m"
        st.caption("Yahoo har bara 5-minutersdata cirka 60 dagar bakåt, så daytrading kan bara testas på kort period.")
    bt_list = st.radio("Aktielista", ["Min bevakningslista", "30 stora USA-aktier + IREN, NBIS", "Egen lista"],
                       horizontal=True, key="bt_list",
                       help="Fler aktier ger fler affärer och därmed säkrare slutsatser.")
    if bt_list == "Min bevakningslista":
        bt_default = ",".join(settings.watchlist)
    elif bt_list == "30 stora USA-aktier + IREN, NBIS":
        bt_default = LARGE_US
    else:
        bt_default = ""
    bt_symbols = parse_symbols(st.text_area("Aktier", bt_default, height=70, key=f"bt_syms_{bt_list}"))
    default_cap = int(settings.day_capital if module is day else settings.swing_capital)
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
        cap_usd = sek_to_usd(bt_cap_sek, usd_sek)
        mkt = None
        if module is not day:
            mkt = market_bars_for(VARIANTS["Swing + marknadsfilter"], period)
            bars = {s: add_market(b, mkt) for s, b in bars.items()}
        if strat_name == "__best__":
            bar = st.progress(0.0, text="Testar kombinationer…")
            st.session_state["bt_best"] = find_best(
                bars, VARIANTS, cap_usd, settings, benchmark=None if mkt is None else mkt.Close,
                progress=lambda i, n: bar.progress(i / n, text=f"Testat {i} av {n} kombinationer"))
            bar.empty()
            st.session_state.pop("bt", None)
            st.session_state.pop("bt_compare", None)
            best = st.session_state["bt_best"]
            if len(best) and best.iloc[0]["Godkänd"] == "ja":
                # Ställ in vinnaren automatiskt i scanner och simulerad handel.
                st.session_state["pending_best"] = (best.iloc[0]["Strategi"], float(best.iloc[0]["Risk per affär %"]))
                st.rerun()
        elif strat_name is None:
            with st.spinner("Jämför varianter… (kan ta en stund)"):
                st.session_state["bt_compare"] = compare_strategies(
                    bars, VARIANTS, cap_usd, settings, benchmark=None if mkt is None else mkt.Close)
                st.session_state["bt_bh"] = buy_and_hold_by_symbol(bars)
            st.session_state.pop("bt", None)
            st.session_state.pop("bt_best", None)
        else:
            st.session_state.pop("bt_best", None)
            with st.spinner("Kör backtest…"):
                res = run_backtest(bars, module, cap_usd, settings)
                eq = res["equity"]
                bench = None
                if mkt is not None and len(eq):
                    # Jämför från första dagen strategin kunde handla (efter SMA200-uppvärmning).
                    times = sorted(set().union(*[b.index for b in bars.values() if b is not None]))
                    bench = period_return(mkt.Close, times[min(module.MIN_BARS, len(times) - 1)])
                st.session_state["bt"] = (strat_name, res, bench)
            st.session_state.pop("bt_compare", None)

    if st.session_state.get("best_applied"):
        st.success(f"✅ Inställt automatiskt: **{st.session_state['best_applied']}**. Scanner och simulerad "
                   "handel använder nu detta (syns i sidopanelen).")

    if "bt_best" in st.session_state:
        best = st.session_state["bt_best"]
        st.subheader("🏆 Bästa kombinationen")
        if len(best) and best.iloc[0]["Godkänd"] == "ja":
            top = best.iloc[0]
            sp = "" if top["S&P 500 %"] is None else (
                f" S&P 500 gav {top['S&P 500 %']:+.1f} % under samma tid, så den "
                + ("**slog indexfonden**." if top["Slår S&P 500?"] == "ja" else "**slog inte indexfonden**."))
            st.success(f"**{top['Strategi']}** med **{top['Risk per affär %']} % risk per affär**: "
                       f"{top['Avkastning %']:+.1f} % totalt ({top['Per år %']:+.1f} % per år), största nedgång "
                       f"{top['Största nedgång %']:.1f} %, {int(top['Affärer'])} affärer.{sp}")
            if st.session_state.get("sb_variant") != top["Strategi"]:
                st.button("Använd den här i scanner och simulerad handel", type="primary",
                          on_click=use_best, args=(top["Strategi"], top["Risk per affär %"]))
            if top["Slår S&P 500?"] == "nej":
                st.warning("Ärligt: den bästa roboten slog inte en vanlig indexfond under perioden. "
                           "Fundera på om en indexfond är det bättre valet för pengarna.")
        elif len(best):
            st.warning("Ingen kombination blev godkänd (plus i både test- och kontrolldel med minst 20 affärer). "
                       "Prova fler aktier eller längre period.")
        st.caption(f"Testperiod: {best.attrs.get('period', '')}. Tabellen är sorterad: godkända först, högst poäng överst.")
        st.dataframe(best, width="stretch", hide_index=True)
        st.caption("Varning för överanpassning: ju fler kombinationer som testas, desto större risk att den bästa "
                   "bara hade tur. Kör därför den valda strategin i simulerad handel en tid innan du litar på den.")

    if "bt_compare" in st.session_state:
        cmp = st.session_state["bt_compare"]
        st.subheader("Jämförelse av swing-varianter")
        if len(cmp):
            st.dataframe(cmp, width="stretch", hide_index=True)
            good = cmp[(cmp.get("Håller i kontroll?") == "ja")] if "Håller i kontroll?" in cmp else cmp.iloc[0:0]
            bench = cmp["S&P 500 %"].iloc[0] if "S&P 500 %" in cmp else None
            if bench is not None and bench == bench:
                beats = int((cmp["Slår S&P 500?"] == "ja").sum())
                msg = (f"S&P 500 (indexfond) gav **{bench:+.1f} %** under samma period. "
                       f"{beats} av {len(cmp)} varianter slog den.")
                (st.info if beats else st.warning)(msg)
            if len(good):
                st.success(f"Bäst av de som går plus i både test- och kontrolldel: **{good.iloc[0].Strategi}**. "
                           "Välj den i sidopanelen för scanner och simulerad handel.")
            else:
                st.warning("Ingen variant gick plus i både test- och kontrolldel med dessa aktier och denna period. "
                           "Prova fler aktier eller en längre period innan du drar slutsatser.")
            st.caption("Tips: testa på många aktier (gärna 20+) och den längsta perioden. Ju fler affärer, "
                       "desto säkrare resultat. Jämför med **S&P 500** – 'Köp & behåll' gäller aktier "
                       "valda i efterhand och är därför ingen rättvis jämförelse.")
            if "Testperiod" in cmp:
                st.caption(f"Testperiod: {cmp['Testperiod'].iloc[0]} (de första ca 220 dagarna används bara "
                           "för att räkna fram SMA200).")
            with st.expander("Köp och behåll per aktie (kontroll av siffrorna)"):
                st.dataframe(st.session_state.get("bt_bh", pd.DataFrame()), width="stretch", hide_index=True)
        else:
            st.warning("Ingen data att jämföra.")

    if "bt" in st.session_state:
        name, r, bench = st.session_state["bt"]
        s = r["stats"]
        st.subheader(f"Resultat – {name}")
        m = st.columns(4)
        m[0].metric("Avkastning", f"{s['total_return_pct']:+.1f} %")
        m[1].metric("S&P 500 (indexfond)", "–" if bench is None else f"{bench:+.1f} %",
                    help="Vad en indexfond på S&P 500 (SPY) gav under samma period. Det är den viktigaste jämförelsen.")
        m[2].metric("Största nedgång", f"{s['max_drawdown_pct']:.1f} %")
        m[3].metric("Antal affärer", s["trades"])
        m = st.columns(4)
        m[0].metric("Vinstandel", f"{s['win_rate_pct']:.0f} %")
        m[1].metric("Snitt per affär (R)", f"{s['avg_r']:+.2f}", help="R = risken per affär. +1R betyder att du vann lika mycket som du riskerade.")
        m[2].metric("Profit factor", "–" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}",
                    help="Summa vinster / summa förluster. Över 1 = lönsam.")
        m[3].metric("Courtage totalt", f"{usd_to_sek(s['fees_usd'], usd_sek):,.0f} SEK",
                    help="Inklusive kostnader för att flytta pengar in och ut ur SPY när parkering är på.")
        if s["buy_and_hold_pct"] is not None:
            st.caption(f"Köp och behåll av just dessa aktier: {s['buy_and_hold_pct']:+.1f} % "
                       "(valda i efterhand – därför ingen rättvis jämförelse).")
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
            rows = scan(paper_syms, True, True, provider=provider, settings=settings, usd_sek=usd_sek,
                        swing_strategy=swing_strategy, market_bars=market_bars_for(swing_strategy))
            prices = latest_prices(provider, set(paper_syms) | set(broker.positions)
                                   | ({settings.park_symbol} if settings.park_idle_in_index else set()))
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
- **Swingtrading** (dagsdata): upptrend (över SMA200, SMA50 > SMA200), nylig rekyl (RSI ≤ 45) och **bekräftad vändning**: RSI stiger två dagar i rad, MACD-linjen och histogrammet stiger, inga nya bottnar och stängning över gårdagens högsta kurs. Köper aldrig när RSI eller MACD är på väg ner.
  Stop = pris − 2 × ATR, mål = 2 × risken, max 20 handelsdagar.
- **Daytrading** (5-minutersdata): kurs över EMA20, EMA9 > EMA20, MACD-linjen och histogrammet stiger, RSI stiger och ligger 50–75, hög volym.
  Stop = pris − 1,5 × ATR, mål = 2 × risken, stängs senast vid dagens slut.
- **Utbrott (trendföljning)**: köper när aktien stänger på ny 50-dagars högsta med stark volym, i en upptrend,
  med stigande RSI och MACD. Inget fast mål – stop loss följer med uppåt (3 × ATR).
- **Parkering i S&P 500**: ledigt kapital ligger i SPY och säljs automatiskt när roboten behöver pengar till ett köp.
- **Positionsstorlek**: så många aktier att du förlorar högst *risk per affär* om stop loss slår till.

**Viktiga begränsningar**
- Kursdata kommer från Yahoo Finance: gratis men kan vara fördröjd och ibland ofullständig. Inte för riktig handel.
- Med litet kapital äter courtaget (ca 1 USD per köp och sälj) en stor del av varje affär. Backtestet visar hur mycket.
- Riktig handel är avstängd. Den byggs först efter en lång period med simulerad handel.
""")
