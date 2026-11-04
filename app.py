"""Dashboard för US Trading Platform. Starta med:  streamlit run app.py"""
import json
import os
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
from trading_platform.strategies.variants import (MARKET_SYMBOL, add_market, daily_variants, data_for,
                                                  hourly_variants, needs_market, swing_variants)
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
if os.environ.get("TP_FAKE_DATA"):  # automatiska tester: påhittade kurser, inget internet
    from trading_platform.fakedata import FakeProvider
    provider = FakeProvider()


@st.cache_data(ttl=600, show_spinner=False)
def realtime_status(_dummy=0):
    if REALTIME is None:
        return True, ("Kurser: Yahoo ✅ fungerar. Gratis och normalt bara sekunder till någon minut "
                      "efter börsen – räcker för swing och för robotens 5-minutersrundor.")
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



@st.cache_data(ttl=120, show_spinner=False)
def cached_prices(symbols):
    """Senaste kurser (hämtas automatiskt, högst varannan minut)."""
    try:
        return latest_prices(provider, list(symbols))
    except Exception:
        return {}


TYPE_NAMES = {"day": "Daytrade", "swing": "Swing", "park": "Parkerat (S&P 500)"}


def positions_table(positions, prices, fx):
    """Öppna positioner, enkelt och i kronor."""
    def kr(v, sign=False):
        return f"{v:+,.0f} kr".replace(",", " ") if sign else f"{v:,.0f} kr".replace(",", " ")

    rows = []
    for sy, p in positions.items():
        q, buy = p["quantity"], p["avg_price"]
        now = prices.get(sy)
        cost = q * buy
        row = {"Aktie": sy, "Typ": TYPE_NAMES.get(p.get("strategy"), p.get("strategy")),
               "Köpt": f"{q:g} st à {buy:,.2f} $", "Köpt för": kr(cost * fx),
               "Resultat nu": "–" if now is None else
               f"{kr(q * (now - buy) * fx, True)} ({(now / buy - 1) * 100:+.1f} %)",
               "Om kursmålet nås": "–", "Om stop loss nås": "–",
               "Köpt (svensk tid)": sv_time(p["opened"])[-5:]}
        if p.get("target") is not None:
            row["Om kursmålet nås"] = f"{kr(q * (p['target'] - buy) * fx, True)} vid {p['target']:,.2f} $"
        elif p.get("trail_atr"):
            row["Om kursmålet nås"] = "inget fast mål – stop följer med uppåt"
        if p.get("stop") is not None:
            row["Om stop loss nås"] = f"{kr(q * (p['stop'] - buy) * fx, True)} vid {p['stop']:,.2f} $"
        rows.append(row)
    order = {"Daytrade": 0, "Swing": 1}
    rows.sort(key=lambda r: order.get(r["Typ"], 2))
    return pd.DataFrame(rows)


ROBOT_RAW = "https://raw.githubusercontent.com/danielsorting/us_trading_platform_v2/main/paper/"
ROBOT_FILES = ("auto_account.json", "auto_state.json", "auto_log.csv")


@st.cache_data(ttl=60, show_spinner=False)
def _download_robot_files(minute):
    """Hämtar robotens senaste filer direkt från GitHub (webbsidans egen kopia
    uppdateras inte efter varje runda). Returnerar mappen, eller None vid fel."""
    import requests
    import tempfile
    folder = Path(tempfile.gettempdir()) / "robot_files"
    folder.mkdir(exist_ok=True)
    try:
        for name in ROBOT_FILES:
            r = requests.get(ROBOT_RAW + name, params={"t": minute}, timeout=10)
            r.raise_for_status()
            (folder / name).write_bytes(r.content)
        for name in ("best_choice.json", "best_table.csv"):
            r = requests.get(ROBOT_RAW.replace("/paper/", "/reports/") + name, params={"t": minute}, timeout=10)
            if r.ok:
                (folder / name).write_bytes(r.content)
        return str(folder)
    except Exception:
        return None


def fresh_robot_files():
    """Mappen med robotens färskaste filer (GitHub), annars webbsidans egen kopia."""
    got = _download_robot_files(datetime.now().strftime("%Y%m%d%H%M"))
    return Path(got) if got else PROJECT_ROOT / "paper"


