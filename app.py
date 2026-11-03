"""Dashboard för US Trading Platform. Starta med:  streamlit run app.py"""
import json
import sys
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
# Läs alltid in robotens senaste kod. Streamlit Cloud behåller annars gamla
# versioner av modulerna i minnet efter en uppdatering (gav ImportError).
for _m in [m for m in sys.modules if m == "trading_platform" or m.startswith("trading_platform.")]:
    del sys.modules[_m]

import pandas as pd
import streamlit as st

from trading_platform.backtest import (buy_and_hold_by_symbol, compare_strategies, find_best, per_year,
                                      period_return, run_backtest)
from trading_platform.brokers.paper import PaperBroker
from trading_platform.config import PROJECT_ROOT, load_settings
from trading_platform.data import AlpacaRealtime, HybridProvider, PrefetchedProvider, YahooResearchProvider
from trading_platform.daytop import (add_extended_check, liquid_universe, market_status, most_liquid,
                                    rank_day_candidates, yahoo_symbol)
from trading_platform.autotrader import day_round, tick
from trading_platform.paper_trader import (STRATEGY_NAMES, close_all_day_positions, latest_prices,
                                          pnl_breakdown, run_paper_cycle)
from trading_platform.risk import sek_to_usd, usd_to_sek
from trading_platform.scanner import scan
from trading_platform.strategies import day, swing
from trading_platform.strategies.day_variants import day_variants
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
def _secret(name):
    """Läser en hemlig nyckel från Streamlit Secrets eller miljövariabel (aldrig från koden)."""
    import os
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name)


_alpaca_key, _alpaca_secret = _secret("ALPACA_KEY_ID"), _secret("ALPACA_SECRET_KEY")
REALTIME = AlpacaRealtime(_alpaca_key, _alpaca_secret) if _alpaca_key and _alpaca_secret else None
provider = HybridProvider(YahooResearchProvider(), REALTIME) if REALTIME else YahooResearchProvider()


@st.cache_data(ttl=600, show_spinner=False)
def realtime_status(_dummy=0):
    if REALTIME is None:
        return False, "Ingen realtidskälla – Yahoo används (kan vara fördröjt)."
    ok, text = REALTIME.check()
    return ok, ("Realtidskurser från Alpaca (IEX) ✅" if ok else text + " – Yahoo används i stället.")


@st.cache_data(ttl=3600, show_spinner=False)
def get_usd_sek(fallback):
    rate = provider.usd_sek()
    return (rate, "aktuell kurs") if rate else (fallback, "reservkurs från config.json")


@st.cache_data(ttl=900, show_spinner=False)
def get_history(symbol, period, interval):
    return provider.history(symbol, period, interval)


@st.cache_data(ttl=900, show_spinner=False)
def get_history_many(symbols, period, interval):
    """Hämtar kurser för många aktier i ett svep – mycket snabbare än en i taget."""
    return provider.history_many(list(symbols), period, interval)


def market_bars_for(strategy, period="2y"):
    """SPY-kurser om strategin behöver marknadsdata, annars None."""
    if not needs_market([strategy]):
        return None
    try:
        return get_history(MARKET_SYMBOL, period, "1d")
    except Exception:
        st.warning("Kunde inte hämta S&P 500 (SPY). Marknadsfiltret blockerar då alla köp.")
        return None


LIQUID_FILE = PROJECT_ROOT / "cache_liquid_ranking.json"
LIQUID_MAX_AGE_H = 6


@st.cache_data(ttl=LIQUID_MAX_AGE_H * 3600, show_spinner=False)
def get_liquid_ranking(include_etfs=False):
    """Alla USA-aktier (pris ≥ 5 USD) sorterade efter omsättning. Tungt (~6 000 aktier).

    Resultatet sparas både i minnet och i en fil i 6 timmar, så att det finns kvar
    även när appen uppdateras. Delas av Scanner, Backtest och Simulerad handel."""
    import time
    try:
        saved = json.loads(LIQUID_FILE.read_text())
        if time.time() - saved["time"] < LIQUID_MAX_AGE_H * 3600 and saved["symbols"]:
            return saved["symbols"]
    except Exception:
        pass
    ranking = liquid_universe(provider, 1000, fallback_symbols=defaults.watchlist)
    try:
        LIQUID_FILE.write_text(json.dumps({"time": time.time(), "symbols": ranking}))
    except Exception:
        pass
    return ranking


