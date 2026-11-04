"""Månadsrotation med momentum (en beprövad idé från forskningen).

Första börsdagen varje månad:
  1. Ta aktierna som var bland de mest omsatta JUST DÅ (ingen facit, se pit.py).
  2. Räkna hur mycket varje aktie steg från ca 12 månader sedan till 1 månad sedan
     (den senaste månaden hoppas över – den brukar studsa tillbaka).
  3. Köp de K starkaste med lika mycket pengar i varje.
  4. Marknadsfilter: ligger S&P 500 under sitt 200-dagars snitt köps inga aktier –
     pengarna ligger i stället i S&P 500 eller som kontanter (inställning).
Köp och sälj sker till öppningskursen med slippage och courtage.
"""
import numpy as np
import pandas as pd

from .pit import make_allowed


def _frame(bars_by_symbol, col):
    out = {}
    for s, b in bars_by_symbol.items():
        if b is None or b.empty:
            continue
        idx = pd.DatetimeIndex(b.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        out[s] = pd.Series(b[col].values, index=idx)
    return pd.DataFrame(out).sort_index()


def run_rotation(bars_by_symbol, spy, members, capital, start, end=None, k=10, lookback=252, skip=21,
                 defensive="spy", fee_pct=0.0004, slip_pct=0.0005, min_price=5.0, market_filter=True):
    close = _frame(bars_by_symbol, "Close")
    opn = _frame(bars_by_symbol, "Open").reindex(close.index)
    sidx = pd.DatetimeIndex(spy.index).tz_localize(None) if spy.index.tz is not None else pd.DatetimeIndex(spy.index)
    spy_c = pd.Series(spy["Close"].values, index=sidx).reindex(close.index).ffill()
    spy_o = pd.Series(spy["Open"].values, index=sidx).reindex(close.index).ffill()
    sma200 = spy_c.rolling(200).mean()
    mom = close.shift(skip) / close.shift(lookback) - 1
    allowed = make_allowed(members)
    start = pd.Timestamp(start).tz_localize(None) if pd.Timestamp(start).tzinfo else pd.Timestamp(start)
    days = close.index[close.index >= start]
    if end is not None:
        e = pd.Timestamp(end)
        days = days[days < (e.tz_localize(None) if e.tzinfo else e)]
    cost = fee_pct + slip_pct
    cash, hold, spy_units = float(capital), {}, 0.0
    equity, last_month, picks_log = [], None, []
    for i, t in enumerate(days):
        pos = close.index.get_loc(t)
        if t.month != last_month and pos > 0:
            last_month = t.month
            prev = close.index[pos - 1]
            # sälj allt till öppningskurs
            for s, q in hold.items():
                px = opn.at[t, s]
                px = px if px == px else close.at[prev, s]
                if px == px:
                    cash += q * px * (1 - cost)
            hold = {}
            if spy_units:
                cash += spy_units * spy_o[t] * (1 - cost)
                spy_units = 0.0
            risk_on = (not market_filter) or (spy_c[prev] > sma200[prev])
            picks = []
            if risk_on:
                m = mom.loc[prev]
                ok = [(m[s], s) for s in m.index if m[s] == m[s] and close.at[prev, s] >= min_price
                      and allowed(s, t) and opn.at[t, s] == opn.at[t, s]]
                picks = [s for _, s in sorted(ok, reverse=True)[:k]]
            if picks:
                each = cash / len(picks)
                for s in picks:
                    px = opn.at[t, s]
                    hold[s] = each * (1 - cost) / px
                cash = 0.0
            elif defensive == "spy":
                spy_units = cash * (1 - cost) / spy_o[t]
                cash = 0.0
            picks_log.append((t, picks))
        val = cash + spy_units * spy_c[t]
        for s, q in hold.items():
            px = close.at[t, s]
            if px != px:  # saknad kurs (t.ex. avnoterad): sista kända
                px = close[s].loc[:t].dropna().iloc[-1] if close[s].loc[:t].notna().any() else 0.0
            val += q * px
        equity.append((t, val))
    eq = pd.Series(dict(equity))
    total = (eq.iloc[-1] / capital - 1) * 100 if len(eq) else 0.0
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 0.1) if len(eq) else 1
    dd = ((eq / eq.cummax()) - 1).min() * 100 if len(eq) else 0.0
    return {"equity": eq, "total_pct": round(total, 1),
            "per_year_pct": round(((1 + total / 100) ** (1 / years) - 1) * 100, 1),
            "max_drawdown_pct": round(float(dd), 1), "picks": picks_log}


def spy_stats(spy, start, end=None):
    idx = pd.DatetimeIndex(spy.index).tz_localize(None) if spy.index.tz is not None else pd.DatetimeIndex(spy.index)
    c = pd.Series(spy["Close"].values, index=idx)
    st = pd.Timestamp(start)
    st = st.tz_localize(None) if st.tzinfo else st
    c = c[c.index >= st]
    if end is not None:
        e = pd.Timestamp(end)
        c = c[c.index < (e.tz_localize(None) if e.tzinfo else e)]
    total = (c.iloc[-1] / c.iloc[0] - 1) * 100
    years = max((c.index[-1] - c.index[0]).days / 365.25, 0.1)
    return {"total_pct": round(total, 1), "per_year_pct": round(((1 + total / 100) ** (1 / years) - 1) * 100, 1),
            "max_drawdown_pct": round(float(((c / c.cummax()) - 1).min() * 100), 1)}


__all__ = ["run_rotation", "spy_stats", "np"]