def account_view(acct, state, log_df, key, last_run=None, price_override=None):
    """Samma vy för 🤖 Automatisk handel och 🧪 Simulerad handel: konto, resultat,
    öppna positioner, nyheter, senaste runda, logg och alla affärer."""
    acct_prices = {**cached_prices(tuple(sorted(acct.positions))), **(price_override or {})}
    acc = acct.get_account(acct_prices)
    park_v = sum(p["quantity"] * acct_prices.get(sy, p["avg_price"]) for sy, p in acct.positions.items()
                 if p.get("strategy") == "park")
    trades_a = {sy: p for sy, p in acct.positions.items() if p.get("strategy") != "park"}
    stock_v = sum(p["quantity"] * acct_prices.get(sy, p["avg_price"]) for sy, p in trades_a.items())
    m = st.columns(5)
    m[0].metric("Kontovärde", f"{usd_to_sek(acc['equity'], usd_sek):,.0f} SEK",
                help=f"Allt tillsammans: aktier + parkerat + kontanter ({acc['equity']:,.2f} USD).")
    m[1].metric(f"I aktier ({len(trades_a)} st)", f"{usd_to_sek(stock_v, usd_sek):,.0f} SEK",
                help="Robotens egna affärer (swing och daytrading).")
    m[2].metric("Parkerat i S&P 500", f"{usd_to_sek(park_v, usd_sek):,.0f} SEK",
                help="Ledigt kapital som väntar i SPY. Säljs automatiskt när roboten köper aktier.")
    m[3].metric("Kontanter", f"{usd_to_sek(acc['cash'], usd_sek):,.0f} SEK",
                help="Daytrading-andelen och en liten buffert ligger som kontanter. Resten parkeras i SPY.")
    m[4].metric("Resultat sedan start", f"{usd_to_sek(acc['equity'] - acc['starting_cash'], usd_sek):+,.0f} kr",
                f"{(acc['equity'] / acc['starting_cash'] - 1) * 100:+.2f} %",
                help="Inklusive courtage och slippage.")
    show_pnl_by_strategy(acct, acct_prices)
    if last_run:
        st.caption(f"Senaste körning: {last_run} (svensk tid).")

    st.subheader("Öppna positioner")
    if acct.positions:
        st.dataframe(positions_table(acct.positions, acct_prices, usd_sek), width="stretch", hide_index=True)
        st.caption("Kurserna uppdateras automatiskt varannan minut. 'Resultat nu' är före försäljningens courtage.")
    else:
        st.caption("Inga öppna positioner.")
    closed = [o for o in acct.orders if o.get("pnl") is not None and o.get("strategy") != "park"]
    if closed:
        pnl = [o["pnl"] for o in closed]
        st.caption(f"Avslutade affärer: {len(pnl)} · vinstandel {sum(x > 0 for x in pnl) / len(pnl) * 100:.0f} % "
                   f"· summa **{usd_to_sek(sum(pnl), usd_sek):+,.0f} kr** ({sum(pnl):+.2f} USD)")

    mn = (state or {}).get("market_news") or {}
    with st.expander(f"📰 Nyheter som roboten läser – världsläget: {mn.get('mood', 'ej läst än')}",
                     expanded=bool(mn.get("risk_off"))):
        st.caption("Roboten läser Yahoo Finance-nyheter om börsen, olja, Trump/tullar och räntor var 30:e minut, "
                   "och nyheterna om varje aktie innan den köper. Tydligt dåliga bolagsnyheter (t.ex. nyemission, "
                   "stämning, utredning, sänkt prognos) stoppar köpet. Är världsläget tydligt negativt halveras "
                   "risken per ny affär. Det går inte att backtesta nyheter bakåt, så se det som ett extra skydd.")
        if mn:
            (st.error if mn.get("risk_off") else st.success if mn.get("mood") == "positivt" else st.info)(
                f"Marknadshumör **{mn.get('mood')}** (snittpoäng {mn.get('score')} per nyhet, {mn.get('count')} "
                f"nyheter, läst {sv_time(mn['ts'])})"
                + (" – **risken per ny affär är halverad**." if mn.get("risk_off") else "."))
            for h in mn.get("headlines", []):
                icon = "🔴" if h["score"] < 0 else "🟢" if h["score"] > 0 else "⚪"
                st.markdown(f"{icon} [{h['title']}]({h['link']})" if h.get("link") else f"{icon} {h['title']}")
        blocked = {k: v for k, v in ((state or {}).get("news") or {}).items() if v.get("block")}
        if blocked:
            st.markdown("**Aktier som inte köptes på grund av nyheter:**")
            for sym, v in blocked.items():
                st.markdown(f"- **{sym}**: \"{v.get('worst')}\" (poäng {v.get('score')})")

    st.subheader("Senaste runda")
    for typ, name in (("day", "📅 Daytrading"), ("swing", "🔁 Swing"), ("nyheter", "📰 Nyheter")):
        rows = log_df[log_df.typ == typ] if log_df is not None and len(log_df) and "typ" in log_df else []
        if len(rows):
            r = rows.iloc[-1]
            st.info(f"**{name}** ({r.tid}): {r.action} – {r.detail}")
        else:
            st.info(f"**{name}**: ingen händelse än.")

    st.subheader("Logg (senaste 100 händelserna)")
    if log_df is not None and len(log_df):
        st.dataframe(log_df.tail(100).iloc[::-1].rename(columns={
            "tid": "Tid (svensk)", "typ": "Typ", "symbol": "Aktie", "action": "Händelse", "detail": "Detaljer"}),
            width="stretch", hide_index=True)
    else:
        st.caption("Ingen logg än.")

    with st.expander("📜 Alla affärer"):
        if acct.orders:
            hist = pd.DataFrame(acct.orders[::-1])
            if "time" in hist:
                hist["time"] = hist["time"].map(sv_time)
            if "pnl" in hist:
                hist.insert(hist.columns.get_loc("pnl"), "Resultat (SEK)",
                            hist["pnl"].map(lambda v: None if v is None or v != v else round(usd_to_sek(v, usd_sek))))
            hist = hist.rename(columns={"time": "Tid (svensk)", "symbol": "Aktie", "side": "Köp/sälj",
                                        "quantity": "Antal", "price": "Kurs (USD)", "fee": "Courtage (USD)",
                                        "pnl": "Resultat (USD)", "strategy": "Strategi", "reason": "Orsak"})
            keep = [c for c in ["Tid (svensk)", "Aktie", "Strategi", "Köp/sälj", "Antal", "Kurs (USD)",
                                "Resultat (SEK)", "Orsak"] if c in hist]
            st.dataframe(hist[keep], width="stretch", hide_index=True)
            fees = sum(o.get("fee", 0) for o in acct.orders)
            st.caption(f"Courtage hittills: {usd_to_sek(fees, usd_sek):,.0f} kr ({fees:.2f} USD).")
        else:
            st.caption("Inga affärer än.")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def omx_close():
    """Svenska börsindexet OMXS30 (dagsdata, 10 år) – för jämförelse."""
    try:
        df = provider.history("^OMX", "10y", "1d")
        return None if df is None or not len(df) else df.Close
    except Exception:
        return None