def liquid_symbols(n=300):
    with st.spinner(f"Hittar de {n} mest omsatta USA-aktierna (kan ta några minuter första gången, "
                    "sedan sparat i 6 timmar)…"):
        return get_liquid_ranking()[:n]


LIQUID = "Hela USA-marknaden (300 mest omsatta)"


def sv_time(iso):
    """ISO-tid sparad av servern → svensk tid (ÅÅÅÅ-MM-DD TT:MM)."""
    try:
        return datetime.fromisoformat(iso).astimezone(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def show_pnl_by_strategy(acct_broker, acct_prices):
    """Visar resultat i kronor uppdelat på swing, daytrading och parkering."""
    br = pnl_breakdown(acct_broker, acct_prices)
    st.markdown("**Resultat per strategi**")
    cols = st.columns(3)
    for col, key in zip(cols, ["swing", "day", "park"]):
        v = br[key]
        win = f" · vinstandel {v['wins'] / v['closed'] * 100:.0f} %" if v["closed"] else ""
        col.metric(STRATEGY_NAMES[key], f"{usd_to_sek(v['total'], usd_sek):+,.0f} kr",
                   help=f"Avslutade affärer: {usd_to_sek(v['realized'], usd_sek):+,.0f} kr ({v['closed']} st). "
                        f"Öppna positioner just nu: {usd_to_sek(v['unrealized'], usd_sek):+,.0f} kr ({v['open']} st). "
                        "Efter courtage. Klicka 'Uppdatera kurser' för aktuella värden.")
        col.caption(f"Avslutat {usd_to_sek(v['realized'], usd_sek):+,.0f} kr ({v['closed']} st{win}) · "
                    f"öppet {usd_to_sek(v['unrealized'], usd_sek):+,.0f} kr ({v['open']} st)")


def parse_symbols(text):
    return list(dict.fromkeys(x.strip().upper() for x in text.replace("\n", ",").split(",") if x.strip()))


# ---------------------------------------------------------------- sidopanel
defaults = load_settings()
RISK_OPTIONS = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
st.session_state.setdefault("sb_risk", float(defaults.risk_per_trade_pct))
st.session_state.setdefault("sb_variant", defaults.swing_variant if defaults.swing_variant in VARIANTS
                            else list(VARIANTS)[0])


def use_best(variant, risk=None):
    st.session_state["sb_variant"] = variant
    if risk is not None:
        st.session_state["sb_risk"] = float(risk)
        st.session_state["best_applied"] = f"{variant} med {risk} % risk per affär"
    else:
        st.session_state["best_applied"] = f"{variant} (din risk per affär behålls)"


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
    swing_strategy_name = variant_name
    usd_sek, fx_source = get_usd_sek(settings.usd_sek_fallback)
    st.divider()
    st.metric("USD/SEK", f"{usd_sek:.2f}", help=f"Källa: {fx_source}")
    rt_ok, rt_text = realtime_status()
    (st.success if rt_ok else st.info)(rt_text)
    st.caption("Inställningarna här gäller bara tills sidan laddas om. Startvärdena finns i config.json.")

st.title("US Trading Platform")
st.caption("Scanner · Backtest · Simulerad handel — inga riktiga order skickas")

c1, c2, c3 = st.columns(3)
c1.metric("Daytrading-budget", f"{settings.day_capital:,.0f} SEK", help=f"≈ {sek_to_usd(settings.day_capital, usd_sek):,.0f} USD")
c2.metric("Swingtrading-budget", f"{settings.swing_capital:,.0f} SEK", help=f"≈ {sek_to_usd(settings.swing_capital, usd_sek):,.0f} USD")
c3.metric("Max risk per affär (swing)", f"{settings.swing_capital * risk / 100:,.0f} SEK")

tab_auto, tab_scan, tab_bt, tab_paper, tab_info = st.tabs(
    ["🤖 Automatisk handel", "🔎 Scanner", "📈 Backtest", "🧪 Simulerad handel", "ℹ️ Om"])

# ---------------------------------------------------------------- scanner
with tab_scan:
    mode = st.radio("Vilka aktier?", ["Min bevakningslista", LIQUID], horizontal=True)
    if mode == "Min bevakningslista":
        symbols = parse_symbols(st.text_area("Aktier (kommaseparerade)", ",".join(settings.watchlist), height=80))
        n_liquid = None
    else:
        n_liquid = st.slider("Antal aktier (de mest omsatta)", 50, 1000, 300, 50, key="scan_n")
        st.caption("Hela marknaden är cirka 6 000 aktier. Roboten tar de mest omsatta (pris minst 5 USD), "
                   "eftersom de går att handla utan stora prisskillnader. Första gången per 6 timmar tar det "
                   "några minuter.")
        symbols = ["(hämtas vid skanning)"]

    chosen = st.multiselect("Strategier", list(STRATEGIES), default=list(STRATEGIES))
    if st.button("Kör skanning", type="primary", disabled=not symbols or not chosen):
        if n_liquid:
            symbols = liquid_symbols(n_liquid)
        bar = st.progress(0.0, text="Hämtar kurser…")
        fast = PrefetchedProvider(provider)
        if "Swingtrading" in chosen:
            fast.add(get_history_many(tuple(symbols), "2y", "1d"), "2y", "1d")
        if "Daytrading" in chosen:
            fast.add(get_history_many(tuple(symbols), "5d", "5m"), "5d", "5m")
        res = scan(symbols, "Daytrading" in chosen, "Swingtrading" in chosen, provider=fast,
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
        choose_risk = st.checkbox("Låt knappen även välja risk per affär", value=False, key="bt_choose_risk",
                                  help="Av: knappen behåller din risk från sidopanelen och väljer bara strategi. "
                                       "På: den testar även 0,5 %, 1 % och 2 % och väljer den jämnaste.")
        if choose_risk:
            st.caption("Testar **alla** strategier på tre risknivåer (0,5 %, 1 % och 2 % per affär) och väljer den "
                       "bästa. Tar 1–3 minuter.")
        else:
            st.caption(f"Testar **alla** strategier med **din risk: {risk} % per affär** och väljer den bästa. "
                       "Tar under en minut.")
        st.caption("Godkänd = plus i både testdel och kontrolldel och minst 20 affärer. Poäng = avkastning per år "
                   "delat med största nedgång (belönar jämn tillväxt).")
    elif bt_mode == "Jämför swing-varianter":
        strat_name = None
        module = swing
        st.caption("Alla varianter testas på samma aktier och period. Perioden delas: de första 70 % är "
                   "*testdel* och de sista 30 % är *kontrolldel*. En bra strategi ska gå plus i båda.")
    else:
        choices = {**{k: v for k, v in VARIANTS.items()}, **day_variants()}
        strat_name = st.selectbox("Strategi", list(choices), key="bt_strat")
        module = choices[strat_name]
    if getattr(module, "NAME", "") != "day":
        period = st.select_slider("Period", ["1y", "2y", "5y", "10y"], value="5y",
                                  format_func=lambda p: p.replace("y", " år"))
        interval = "1d"
    else:
        period = st.select_slider("Period", ["5d", "1mo", "60d"], value="60d",
                                  format_func=lambda p: {"5d": "5 dagar", "1mo": "1 månad", "60d": "60 dagar"}[p])
        interval = "5m"
        st.caption("Yahoo har bara 5-minutersdata cirka 60 dagar bakåt, så daytrading kan bara testas på kort period.")
    bt_list = st.radio("Aktielista", ["Min bevakningslista", "30 stora USA-aktier + IREN, NBIS", LIQUID, "Egen lista"],
                       horizontal=True, key="bt_list",
                       help="Fler aktier ger fler affärer och därmed säkrare slutsatser.")
    if bt_list == "Min bevakningslista":
        bt_default = ",".join(settings.watchlist)
    elif bt_list == "30 stora USA-aktier + IREN, NBIS":
        bt_default = LARGE_US
    else:
        bt_default = ""
    if bt_list == LIQUID:
        bt_symbols = ["(hämtas vid test)"]
        st.caption("Dagens 300 mest omsatta aktier. Obs: de är valda utifrån hur de ser ut **i dag**, så "
                   "historiska test blir något för positiva (bolag som gått dåligt och försvunnit saknas). "
                   "Med 300 aktier tar 🏆-knappen några minuter.")
    else:
        bt_symbols = parse_symbols(st.text_area("Aktier", bt_default, height=70, key=f"bt_syms_{bt_list}"))
    default_cap = int(settings.day_capital if getattr(module, "NAME", "") == "day" else settings.swing_capital)
    bt_cap_sek = st.number_input("Startkapital för testet (SEK)", min_value=1000, value=default_cap, step=1000)

    if st.button("Kör backtest", type="primary", disabled=not bt_symbols):
        if bt_list == LIQUID:
            bt_symbols = liquid_symbols(300)
        with st.spinner("Hämtar historiska kurser…"):
            bars = get_history_many(tuple(bt_symbols), period, interval)
        missing = [s for s, b in bars.items() if b is None or len(b) < module.MIN_BARS]
        if missing:
            st.warning("För lite data för: " + ", ".join(missing))
        cap_usd = sek_to_usd(bt_cap_sek, usd_sek)
        mkt = None
        if getattr(module, "NAME", "") != "day":
            mkt = market_bars_for(VARIANTS["Swing + marknadsfilter"], period)
            bars = {s: add_market(b, mkt) for s, b in bars.items()}
        if strat_name == "__best__":
            bar = st.progress(0.0, text="Testar kombinationer…")
            pick_risk = st.session_state.get("bt_choose_risk", False)
            st.session_state["bt_best"] = find_best(
                bars, VARIANTS, cap_usd, settings, benchmark=None if mkt is None else mkt.Close,
                risk_levels=(0.5, 1.0, 2.0) if pick_risk else (settings.risk_per_trade_pct,),
                progress=lambda i, n: bar.progress(i / n, text=f"Testat {i} av {n} kombinationer"))
            bar.empty()
            st.session_state.pop("bt", None)
            st.session_state.pop("bt_compare", None)
            best = st.session_state["bt_best"]
            if len(best) and best.iloc[0]["Godkänd"] == "ja":
                # Ställ in vinnaren automatiskt i scanner och simulerad handel.
                st.session_state["pending_best"] = (
                    best.iloc[0]["Strategi"], float(best.iloc[0]["Risk per affär %"]) if pick_risk else None)
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
                          on_click=use_best, args=(top["Strategi"],))
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
    st.write("Handla med **låtsaspengar** – swing och daytrading på samma ställe. Slå på **Autoläge** så "
             "köper och säljer roboten själv medan sidan är öppen.")
    broker = PaperBroker(sek_to_usd(settings.total_capital, usd_sek), PAPER_FILE,
                         settings.commission_per_share_usd, settings.min_commission_usd,
                         settings.slippage_pct, usd_sek, settings.commission_pct, settings.fx_fee_pct)
    prices = st.session_state.get("paper_prices", {})
    acc = broker.get_account(prices)
    park_val = sum(p["quantity"] * prices.get(sym, p["avg_price"]) for sym, p in broker.positions.items()
                   if p.get("strategy") == "park")
    trades = {sym: p for sym, p in broker.positions.items() if p.get("strategy") != "park"}
    stock_val = sum(p["quantity"] * prices.get(sym, p["avg_price"]) for sym, p in trades.items())
    m = st.columns(5)
    m[0].metric("Kontovärde", f"{usd_to_sek(acc['equity'], usd_sek):,.0f} SEK",
                help=f"Allt tillsammans: aktier + parkerat + kontanter ({acc['equity']:,.2f} USD).")
    m[1].metric(f"I aktier ({len(trades)} st)", f"{usd_to_sek(stock_val, usd_sek):,.0f} SEK",
                help="Robotens egna affärer (swing och daytrading).")
    m[2].metric("Parkerat i S&P 500", f"{usd_to_sek(park_val, usd_sek):,.0f} SEK",
                help="Ledigt kapital som väntar i SPY. Säljs automatiskt när roboten köper aktier.")
    m[3].metric("Kontanter", f"{usd_to_sek(acc['cash'], usd_sek):,.0f} SEK",
                help="Daytrading-andelen och en liten buffert ligger som kontanter. Resten parkeras i SPY.")
    m[4].metric("Resultat sedan start", f"{usd_to_sek(acc['equity'] - acc['starting_cash'], usd_sek):+,.0f} kr",
                f"{(acc['equity'] / acc['starting_cash'] - 1) * 100:+.2f} %",
                help="Inklusive courtage och slippage, även för att köpa och sälja SPY. "
                     "Klicka 'Uppdatera kurser' för aktuellt värde.")
    show_pnl_by_strategy(broker, prices)

    # ------------------------------------------------ dagens bästa trades (swing + daytrading)
    st.divider()
    st.subheader("⚡ Dagens bästa trades")
    is_open, status_text = market_status()
    (st.success if is_open else st.warning)(status_text + ("" if is_open else
        " Roboten köper först när börsen är öppen."))
    st.caption(f"Roboten handlar **både swing** ({settings.swing_allocation_pct:.0f} % av kontot, strategi "
               f"**{swing_strategy_name}**, din bevakningslista) **och daytrading** "
               f"({settings.day_allocation_pct:.0f} %, strategi **{settings.day_variant}**, de bästa bland de 300 "
               "mest omsatta). Den köper och säljer, "
               "flyttar stop loss uppåt på vinnare, stänger alla dagpositioner före 22:00 och använder parkerade "
               "pengar i SPY när det behövs. Andelarna ställs in i sidopanelen.")
    d1, d2, d3 = st.columns(3)
    dt_universe = d1.radio("Daytrading bland", [LIQUID, "Min bevakningslista"], key="dt_universe")
    dt_top = d2.slider("Max antal daytrades", 1, 20, int(settings.auto_day_top_n), key="dt_top",
                       help=f"Bakgrundsroboten på GitHub använder {settings.auto_day_top_n} (från config.json).")
    dt_maxmove = d3.slider("Max prisrörelse sedan signalen, inkl. förhandel (%)", 0.5, 10.0,
                           float(settings.auto_max_move_pct), 0.5, key="dt_maxmove",
                           help="Har aktien rört sig mer än så här sedan signalen köps den inte.")
    with st.expander("Swing-lista (din bevakningslista)"):
        swing_syms = parse_symbols(st.text_area("Aktier för swing", ",".join(settings.watchlist), height=70,
                                                key="paper_syms"))

    tick_state = st.session_state.setdefault("tick_state", {})

    def run_tick(force):
        universe = (liquid_symbols(300) if dt_universe == LIQUID else list(settings.watchlist)) \
            if (force or not tick_state.get("day_list")) and settings.day_allocation_pct > 0 else []
        if not universe and settings.day_allocation_pct > 0:
            universe = tick_state.get("day_list") or list(settings.watchlist)
        bar = st.progress(0.0, text="Hämtar kurser…")
        events = tick(provider, broker, settings, tick_state, universe, swing_strategy=swing_strategy,
                      market_bars=market_bars_for(swing_strategy), usd_sek=usd_sek, top_n=dt_top,
                      max_move_pct=dt_maxmove, force=force, swing_symbols=swing_syms,
                      progress=lambda i, n: bar.progress(i / n, text=f"Hämtar kurser: {i} av {n} aktier"))
        bar.empty()
        st.session_state["paper_actions"] = events or [{"symbol": "", "action": "inget att göra",
                                                        "detail": "Inga köp eller sälj just nu."}]
        log = st.session_state.setdefault("auto_log", [])
        buys = sum(e["action"] == "KÖP" for e in events)
        sells = sum(e["action"] == "SÄLJ" for e in events)
        swing_n = sum(e["typ"] == "swing" and e["action"] in ("KÖP", "SÄLJ") for e in events)
        log.insert(0, f"{datetime.now(ZoneInfo('Europe/Stockholm')):%H:%M} runda: {buys} köp, {sells} sälj "
                      f"(varav swing {swing_n})")
        del log[50:]

    st.markdown("**Handelsrundor**")
    r0, r1, r2, r3 = st.columns(4)
    if r0.button("▶️ Kör handelsrunda nu", type="primary", width="stretch", disabled=not is_open,
                 help="Söker dagens bästa (swing + daytrading), köper och säljer direkt."):
        try:
            run_tick(force=True)
        except Exception as e:
            st.error(f"Något gick fel: {e}")
        st.rerun()
    if r1.button("🔎 Visa dagens bästa (köper inte)", width="stretch",
                 help="Visar rangordningen för swing och daytrading utan att handla."):
        try:
            prices_v = {}
            uni = liquid_symbols(300) if dt_universe == LIQUID else list(settings.watchlist)
            ranked, _a, _p = day_round(provider, broker, settings, uni, top_n=dt_top,
                                       day_share=settings.day_allocation_pct, max_move_pct=dt_maxmove,
                                       is_open=False)
            from trading_platform.autotrader import DAY_COLS, SWING_COLS, _records
            tick_state["day_top"] = _records(ranked, DAY_COLS)
            tick_state["day_list"] = ranked.symbol.head(30).tolist() if len(ranked) else []
            rows = scan(swing_syms, False, True, provider=provider, settings=settings, usd_sek=usd_sek,
                        swing_strategy=swing_strategy, market_bars=market_bars_for(swing_strategy))
            tick_state["swing_top"] = _records(pd.DataFrame(rows), SWING_COLS, n=len(rows))
            tick_state["last_search_time"] = datetime.now(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%d %H:%M")
        except Exception as e:
            st.error(f"Något gick fel: {e}")
        st.rerun()
    if r2.button("🔄 Uppdatera kurser", disabled=not broker.positions, width="stretch"):
        st.session_state["paper_prices"] = latest_prices(provider, list(broker.positions))
        st.rerun()
    day_open = [s for s, p in broker.positions.items() if p.get("strategy") == "day"]
    if r3.button("⏹ Stäng alla dagpositioner", disabled=not day_open, width="stretch"):
        prices_c = latest_prices(provider, set(day_open))
        st.session_state["paper_actions"] = close_all_day_positions(broker, prices_c)
        st.rerun()

    # ------------------------------------------------ autoläge
    st.markdown("**🤖 Autoläge – roboten köper och säljer själv (swing + daytrading)**")
    a1, a2 = st.columns(2)
    auto_on = a1.toggle("Autoläge på", key="auto_on",
                        help="Kör handelsrundan automatiskt under börsens öppettider. Fungerar bara så länge "
                             "den här sidan är öppen. Bakgrundsroboten på GitHub gör samma sak även när datorn är "
                             "avstängd (fliken 🤖 Automatisk handel).")
    auto_every = a2.selectbox("Handelsrunda var", [5, 10, 15], key="auto_every", format_func=lambda m: f"{m}:e minut")
    st.caption("Varje runda: kontrollerar alla positioner (stop loss, kursmål, dagens slut) och köper nya. "
               "Ny sökning efter dagens bästa – både swing och daytrading – var 30:e minut.")

    @st.fragment(run_every=60 if auto_on else None)
    def auto_runner():
        import time as _t
        if not st.session_state.get("auto_on"):
            return
        now = _t.time()
        log = st.session_state.setdefault("auto_log", [])
        if not market_status()[0]:
            if any(p.get("strategy") == "day" for p in broker.positions.values()):
                try:
                    run_tick(force=False)  # stänger kvarvarande dagpositioner
                except Exception as e:
                    log.insert(0, f"fel: {e}")
                st.rerun()
            st.caption("🤖 Autoläget väntar – börsen är stängd (öppnar 15:30 svensk tid).")
            return
        wait = st.session_state.get("auto_last", 0) + auto_every * 60 - now
        if wait > 0:
            st.caption(f"🤖 Autoläget är på. Nästa handelsrunda om cirka {max(1, round(wait / 60))} min.")
            for line in log[:8]:
                st.caption(line)
            return
        st.session_state["auto_last"] = now
        try:
            run_tick(force=False)
        except Exception as e:
            log.insert(0, f"{datetime.now(ZoneInfo('Europe/Stockholm')):%H:%M} fel: {e}")
        st.rerun()

    auto_runner()

    # ------------------------------------------------ rangordning
    if tick_state.get("day_top") or tick_state.get("swing_top"):
        with st.expander(f"Dagens bästa – rangordning (senaste sökning {tick_state.get('last_search_time', '')})",
                         expanded=not is_open):
            rows = [{"Typ": "Swing", **r} for r in tick_state.get("swing_top", [])
                    if r.get("signal") in ("CANDIDATE", "WATCH")]
            rows += [{"Typ": "Daytrade", **r} for r in tick_state.get("day_top", [])]
            r = pd.DataFrame(rows)
            if len(r):
                r["_c"] = (r["signal"] == "CANDIDATE")
                r = r.sort_values(["_c", "Typ"], ascending=[False, False]).drop(columns="_c")
                r = r.rename(columns={**COLUMN_NAMES, "score": "Poäng", "change_today_pct": "Idag %",
                                      "rel_volume": "Relativ volym", "price": "Signalpris (USD)",
                                      "ext_price": "Senaste inkl. förhandel", "ext_move_pct": "Rörelse sedan signal %"})
                if "Signal" in r:
                    r["Signal"] = r["Signal"].map(SIGNAL_NAMES).fillna(r["Signal"])
                st.dataframe(r, width="stretch", hide_index=True)

    if st.session_state.get("paper_actions"):
        st.subheader("Senaste handelsrundan")
        st.dataframe(pd.DataFrame(st.session_state["paper_actions"]).rename(
            columns={"symbol": "Aktie", "action": "Händelse", "detail": "Detaljer"}), width="stretch", hide_index=True)

    st.subheader("Öppna positioner")
    if broker.positions:
        pos = pd.DataFrame([{"Aktie": s, "Strategi": p.get("strategy"), "Antal": p["quantity"],
                             "Köpkurs": round(p["avg_price"], 2), "Senaste kurs": prices.get(s),
                             "Stop loss": p.get("stop"), "Kursmål": None if p.get("target") is None else round(p["target"], 2),
                             "Orealiserat (SEK)": None if s not in prices else round(usd_to_sek(p["quantity"] * (prices[s] - p["avg_price"]), usd_sek)),
                             "Orealiserat (USD)": None if s not in prices else round(p["quantity"] * (prices[s] - p["avg_price"]), 2),
                             "Öppnad (svensk tid)": sv_time(p["opened"])} for s, p in broker.positions.items()])
        st.dataframe(pos, width="stretch", hide_index=True)
    else:
        st.caption("Inga öppna positioner.")

    st.subheader("Affärshistorik")
    if broker.orders:
        hist = pd.DataFrame(broker.orders[::-1])
        if "time" in hist:
            hist["time"] = hist["time"].map(sv_time)
        if "pnl" in hist:
            hist.insert(hist.columns.get_loc("pnl"), "Resultat (SEK)",
                        hist["pnl"].map(lambda v: None if v is None or v != v else round(usd_to_sek(v, usd_sek))))
        hist = hist.rename(columns={"time": "Tid (svensk)", "symbol": "Aktie", "side": "Köp/sälj", "quantity": "Antal",
                                    "price": "Kurs (USD)", "fee": "Courtage (USD)", "pnl": "Resultat (USD)",
                                    "strategy": "Strategi", "reason": "Orsak"})
        st.dataframe(hist, width="stretch", hide_index=True)
        closed = [o["pnl"] for o in broker.orders if o.get("pnl") is not None and o.get("strategy") != "park"]
        if closed:
            st.caption(f"Avslutade affärer: {len(closed)} · vinstandel {sum(p > 0 for p in closed) / len(closed) * 100:.0f} % "
                       f"· summa **{usd_to_sek(sum(closed), usd_sek):+,.0f} kr** ({sum(closed):+.2f} USD)")
            fees = sum(o.get("fee", 0) for o in broker.orders)
            st.caption(f"Courtage hittills: {usd_to_sek(fees, usd_sek):,.0f} kr ({fees:.2f} USD).")

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
# ---------------------------------------------------------------- automatisk handel (GitHub)
with tab_auto:
    AUTO_DIR = PROJECT_ROOT / "paper"
    st.write("Här ser du **bakgrundsroboten**. Den körs på GitHub **var 15:e minut** under börsens öppettider "
             "(15:30–22:00 svensk tid), även när din dator är avstängd. Den handlar med **låtsaspengar**.")
    st.caption(f"Daytrading: {settings.day_allocation_pct:.0f} % av kontot, strategi **{settings.day_variant}**, "
               f"de {settings.auto_day_top_n} bästa "
               f"bland de 300 mest omsatta, ny sökning var 30:e minut, stängs före 22:00. "
               f"Swing: {settings.swing_allocation_pct:.0f} %, strategi **{settings.swing_variant}**, ny sökning var "
               f"30:e minut på din bevakningslista, stop loss kontrolleras var 15:e minut. "
               f"Inställningarna hämtas från config.json.")
    auto_acc_file = AUTO_DIR / "auto_account.json"
    if not auto_acc_file.exists():
        st.info("Bakgrundsroboten har inte handlat än. Den startar automatiskt nästa gång börsen är öppen. "
                "Sidan uppdateras när roboten har sparat (var 15:e minut).")
    else:
        auto_broker = PaperBroker(state_file=auto_acc_file)
        auto_state = {}
        try:
            auto_state = json.loads((AUTO_DIR / "auto_state.json").read_text())
        except Exception:
            pass
        auto_prices = st.session_state.get("auto_prices", {})
        acc = auto_broker.get_account(auto_prices)
        park_v = sum(p["quantity"] * auto_prices.get(sy, p["avg_price"]) for sy, p in auto_broker.positions.items()
                     if p.get("strategy") == "park")
        trades_a = {sy: p for sy, p in auto_broker.positions.items() if p.get("strategy") != "park"}
        stock_v = sum(p["quantity"] * auto_prices.get(sy, p["avg_price"]) for sy, p in trades_a.items())
        m = st.columns(5)
        m[0].metric("Kontovärde", f"{usd_to_sek(acc['equity'], usd_sek):,.0f} SEK",
                    help=f"{acc['equity']:,.2f} USD. Klicka 'Uppdatera kurser' för aktuellt värde.")
        m[1].metric(f"I aktier ({len(trades_a)} st)", f"{usd_to_sek(stock_v, usd_sek):,.0f} SEK")
        m[2].metric("Parkerat i S&P 500", f"{usd_to_sek(park_v, usd_sek):,.0f} SEK")
        m[3].metric("Kontanter", f"{usd_to_sek(acc['cash'], usd_sek):,.0f} SEK")
        m[4].metric("Resultat sedan start", f"{usd_to_sek(acc['equity'] - acc['starting_cash'], usd_sek):+,.0f} kr",
                    f"{(acc['equity'] / acc['starting_cash'] - 1) * 100:+.2f} %")
        show_pnl_by_strategy(auto_broker, auto_prices)
        if auto_state.get("last_run"):
            st.caption(f"Senaste körning: {sv_time(auto_state['last_run'])} (svensk tid).")
        if st.button("🔄 Uppdatera kurser", key="auto_refresh", disabled=not auto_broker.positions):
            st.session_state["auto_prices"] = latest_prices(provider, list(auto_broker.positions))
            st.rerun()
        st.subheader("Öppna positioner")
        if auto_broker.positions:
            st.dataframe(pd.DataFrame([{
                "Aktie": sy, "Strategi": p.get("strategy"), "Antal": p["quantity"], "Köpkurs": round(p["avg_price"], 2),
                "Senaste kurs": auto_prices.get(sy), "Stop loss": p.get("stop"),
                "Kursmål": None if p.get("target") is None else round(p["target"], 2),
                "Orealiserat (SEK)": None if sy not in auto_prices else round(usd_to_sek(p["quantity"] * (auto_prices[sy] - p["avg_price"]), usd_sek)),
                "Orealiserat (USD)": None if sy not in auto_prices else round(p["quantity"] * (auto_prices[sy] - p["avg_price"]), 2),
                "Öppnad (svensk tid)": sv_time(p["opened"])} for sy, p in auto_broker.positions.items()]),
                width="stretch", hide_index=True)
        else:
            st.caption("Inga öppna positioner.")
        closed = [o for o in auto_broker.orders if o.get("pnl") is not None and o.get("strategy") != "park"]
        if closed:
            pnl = [o["pnl"] for o in closed]
            st.caption(f"Avslutade affärer: {len(pnl)} · vinstandel {sum(x > 0 for x in pnl) / len(pnl) * 100:.0f} % "
                       f"· summa **{usd_to_sek(sum(pnl), usd_sek):+,.0f} kr** ({sum(pnl):+.2f} USD)")
        st.subheader("Logg (senaste 100 händelserna)")
        try:
            log_df = pd.read_csv(AUTO_DIR / "auto_log.csv").tail(100).iloc[::-1]
            st.dataframe(log_df.rename(columns={"tid": "Tid (svensk)", "typ": "Typ", "symbol": "Aktie",
                                                "action": "Händelse", "detail": "Detaljer"}),
                         width="stretch", hide_index=True)
        except Exception:
            st.caption("Ingen logg än.")
    st.caption("Vill du starta en körning direkt, ändra något eller börja om? Säg till i chatten så fixar jag det.")

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
