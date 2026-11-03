"""Dagens bästa daytrading-kandidater i hela USA-marknaden.

Två steg (för att det ska gå att göra med gratis data):
1. Likviditet: dagsdata för alla aktier → de N mest omsatta (pris ≥ 5 USD).
   Görs en gång per dag och sparas.
2. Daytrading: 5-minutersdata för de N aktierna → bedöms med daytrading-
   strategin och rangordnas. De bästa köpkandidaterna kan sedan handlas.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from .strategies import day

NY = ZoneInfo("America/New_York")
MIN_PRICE = 5.0


def market_status(now=None):
    """Returnerar (öppen?, text) för den amerikanska börsen (ordinarie tid)."""
    now = (now or datetime.now(NY)).astimezone(NY)
    if now.weekday() >= 5:
        return False, "Börsen är stängd (helg)."
    if now.time() < time(9, 30):
        return False, "Börsen har inte öppnat än (öppnar 15:30 svensk tid)."
    if now.time() >= time(16, 0):
        return False, "Börsen har stängt för dagen (stänger 22:00 svensk tid)."
    return True, "Börsen är öppen."


def extended_phase(now=None):
    """Avanzas handel utanför ordinarie tid i USA-aktier:
    "pre"  = förhandel 11:00–15:30 svensk tid (05:00–09:30 New York)
    "post" = efterhandel 22:00–22:30 svensk tid (16:00–16:30 New York)
    None   = ingen sådan handel just nu."""
    now = (now or datetime.now(NY)).astimezone(NY)
    if now.weekday() >= 5:
        return None
    if time(5, 0) <= now.time() < time(9, 30):
        return "pre"
    if time(16, 0) <= now.time() < time(16, 30):
        return "post"
    return None


def yahoo_symbol(sym):
    """Nasdaq-listans format (BRK.B) → Yahoos format (BRK-B)."""
    return sym.replace(".", "-")


def most_liquid(daily_by_symbol, n=300, min_price=MIN_PRICE, min_dollar_volume=0):
    """De n aktier som omsätter mest pengar per dag (snitt senaste 20 dagarna).
    n=None ger hela den sorterade listan."""
    rows = []
    for sym, df in daily_by_symbol.items():
        if df is None or len(df) < 5:
            continue
        last = float(df.Close.iloc[-1])
        dv = float((df.Close * df.Volume).tail(20).mean())
        if last >= min_price and dv >= min_dollar_volume:
            rows.append((sym, dv))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in (rows if n is None else rows[:n])]


def score_day(symbol, bars, strategy=None):
    """Bedömer en aktie för daytrading och ger den en poäng (högre = bättre)."""
    row = (strategy or day).evaluate(symbol, bars)
    if row.get("signal") not in ("CANDIDATE", "WATCH"):
        return row
    x = bars.dropna()
    last_day = x.index[-1].normalize()
    today = x[x.index.normalize() == last_day]
    rel_vol = float(x.Volume.iloc[-1] / x.Volume.tail(20).mean()) if x.Volume.tail(20).mean() > 0 else 0.0
    change = float(today.Close.iloc[-1] / today.Open.iloc[0] - 1) * 100 if len(today) else 0.0
    ok = int(row["conditions_met"].split("/")[0])
    row["rel_volume"] = round(rel_vol, 2)
    row["change_today_pct"] = round(change, 2)
    row["bars_today"] = len(today)
    row["score"] = round(ok * 100 + min(rel_vol, 5) * 10 + max(min(change, 10), -10), 1)
    return row


def rank_day_candidates(intraday_by_symbol, strategy=None):
    """Rangordnar alla aktier. Köpkandidater (alla villkor uppfyllda) överst."""
    rows = [score_day(s, b, strategy) for s, b in intraday_by_symbol.items() if b is not None]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["is_candidate"] = df.signal == "CANDIDATE"
    if "score" not in df:
        df["score"] = None
    return df.sort_values(["is_candidate", "score"], ascending=[False, False], na_position="last").reset_index(drop=True)


def add_extended_check(ranked, extended, max_move_pct=2.0):
    """Lägger till senaste pris inklusive förhandel och markerar aktier som
    rört sig för mycket sedan signalen (t.ex. stort fall i förhandeln).

    extended: {aktie: (pris, tid)} från provider.latest_extended().
    """
    if ranked is None or not len(ranked):
        return ranked
    df = ranked.copy()
    ext = df.symbol.map(lambda s: extended.get(s, (None, None))[0])
    df["ext_price"] = ext
    df["ext_move_pct"] = ((ext / df.price - 1) * 100).round(2)
    df["move_ok"] = df.ext_move_pct.abs().le(max_move_pct) | df.ext_move_pct.isna()
    return df


def liquid_universe(provider, n=300, fallback_symbols=None, allow_heavy=True):
    """De n mest omsatta USA-aktierna.

    1) Yahoos färdiga lista "most actives" (ett fåtal anrop) om den finns.
    2) Annars: dagsdata för hela Nasdaq-listan (tungt – ca 6 000 aktier).
    3) Sista utväg: fallback_symbols (t.ex. bevakningslistan).
    """
    try:
        fast = provider.most_active(n) if hasattr(provider, "most_active") else []
    except Exception:
        fast = []
    if len(fast) >= min(n, 100):
        return fast[:n]
    if fast and not allow_heavy:
        return fast[:n]
    if not allow_heavy:
        return list(fallback_symbols or [])[:n]
    try:
        from .universe import get_symbols
        uni = get_symbols("us_market", [], False)
        syms = [yahoo_symbol(x) for x in uni["symbol"].tolist() if "$" not in x]
        heavy = most_liquid(provider.history_many(syms, "1mo", "1d"), n)
        if heavy:
            return heavy
    except Exception:
        pass
    return list(fallback_symbols or [])[:n]