def add_omx(df):
    """Lägger till kolumnen 'OMXS30 %' (svenska indexet under samma testperiod) bredvid S&P 500."""
    if df is None or not len(df) or "Testperiod" not in df:
        return df
    omx = omx_close()
    vals = []
    for per in df["Testperiod"]:
        try:
            a, b = [pd.Timestamp(x.strip()) for x in str(per).split("–")]
            vals.append(period_return(omx, a, b + pd.Timedelta(days=1)) if omx is not None else None)
        except Exception:
            vals.append(None)
    out = df.copy()
    pos = list(out.columns).index("S&P 500 %") + 1 if "S&P 500 %" in out else len(out.columns)
    out.insert(pos, "OMXS30 %", vals)
    return out

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


# Resultat från 🏆-knappen ställs in innan reglagen ritas (Streamlit kräver det).
if "pending_best" in st.session_state:
    use_best(*st.session_state.pop("pending_best"))


from trading_platform.status import render_status_box
render_status_box(provider, PROJECT_ROOT / "paper" / "auto_state.json")

st.title("US Trading Platform")
st.caption("Automatisk handel · Scanner · Backtest · Simulerad handel — inga riktiga order skickas")

tab_auto, tab_scan, tab_bt, tab_paper, tab_info = st.tabs(
    ["🤖 Automatisk handel", "🔎 Scanner", "📈 Backtest", "🧪 Simulerad handel", "ℹ️ Om"])


def settings_panel(base, prefix, disabled, risk_key=None, variant_key=None, left_extra=None, right_extra=None):
    """Reglagen för kapital och risk. Varje flik har sina egna:
    - 🤖 Automatisk handel: robotens inställningar, låsta (bara för att visa)
    - 🔎 Scanner, 📈 Backtest, 🧪 Simulerad handel: egna reglage som går att ändra.
      Alla börjar med robotens inställningar."""
    k = lambda name: f"{prefix}_{name}"
    a, b, c = st.columns(3)
    capital = a.number_input("Totalt kapital (SEK)", min_value=1000, value=int(base.total_capital), step=1000,
                             disabled=disabled, key=k("cap"))
    day_pct = a.slider("Andel till daytrading (%)", 0, 100, int(base.day_allocation_pct), 5,
                       disabled=disabled, key=k("day"))
    a.caption(f"Andel till swingtrading: {100 - day_pct} %")
    if left_extra:
        with a:
            left_extra()
    if disabled:
        risk = b.select_slider("Risk per affär (% av strategins kapital)", options=RISK_OPTIONS,
                               value=float(base.risk_per_trade_pct), disabled=True, key=k("risk"))
    elif risk_key:
        risk = b.select_slider("Risk per affär (% av strategins kapital)", options=RISK_OPTIONS, key=risk_key)
    else:
        risk = b.select_slider("Risk per affär (% av strategins kapital)", options=RISK_OPTIONS,
                               value=float(base.risk_per_trade_pct), key=k("risk"))
    max_pos = b.slider("Max antal positioner per strategi", 1, 10, int(base.max_positions_per_strategy),
                       disabled=disabled, key=k("maxpos"))
    max_pos_pct = b.slider("Max storlek per swingaffär (% av swingkapitalet)", 5, 100,
                           int(base.max_position_pct_of_strategy_capital), 5, disabled=disabled, key=k("maxpct"))
    day_pct_max = b.slider("Max storlek per daytrade (% av daytrading-kapitalet)", 5, 100,
                           int(base.day_max_position_pct or base.max_position_pct_of_strategy_capital), 5,
                           disabled=disabled, key=k("daymaxpct"))
    fractional = c.checkbox("Tillåt delaktier", value=base.allow_fractional_shares, disabled=disabled,
                            key=k("frac"), help="T.ex. 0,35 aktie. Avanza säljer bara hela aktier, därför är "
                                                "det avstängt för att likna verkligheten.")
    park = c.checkbox("Parkera ledigt kapital i S&P 500 (SPY)", value=base.park_idle_in_index, disabled=disabled,
                      key=k("park"), help="Pengar som inte används i affärer ligger i SPY och växer med börsen. "
                                          "SPY säljs automatiskt när roboten behöver pengar till ett köp.")
    if disabled:
        c.selectbox("Swing-strategi", [base.swing_variant], disabled=True, key=k("var"))
        c.selectbox("Daytrading-strategi", [base.day_variant], disabled=True, key=k("dvar"))
        variant = base.swing_variant
    elif variant_key:
        variant = c.selectbox("Swing-strategi (ställs in automatiskt av 🏆-knappen)", list(VARIANTS),
                              key=variant_key)
        c.caption(f"Daytrading-strategi: **{base.day_variant}**")
    else:
        names = list(VARIANTS)
        variant = c.selectbox("Swing-strategi", names, key=k("var"),
                              index=names.index(base.swing_variant) if base.swing_variant in names else 0)
        c.caption(f"Daytrading-strategi: **{base.day_variant}**")
    out = replace(base, total_capital=float(capital), day_allocation_pct=float(day_pct),
                  swing_allocation_pct=float(100 - day_pct), risk_per_trade_pct=float(risk),
                  max_positions_per_strategy=max_pos, max_position_pct_of_strategy_capital=float(max_pos_pct),
                  allow_fractional_shares=fractional, park_idle_in_index=park,
                  day_max_position_pct=float(day_pct_max))
    if right_extra:
        with c:
            right_extra()
    st.caption((f"💰 Daytrading-budget **{out.day_capital:,.0f} kr** · Swing-budget **{out.swing_capital:,.0f} kr** · "
                f"Max risk per swingaffär **{out.swing_capital * out.risk_per_trade_pct / 100:,.0f} kr**")
               .replace(",", " "))
    return out, variant


