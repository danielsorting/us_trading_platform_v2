"""Test utan facit i efterhand ("point in time").

Vanliga backtester väljer bland aktierna som är mest omsatta I DAG. Men aktier
blir ofta mycket omsatta just för att de har gått bra – då vet testet redan
vilka som blev vinnare. Här väljs listan i stället om varje kvartal, bara utifrån
handeln de 60 börsdagarna FÖRE kvartalet. Roboten får bara köpa aktier som var
med på listan just då.
"""
import bisect

import pandas as pd


def pit_members(bars_by_symbol, n=300, lookback=60, min_price=5.0, freq="QS"):
    """Returnerar [(kvartalets_start, {symboler}), ...] sorterat på tid."""
    closes, dollars = {}, {}
    for sym, b in bars_by_symbol.items():
        if b is None or b.empty or "Volume" not in b:
            continue
        idx = pd.DatetimeIndex(b.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        closes[sym] = pd.Series(b["Close"].values, index=idx)
        dollars[sym] = pd.Series((b["Close"] * b["Volume"]).values, index=idx)
    if not closes:
        return []
    close = pd.DataFrame(closes).sort_index()
    dvol = pd.DataFrame(dollars).reindex(close.index)
    avg = dvol.rolling(lookback, min_periods=int(lookback * 0.8)).mean()
    out = []
    for q in pd.date_range(close.index[0], close.index[-1], freq=freq):
        before = close.index[close.index < q]
        if len(before) < lookback:
            continue
        t = before[-1]  # sista börsdagen FÖRE kvartalet – ingen framtida information
        ok = close.loc[t] >= min_price
        ranked = avg.loc[t][ok].dropna().sort_values(ascending=False)
        out.append((q, set(ranked.index[:n])))
    return out


def make_allowed(members):
    """Gör en funktion allowed(symbol, tid) för run_backtest."""
    starts = [pd.Timestamp(q) for q, _ in members]
    sets = [s for _, s in members]

    def allowed(sym, t):
        t = pd.Timestamp(t)
        if t.tzinfo is not None:
            t = t.tz_localize(None)
        i = bisect.bisect_right(starts, t) - 1
        return i >= 0 and sym in sets[i]
    return allowed


def today_members(bars_by_symbol, n=300, lookback=60, min_price=5.0):
    """Det vanliga sättet (med facit): de mest omsatta de senaste 60 dagarna."""
    rows = []
    for sym, b in bars_by_symbol.items():
        if b is None or len(b) < lookback or float(b["Close"].iloc[-1]) < min_price:
            continue
        rows.append((float((b["Close"] * b["Volume"]).iloc[-lookback:].mean()), sym))
    return {s for _, s in sorted(rows, reverse=True)[:n]}