from trading_platform import autopick  # noqa: E402

AUTO_DIR = fresh_robot_files()
_choice_file = AUTO_DIR / "best_choice.json"
robot_settings, robot_choice = autopick.apply(
    defaults, VARIANTS, path=_choice_file if _choice_file.exists() else autopick.CHOICE_FILE)

with tab_auto:
    with st.expander("⚙️ Robotens inställningar (låsta – roboten på GitHub använder dessa)", expanded=False):
        if robot_choice:
            st.success(f"🤖 **Väljs automatiskt varje natt.** Senaste val ({robot_choice['date']}): "
                       f"**{robot_settings.swing_variant}**, risk **{robot_settings.risk_per_trade_pct:g} %** per "
                       f"affär, de **{robot_settings.swing_liquid_n} mest omsatta** aktierna. "
                       f"I testet: {robot_choice['per_year_pct']:+.1f} % per år, största nedgång "
                       f"{robot_choice['max_drawdown_pct']:.0f} %.")
        elif defaults.auto_pick_best:
            st.info("🤖 Automatiskt val är påslaget. Nattrapporten väljer bästa strategi, risk och antal aktier "
                    "i natt – tills dess används inställningarna nedan.")
        st.caption("Reglagen är låsta så att inget ändras av misstag. Vill du ändra något – säg till Claude, "
                   "så ändras det i robotens inställningsfil (config.json).")
        settings_panel(robot_settings, "rb", disabled=True)



def tab_settings(prefix, title, note, risk_key=None, variant_key=None, left_extra=None, right_extra=None):
    """Fliken egna inställningar i en ihopfälld ruta. Returnerar (inställningar, strateginamn, rutan) –
    rutan används för att lägga fler val i den längre ner i fliken."""
    box = st.expander(f"⚙️ {title}", expanded=False)
    with box:
        st.caption(note + " Alla reglage börjar med robotens inställningar. Ändringarna påverkar INTE "
                   "bakgrundsroboten och gäller tills sidan laddas om.")
        out = settings_panel(defaults, prefix, disabled=False, risk_key=risk_key, variant_key=variant_key,
                             left_extra=left_extra, right_extra=right_extra)
    return out[0], out[1], box


def short_settings(s_, v_):
    return (f"{s_.total_capital:,.0f} SEK · daytrading {s_.day_allocation_pct:.0f} % · risk "
            f"{s_.risk_per_trade_pct:g} % per affär · swing-strategi {v_}").replace(",", " ")


usd_sek, fx_source = get_usd_sek(defaults.usd_sek_fallback)
with st.sidebar:
    st.metric("USD/SEK", f"{usd_sek:.2f}", help=f"Källa: {fx_source}")

# ---------------------------------------------------------------- scanner
with tab_scan:
    settings, variant_name, opt_box = tab_settings("scan", "Inställningar för scannern",
                                          "Används för att räkna hur stor varje affär blir.")
    risk, swing_strategy, swing_strategy_name = settings.risk_per_trade_pct, VARIANTS[variant_name], variant_name
    st.caption("Inställningar: " + short_settings(settings, variant_name))
    with opt_box:
        mode = st.radio("Vilka aktier?", [LIQUID, "Min bevakningslista"], horizontal=True)
        if mode == "Min bevakningslista":
            symbols = parse_symbols(st.text_area("Aktier (kommaseparerade)", ",".join(settings.watchlist),
                                                 height=80))
            n_liquid = None
        else:
            n_liquid = st.slider("Antal aktier (de mest omsatta)", 50, 1000, 300, 50, key="scan_n")
            st.caption("Hela marknaden är cirka 6 000 aktier. Roboten tar de mest omsatta (pris minst 5 USD). "
                       "Första gången per 6 timmar tar det några minuter.")
            symbols = ["(hämtas vid skanning)"]
        chosen = st.multiselect("Strategier", list(STRATEGIES), default=list(STRATEGIES))
    scan_status = st.empty()
    if st.button("Kör skanning", type="primary", disabled=not symbols or not chosen):
        if n_liquid:
            symbols = liquid_symbols(n_liquid)
        bar = scan_status.progress(0.0, text="⏳ Hämtar kurser…")
        fast = PrefetchedProvider(provider)
        if "Swingtrading" in chosen:
            fast.add(get_history_many(tuple(symbols), "2y", "1d"), "2y", "1d")
            if getattr(swing_strategy, "DATA", None):
                fast.add(get_history_many(tuple(symbols), *swing_strategy.DATA), *swing_strategy.DATA)
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
    bt_mode = st.radio("Vad vill du göra?",
                       ["🏆 Hitta bästa automatiskt", "Jämför swing-varianter", "Testa en strategi i detalj"],
                       horizontal=True, key="bt_mode")
    if bt_mode == "🏆 Hitta bästa automatiskt":
        strat_name, module = "__best__", swing
        bt_help = ("Testar först **alla** strategier snabbt, och sedan bara de **3 bästa** vidare med risk 0,5 %, "
                   "1 % och 2 %. Godkänd = plus i både test- och kontrolldel och minst 20 affärer. Poäng = "
                   "avkastning per år / största nedgång.")
    elif bt_mode == "Jämför swing-varianter":
        strat_name, module = None, swing
        bt_help = ("Alla varianter på samma aktier och period. De första 70 % är *testdel*, de sista 30 % "
                   "*kontrolldel* – en bra strategi går plus i båda.")
    else:
        choices = {**{k: v for k, v in VARIANTS.items()}, **day_variants()}
        strat_name = st.selectbox("Strategi", list(choices), key="bt_strat")
        module = choices[strat_name]
        bt_help = "Köp sker alltid på stapeln **efter** signalen, och courtage och slippage räknas med."
    hourly_mod = getattr(module, "DATA", None) is not None
    bt_opts = {}

    def bt_left():
        if hourly_mod:
            bt_opts["period"], bt_opts["interval"] = data_for(module, backtest=True)
            st.caption("Timgraf: Yahoo har cirka 2 år bakåt.")
        elif getattr(module, "NAME", "") != "day":
            per = {"1 år": "1y", "2 år": "2y", "5 år": "5y", "10 år": "10y"}
            bt_opts["period"], bt_opts["interval"] = per[st.select_slider("Period", list(per), value="5 år")], "1d"
        else:
            per = {"5 dagar": "5d", "1 månad": "1mo", "60 dagar": "60d"}
            bt_opts["period"] = per[st.select_slider("Period", list(per), value="60 dagar",
                                                     help="Yahoo har 5-minutersdata cirka 60 dagar bakåt.")]
            bt_opts["interval"] = "5m"
        bt_opts["cap"] = st.number_input("Startkapital för testet (SEK)", min_value=1000, value=100_000,
                                         step=10_000, key="bt_startcap")

    def bt_right():
        bt_opts["choose_risk"] = st.checkbox("Låt 🏆 även välja risk per affär", value=True, key="bt_choose_risk",
                                             help="De 3 bästa strategierna testas även med 0,5 %, 1 % och 2 %.")
        bt_opts["hourly"] = st.checkbox("Ta med kort swing (timgraf)", value=False, key="bt_hourly",
                                        help="Mycket mer data – testet blir betydligt långsammare.")

    settings, variant_name, opt_box = tab_settings("bt", "Inställningar för backtestet",
                                                   "Testet körs med de här inställningarna.", risk_key="sb_risk",
                                                   variant_key="sb_variant", left_extra=bt_left,
                                                   right_extra=bt_right)
    risk, swing_strategy, swing_strategy_name = settings.risk_per_trade_pct, VARIANTS[variant_name], variant_name
    period, interval, bt_cap_sek = bt_opts["period"], bt_opts["interval"], bt_opts["cap"]
    with_hourly = bt_opts["hourly"]
    with opt_box:
        bt_list = st.radio("Aktielista", [LIQUID, "Min bevakningslista (30 stora + IREN, NBIS)", "Egen lista"],
                           horizontal=True, key="bt_list",
                           help="Hela USA-marknaden är samma aktier som roboten handlar och gav bäst resultat. "
                                "Obs: dagens mest omsatta – historiska test blir därför något för positiva.")
        if bt_list == LIQUID:
            bt_symbols = ["(hämtas vid test)"]
        else:
            bt_default = ",".join(settings.watchlist) if bt_list.startswith("Min bevakningslista") else ""
            bt_symbols = parse_symbols(st.text_area("Aktier", bt_default, height=68, key=f"bt_syms_{bt_list}"))
    st.caption(bt_help)
    if strat_name == "__best__":
        _night = next((f for f in (AUTO_DIR / "best_table.csv", PROJECT_ROOT / "reports" / "best_table.csv")
                       if f.exists()), None)
        if _night is not None:
            try:
                night = pd.read_csv(_night)
                with st.expander(f"🌙 Nattens test – klart direkt ({night['Testat'].iloc[0]}, robotens inställningar, "
                                 "5 år, 300 och 600 mest omsatta)", expanded=True):
                    st.caption("Samma test som 🏆-knappen, men gjort i natt med alla strategier × risk 0,5/1/2 %. "
                               "Kör knappen själv bara om du har ändrat något under ⚙️.")
                    st.dataframe(night.drop(columns=["Testat"], errors="ignore").head(10), width="stretch",
                                 hide_index=True)
            except Exception:
                pass
    bt_status = st.empty()  # förloppet visas här uppe, så du slipper scrolla
    st.caption((f"Test: {'2 år (timgraf)' if hourly_mod else period.replace('y', ' år').replace('mo', ' mån')} · "
                f"{'300 mest omsatta' if bt_list == LIQUID else bt_list} · startkapital {bt_cap_sek:,.0f} kr · "
                + short_settings(settings, variant_name)).replace(",", " ") + " – ändras under ⚙️.")

    if st.button("Kör backtest", type="primary", disabled=not bt_symbols):
        bt_status.progress(0.0, text="⏳ Startar testet…")
        if bt_list == LIQUID:
            bt_symbols = liquid_symbols(300)
        def show(frac, text):
            bt_status.progress(min(max(frac, 0.0), 1.0), text=f"⏳ {text}")

        show(0.02, f"Hämtar historiska kurser för {len(bt_symbols)} aktier…")
        bars = get_history_many(tuple(bt_symbols), period, interval)
        missing = [s for s, b in bars.items() if b is None or len(b) < module.MIN_BARS]
        if missing:
            st.warning("För lite data för: " + ", ".join(missing))
        cap_usd = sek_to_usd(bt_cap_sek, usd_sek)
        mkt = None
        if getattr(module, "NAME", "") != "day" and not hourly_mod:
            show(0.08, "Hämtar S&P 500 för jämförelse…")
            mkt = market_bars_for(VARIANTS["Swing + marknadsfilter"], period)
            bars = {s: add_market(b, mkt) for s, b in bars.items()}
        hbars = None
        if strat_name in ("__best__", None) and with_hourly:  # timgrafs-varianterna testas på timdata
            show(0.12, "Hämtar timdata för kort swing…")
            hbars = get_history_many(tuple(bt_symbols), *data_for(next(iter(hourly_variants().values())), True))
        if strat_name == "__best__":
            pick_risk = bt_opts["choose_risk"]
            base_risk = settings.risk_per_trade_pct
            more_risks = tuple(r for r in (0.5, 1.0, 2.0) if r != base_risk) if pick_risk else ()
            groups = [(bars, daily_variants(), "dagsdata")] + ([(hbars, hourly_variants(), "timgraf")] if hbars else [])
            n_first = sum(len(v) for _, v, _ in groups)
            total = n_first + 3 * len(more_risks)
            done = [0]

            def step(label):
                return lambda i, n: show(0.15 + 0.85 * (done[0] + i) / total,
                                         f"Testat {done[0] + i} av {total} kombinationer ({label})…")

            # Steg 1: alla strategier snabbt med risken i inställningarna.
            parts = []
            for b_, vars_, label in groups:
                d = find_best(b_, vars_, cap_usd, settings, benchmark=None if mkt is None else mkt.Close,
                              risk_levels=(base_risk,), progress=step(f"steg 1: {label}"))
                d.attrs["_bars"], d.attrs["_vars"] = label, vars_
                parts.append(d)
                done[0] += len(vars_)
            # Steg 2: bara de 3 bästa testas vidare med andra risknivåer.
            if more_risks:
                first = pd.concat([d for d in parts if len(d)], ignore_index=True)
                first["_ok"] = first["Godkänd"] == "ja"
                top3 = first.sort_values(["_ok", "Poäng"], ascending=[False, False]).Strategi.head(3).tolist()
                for b_, vars_, label in groups:
                    sub = {k: v for k, v in vars_.items() if k in top3}
                    if sub:
                        parts.append(find_best(b_, sub, cap_usd, settings,
                                               benchmark=None if mkt is None else mkt.Close, risk_levels=more_risks,
                                               progress=step(f"steg 2: de 3 bästa med fler risknivåer")))
                        done[0] += len(sub) * len(more_risks)
            for d in parts:
                d["Testperiod"] = d.attrs.get("period", "")
            best_all = pd.concat([d for d in parts if len(d)], ignore_index=True)
            best_all["_ok"] = best_all["Godkänd"] == "ja"
            best_all = best_all.sort_values(["_ok", "Poäng"], ascending=[False, False]).drop(columns="_ok")
            best_all = best_all.reset_index(drop=True)
            best_all.attrs["period"] = " / ".join(dict.fromkeys(d.attrs.get("period", "") for d in parts))
            st.session_state["bt_best"] = add_omx(best_all)
            bt_status.empty()
            st.session_state.pop("bt", None)
            st.session_state.pop("bt_compare", None)
            best = st.session_state["bt_best"]
            if len(best) and best.iloc[0]["Godkänd"] == "ja":
                # Ställ in vinnaren automatiskt i scanner och simulerad handel.
                st.session_state["pending_best"] = (
                    best.iloc[0]["Strategi"], float(best.iloc[0]["Risk per affär %"]) if pick_risk else None)
                st.rerun()
        elif strat_name is None:
            show(0.4, "Jämför alla swing-varianter…")
            if True:
                cmp_parts = [compare_strategies(bars, daily_variants(), cap_usd, settings,
                                                benchmark=None if mkt is None else mkt.Close)]
                if hbars:
                    cmp_parts.append(compare_strategies(hbars, hourly_variants(), cap_usd, settings,
                                                        benchmark=None if mkt is None else mkt.Close))
                st.session_state["bt_compare"] = add_omx(pd.concat([c for c in cmp_parts if len(c)], ignore_index=True)
                                                         .sort_values("Avkastning %", ascending=False)
                                                         .reset_index(drop=True))
                st.session_state["bt_bh"] = buy_and_hold_by_symbol(bars)
                bt_status.empty()
            st.session_state.pop("bt", None)
            st.session_state.pop("bt_best", None)
        else:
            st.session_state.pop("bt_best", None)
            show(0.5, f"Kör backtest för {strat_name}…")
            if True:
                res = run_backtest(bars, module, cap_usd, settings)
                eq = res["equity"]
                bench = omx_v = None
                if len(eq):
                    # Jämför från första dagen strategin kunde handla (efter uppvärmning).
                    times = sorted(set().union(*[b.index for b in bars.values() if b is not None]))
                    start = times[min(module.MIN_BARS, len(times) - 1)]
                    spy_close = mkt.Close if mkt is not None else get_history(MARKET_SYMBOL, "5y", "1d").Close
                    bench = period_return(spy_close, start)
                    omx_v = period_return(omx_close(), start)
                st.session_state["bt"] = (strat_name, res, bench, omx_v)
                bt_status.empty()
            st.session_state.pop("bt_compare", None)

    if st.session_state.get("best_applied"):
        st.success(f"✅ Inställt automatiskt i backtestets inställningar: **{st.session_state['best_applied']}**. "
                   "Vill du att bakgrundsroboten ska använda den – säg till Claude.")

    if "bt_best" in st.session_state:
        best = st.session_state["bt_best"]
        st.subheader("🏆 Bästa kombinationen")
        if len(best) and best.iloc[0]["Godkänd"] == "ja":
            top = best.iloc[0]
            sp = "" if top["S&P 500 %"] is None or top["S&P 500 %"] != top["S&P 500 %"] else (
                f" Indexfonden S&P 500 gav {top['S&P 500 %']:+.1f} % under samma tid"
                + (f" och svenska OMXS30 {top['OMXS30 %']:+.1f} %" if top.get("OMXS30 %") == top.get("OMXS30 %")
                   and top.get("OMXS30 %") is not None else "")
                + ", så den " + ("**slog indexfonden**." if top["Slår S&P 500?"] == "ja" else "**slog inte indexfonden**."))
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
                omx_v = cmp["OMXS30 %"].iloc[0] if "OMXS30 %" in cmp else None
                msg = (f"S&P 500 (indexfond) gav **{bench:+.1f} %** under samma period"
                       + (f", svenska OMXS30 **{omx_v:+.1f} %**" if omx_v is not None and omx_v == omx_v else "")
                       + f". {beats} av {len(cmp)} varianter slog S&P 500.")
                (st.info if beats else st.warning)(msg)
            if len(good):
                st.success(f"Bäst av de som går plus i både test- och kontrolldel: **{good.iloc[0].Strategi}**. "
                           "Du kan välja den under ⚙️ inställningarna i varje flik.")
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
        name, r, bench, omx_v = (list(st.session_state["bt"]) + [None])[:4]
        s = r["stats"]
        st.subheader(f"Resultat – {name}")
        m = st.columns(5)
        m[0].metric("Avkastning", f"{s['total_return_pct']:+.1f} %")
        m[1].metric("S&P 500 (indexfond)", "–" if bench is None else f"{bench:+.1f} %",
                    help="Vad en indexfond på S&P 500 (SPY) gav under samma period. Det är den viktigaste jämförelsen.")
        m[2].metric("OMXS30 (Sverige)", "–" if omx_v is None else f"{omx_v:+.1f} %",
                    help="Vad det svenska börsindexet OMXS30 gav under samma period.")
        m[3].metric("Största nedgång", f"{s['max_drawdown_pct']:.1f} %")
        m[4].metric("Antal affärer", s["trades"])
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
    settings, variant_name, opt_box = tab_settings("sim", "Inställningar för simulerad handel",
                                          "Den simulerade roboten handlar med de här inställningarna.")
    risk, swing_strategy, swing_strategy_name = settings.risk_per_trade_pct, VARIANTS[variant_name], variant_name
    st.write("Handla med **låtsaspengar** – swing och daytrading på samma ställe. Slå på **Autoläge** så "
             "köper och säljer roboten själv medan sidan är öppen.")
    broker = PaperBroker(sek_to_usd(settings.total_capital, usd_sek), PAPER_FILE,
                         settings.commission_per_share_usd, settings.min_commission_usd,
                         settings.slippage_pct, usd_sek, settings.commission_pct, settings.fx_fee_pct)
    if settings.paper_reset_id and getattr(broker, "reset_id", None) != settings.paper_reset_id:
        # Återställningssignal från config.json: börja om med ett nytt konto (sker bara en gång).
        broker.reset(sek_to_usd(settings.total_capital, usd_sek), usd_sek, reset_id=settings.paper_reset_id)
        for k in ("paper_actions", "paper_prices", "tick_state", "auto_log", "auto_last"):
            st.session_state.pop(k, None)
        st.success("Det simulerade kontot har börjat om med ett nytt konto.")
    prices = st.session_state.get("paper_prices", {})
    sim_events = st.session_state.setdefault("sim_events", [])
    sim_log_df = pd.DataFrame(sim_events) if sim_events else None
    _last = sim_events[-1]["tid"] if sim_events else None
    account_view(broker, st.session_state.get("tick_state", {}), sim_log_df, key="sim", last_run=_last,
                 price_override=prices)

    # ------------------------------------------------ knappar (bara för simuleringen)
    st.divider()
    st.subheader("🎛️ Styr den simulerade roboten")
    is_open, status_text = market_status()
    (st.success if is_open else st.warning)(status_text + ("" if is_open else
        " Roboten köper först när börsen är öppen."))
    st.caption(f"Roboten handlar **både swing** ({settings.swing_allocation_pct:.0f} % av kontot, strategi "
               f"**{swing_strategy_name}**, "
               f"{'de 300 mest omsatta + din bevakningslista' if settings.swing_universe == 'liquid' else 'din bevakningslista'}) "
               "**och daytrading** "
               f"({settings.day_allocation_pct:.0f} %, strategi **{settings.day_variant}**, de bästa bland de 300 "
               "mest omsatta). Den köper och säljer, "
               "flyttar stop loss uppåt på vinnare, stänger alla dagpositioner före 22:00 och använder parkerade "
               "pengar i SPY när det behövs. Andelarna ställs in under ⚙️ inställningarna högst upp i fliken.")
    with opt_box:
        st.markdown("**Handelns val**")
        d1, d2, d3 = st.columns(3)
        dt_universe = d1.radio("Daytrading bland", [LIQUID, "Min bevakningslista"], key="dt_universe")
        dt_top = d2.slider("Max antal daytrades", 1, 20, int(settings.auto_day_top_n), key="dt_top",
                           help=f"Bakgrundsroboten på GitHub använder {settings.auto_day_top_n}.")
        dt_maxmove = d3.slider("Max prisrörelse sedan signalen, inkl. förhandel (%)", 0.5, 10.0,
                               float(settings.auto_max_move_pct), 0.5, key="dt_maxmove",
                               help="Har aktien rört sig mer än så här sedan signalen köps den inte.")
        swing_syms = parse_symbols(st.text_area("Extra aktier för swing (din bevakningslista)",
                                                ",".join(settings.watchlist), height=70, key="paper_syms"))

    tick_state = st.session_state.setdefault("tick_state", {})

    def run_tick(force):
        universe = (liquid_symbols(300) if dt_universe == LIQUID else list(settings.watchlist)) \
            if settings.day_allocation_pct > 0 else []
        bar = st.progress(0.0, text="Hämtar kurser…")
        swing_list = (list(dict.fromkeys(liquid_symbols(300) + swing_syms))
                      if settings.swing_universe == "liquid" else swing_syms)
        events = tick(provider, broker, settings, tick_state, universe, swing_strategy=swing_strategy,
                      market_bars=market_bars_for(swing_strategy), usd_sek=usd_sek, top_n=dt_top,
                      max_move_pct=dt_maxmove, force=force, swing_symbols=swing_list,
                      progress=lambda i, n: bar.progress(i / n, text=f"Hämtar kurser: {i} av {n} aktier"))
        bar.empty()
        st.session_state["paper_actions"] = events or [{"symbol": "", "action": "inget att göra",
                                                        "detail": "Inga köp eller sälj just nu."}]
        ev_log = st.session_state.setdefault("sim_events", [])
        ev_log.extend({k: e.get(k) for k in ("tid", "typ", "symbol", "action", "detail")} for e in events)
        del ev_log[:-300]
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
            swing_list = (list(dict.fromkeys(liquid_symbols(300) + swing_syms))
                          if settings.swing_universe == "liquid" else swing_syms)
            fast = PrefetchedProvider(provider)
            fast.add(get_history_many(tuple(swing_list), "2y", "1d"), "2y", "1d")
            if getattr(swing_strategy, "DATA", None):
                fast.add(get_history_many(tuple(swing_list), *swing_strategy.DATA), *swing_strategy.DATA)
            rows = scan(swing_list, False, True, provider=fast, settings=settings, usd_sek=usd_sek,
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
    _every = {"5:e minut": 5, "10:e minut": 10, "15:e minut": 15}
    auto_every = _every[a2.selectbox("Handelsrunda var", list(_every), key="auto_every_txt")]
    st.caption("Varje runda: kontrollerar alla positioner (stop loss, kursmål, dagens slut) och köper nya. "
               "Daytrading granskar alla 300 varje runda. Swing söker nytt var 30:e minut (dagsdata ändras långsamt).")

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
    if defaults.extended_hours_swing:
        st.write("Här ser du **bakgrundsroboten**. Den körs på GitHub **var 5:e minut**, även när din dator är "
                 "avstängd, och handlar med **låtsaspengar**: förhandel 11:00–15:30 (bara swing), börsdag "
                 "15:30–22:00 (swing och daytrading), efterhandel 22:00–22:30 (bara stop loss för swing).")
    else:
        st.write("Här ser du **bakgrundsroboten**. Den körs på GitHub **var 5:e minut** under börsens öppettider "
                 "(15:30–22:00 svensk tid), även när din dator är avstängd. Den handlar med **låtsaspengar**.")
    rb = defaults
    st.caption(f"Övrigt: daytrading väljer de {rb.auto_day_top_n} bästa bland de 300 mest omsatta och stänger före 22:00. "
               f"Swing söker bland de {robot_settings.swing_liquid_n} mest omsatta aktierna (antalet väljs i nattrapporten). "
               f"Courtage {rb.commission_pct:g} % (Avanza Pro).")
    auto_acc_file = AUTO_DIR / "auto_account.json"
    if not auto_acc_file.exists():
        st.info("Bakgrundsroboten har inte handlat än. Den startar automatiskt nästa gång börsen är öppen. "
                "Sidan uppdateras när roboten har sparat (var 5:e minut).")
    else:
        auto_broker = PaperBroker(state_file=auto_acc_file)
        auto_state = {}
        try:
            auto_state = json.loads((AUTO_DIR / "auto_state.json").read_text())
        except Exception:
            pass
        try:
            auto_log_df = pd.read_csv(AUTO_DIR / "auto_log.csv").tail(300)
        except Exception:
            auto_log_df = None
        account_view(auto_broker, auto_state, auto_log_df, key="auto",
                     last_run=sv_time(auto_state["last_run"]) if auto_state.get("last_run") else None)
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
