"""Backtesting: hur hade en strategi gått på historiska kurser?

Regler (för att resultatet ska vara ärligt):
- Ingen framtidsinformation: signalen räknas på en stapels stängningskurs
  och köpet sker först vid NÄSTA stapels öppningskurs.
- Stop loss = signalkurs − STOP_ATR × ATR. Kursmål = TARGET_R × risken.
- Strategier med TRAIL_ATR har inget fast kursmål. I stället flyttas stop
  loss upp efter varje stängning till (högsta stängning − TRAIL_ATR × ATR),
  så att vinnare får löpa. Flytten gäller från NÄSTA stapel.
- Om kursen öppnar under stop (gap) säljs till öppningskursen, inte stop.
- Om både stop och mål nås under samma stapel antas stop (det sämre utfallet).
- Positionen säljs senast efter MAX_HOLD_BARS staplar. Daytrading stänger
  alltid vid dagens sista stapel (inga positioner över natten).
- Courtage och slippage dras på varje köp och sälj.
- Gemensam kassa för alla aktier, högst max_positions samtidigt.

Alla belopp är i USD.
"""
import pandas as pd

from .config import Settings
from .risk import commission, position_size
from .strategies import entry_signals


def _prepare(bars_by_symbol, module):
    prepared = {}
    for sym, bars in bars_by_symbol.items():
        if bars is None or len(bars) < module.MIN_BARS:
            continue
        x, sig = entry_signals(module, bars)
        x = x.assign(SIGNAL=sig)
        dates = pd.Series(x.index.normalize(), index=x.index)
        # Sista stapeln för dagen (för daytrading: tvingad stängning).
        x["LAST_OF_DAY"] = dates.ne(dates.shift(-1)).values
        prepared[sym] = x
    return prepared


def stop_and_target(module, signal_close, atr, entry):
    """Stop loss och kursmål enligt strategin (ATR-baserat eller fast procent)."""
    stop_pct = getattr(module, "STOP_PCT", None)
    target_pct = getattr(module, "TARGET_PCT", None)
    stop = signal_close * (1 - stop_pct / 100) if stop_pct else signal_close - module.STOP_ATR * atr
    if getattr(module, "TRAIL_ATR", None):
        return float(stop), float("inf")
    target = entry * (1 + target_pct / 100) if target_pct else entry + module.TARGET_R * (entry - stop)
    return float(stop), float(target)


def run_backtest(bars_by_symbol, module, capital_usd, settings=None, max_positions=None, start=None, end=None):
    """Kör ett backtest. bars_by_symbol: {"AAPL": kurstabell, ...}.

    start/end: handla bara inom detta tidsfönster (indikatorerna räknas ändå
    på all tidigare data). Öppna positioner stängs vid fönstrets slut.

    Returnerar {"trades": tabell, "equity": serie, "stats": dict}.
    """
    settings = settings or Settings()
    max_positions = max_positions or settings.max_positions_per_strategy
    slip = settings.slippage_pct / 100
    is_day = module.NAME == "day"
    data = _prepare(bars_by_symbol, module)
    if not data:
        return {"trades": pd.DataFrame(), "equity": pd.Series(dtype=float), "stats": _empty_stats(capital_usd)}

    def fee(qty, price):
        return commission(qty, settings.commission_per_share_usd, settings.min_commission_usd, price)

    timeline = sorted(set().union(*[x.index for x in data.values()]))
    if start is not None:
        timeline = [t for t in timeline if t >= pd.Timestamp(start)]
    if end is not None:
        timeline = [t for t in timeline if t < pd.Timestamp(end)]
    if not timeline:
        return {"trades": pd.DataFrame(), "equity": pd.Series(dtype=float), "stats": _empty_stats(capital_usd)}
    pos_in = {s: {t: i for i, t in enumerate(x.index)} for s, x in data.items()}
    # Snabb åtkomst till staplarna (mycket snabbare än .iloc i loopen).
    cols = ["Open", "High", "Low", "Close", "ATR14", "SIGNAL", "LAST_OF_DAY"]
    rows = {s: list(x[cols].itertuples(index=False)) for s, x in data.items()}
    cash = float(capital_usd)
    open_pos, trades, equity_points, last_close = {}, [], [], {}

    def close(sym, p, t, price, reason):
        nonlocal cash
        exit_px = price * (1 - slip)
        c = fee(p["qty"], exit_px)
        cash += p["qty"] * exit_px - c
        pnl = p["qty"] * (exit_px - p["entry"]) - p["fees"] - c
        risk = p["qty"] * (p["entry"] - p["init_stop"])
        trades.append({
            "symbol": sym, "entry_time": p["time"], "exit_time": t,
            "entry_price": round(p["entry"], 4), "exit_price": round(exit_px, 4),
            "shares": p["qty"], "stop": round(p["init_stop"], 4),
            "target": None if p["target"] == float("inf") else round(p["target"], 4),
            "pnl_usd": round(pnl, 2), "r_multiple": round(pnl / risk, 2) if risk > 0 else 0.0,
            "fees_usd": round(p["fees"] + c, 2), "bars_held": p["bars"], "exit_reason": reason,
        })
        del open_pos[sym]

    def check_exit(sym, p, bar, t, allow_time_exit=True):
        if bar.Open <= p["stop"]:
            return close(sym, p, t, bar.Open, "stop (gap)") or True
        if bar.Low <= p["stop"]:
            reason = "följande stop" if p["stop"] > p["init_stop"] else "stop"
            return close(sym, p, t, p["stop"], reason) or True
        if bar.Open >= p["target"]:
            return close(sym, p, t, bar.Open, "mål (gap)") or True
        if bar.High >= p["target"]:
            return close(sym, p, t, p["target"], "mål") or True
        if allow_time_exit and p["bars"] >= module.MAX_HOLD_BARS:
            return close(sym, p, t, bar.Close, "tidsgräns") or True
        if is_day and bar.LAST_OF_DAY:
            return close(sym, p, t, bar.Close, "dagens slut") or True
        trail = getattr(module, "TRAIL_ATR", None)
        if trail and bar.ATR14 == bar.ATR14:  # flytta upp stop (gäller från nästa stapel)
            p["stop"] = max(p["stop"], float(bar.Close - trail * bar.ATR14))
        return False

    for t in timeline:
        # 1) Hantera öppna positioner.
        for sym in list(open_pos):
            i = pos_in[sym].get(t)
            if i is None:
                continue
            p = open_pos[sym]
            p["bars"] += 1
            check_exit(sym, p, rows[sym][i], t)

        # 2) Nya köp på signaler från föregående stapel.
        for sym in sorted(data):
            i = pos_in[sym].get(t)
            if i is None or i == 0 or sym in open_pos or len(open_pos) >= max_positions:
                continue
            x = data[sym]
            prev, bar = rows[sym][i - 1], rows[sym][i]
            if not prev.SIGNAL:
                continue
            if is_day and (prev.LAST_OF_DAY or bar.LAST_OF_DAY):
                continue  # ingen signal över natten, inget köp på dagens sista stapel
            entry = float(bar.Open) * (1 + slip)
            stop, target = stop_and_target(module, float(prev.Close), float(prev.ATR14), entry)
            if entry <= stop:
                continue
            equity = cash + sum(q["qty"] * last_close.get(s, q["entry"]) for s, q in open_pos.items())
            qty = position_size(equity, entry, stop, settings.risk_per_trade_pct,
                                settings.max_position_pct_of_strategy_capital,
                                settings.allow_fractional_shares)
            c = fee(qty, entry)
            if qty <= 0 or qty * entry + c > cash:
                continue
            cash -= qty * entry + c
            p = {"entry": entry, "qty": qty, "stop": stop, "init_stop": stop,
                 "target": target,
                 "time": t, "fees": c, "bars": 0}
            open_pos[sym] = p
            check_exit(sym, p, bar, t, allow_time_exit=False)  # stop/mål kan nås redan första stapeln

        # 3) Värdera portföljen.
        for sym, x in data.items():
            i = pos_in[sym].get(t)
            if i is not None:
                last_close[sym] = float(rows[sym][i].Close)
        equity_points.append((t, cash + sum(q["qty"] * last_close[s] for s, q in open_pos.items())))

    # Stäng det som är kvar vid testets slut.
    end_t = timeline[-1]
    for sym in list(open_pos):
        close(sym, open_pos[sym], end_t, last_close[sym], "testets slut")
    window = (timeline[0], end_t)
    equity = pd.Series(dict(equity_points), name="equity")
    equity.iloc[-1] = cash
    trades_df = pd.DataFrame(trades)
    return {"trades": trades_df, "equity": equity, "stats": _stats(trades_df, equity, capital_usd, data, window)}


def _empty_stats(capital):
    return {"start_capital_usd": capital, "end_capital_usd": capital, "total_return_pct": 0.0,
            "trades": 0, "win_rate_pct": 0.0, "avg_r": 0.0, "profit_factor": None,
            "max_drawdown_pct": 0.0, "fees_usd": 0.0, "buy_and_hold_pct": None}


def _stats(trades, equity, capital, data, window=None):
    s = _empty_stats(capital)
    if len(equity):
        s["end_capital_usd"] = round(float(equity.iloc[-1]), 2)
        s["total_return_pct"] = round((equity.iloc[-1] / capital - 1) * 100, 2)
        dd = equity / equity.cummax() - 1
        s["max_drawdown_pct"] = round(float(dd.min()) * 100, 2)
    if len(trades):
        wins = trades[trades.pnl_usd > 0].pnl_usd.sum()
        losses = -trades[trades.pnl_usd < 0].pnl_usd.sum()
        s["trades"] = int(len(trades))
        s["win_rate_pct"] = round((trades.pnl_usd > 0).mean() * 100, 1)
        s["avg_r"] = round(float(trades.r_multiple.mean()), 2)
        s["profit_factor"] = round(wins / losses, 2) if losses > 0 else None
        s["fees_usd"] = round(float(trades.fees_usd.sum()), 2)
    # Jämförelse: köp och behåll alla aktier med lika vikt hela perioden.
    closes = [x.Close if window is None else x.Close[(x.index >= window[0]) & (x.index <= window[1])]
              for x in data.values()]
    rets = [c.iloc[-1] / c.iloc[0] - 1 for c in closes if len(c) > 1]
    if rets:
        s["buy_and_hold_pct"] = round(sum(rets) / len(rets) * 100, 2)
    return {k: (float(v) if hasattr(v, "item") else v) for k, v in s.items()}


def period_return(close, start, end=None):
    """Avkastning i % för en kursserie mellan två tidpunkter (None om data saknas)."""
    if close is None or not len(close):
        return None
    c = close[(close.index >= start) & ((close.index <= end) if end is not None else True)].dropna()
    if len(c) < 2:
        return None
    return round((float(c.iloc[-1]) / float(c.iloc[0]) - 1) * 100, 2)


def per_year(total_pct, start, end):
    """Omräkning av total avkastning till genomsnitt per år."""
    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    if total_pct is None or years <= 0.25:
        return None
    return round(((1 + total_pct / 100) ** (1 / years) - 1) * 100, 1)


def compare_strategies(bars_by_symbol, strategies, capital_usd, settings=None, split=0.7, benchmark=None):
    """Jämför flera strategier på samma aktier.

    Perioden delas i två delar: "test" (första split-andelen) och "kontroll"
    (resten). En strategi som bara fungerar i testdelen är troligen tur
    eller överanpassad.

    strategies: {"namn": strategi, ...}. Returnerar en tabell, en rad per strategi.
    benchmark: stängningskurser för jämförelseindex (t.ex. SPY = S&P 500).
    """
    settings = settings or Settings()
    times = sorted(set().union(*[b.index for b in bars_by_symbol.values() if b is not None and len(b)]))
    if not times:
        return pd.DataFrame()
    # Börja handla först när det finns data nog för SMA200.
    first = times[min(220, len(times) - 1)]
    usable = [t for t in times if t >= first]
    cut = usable[int(len(usable) * split)] if len(usable) > 10 else None
    bench = period_return(benchmark, first)
    rows = []
    for name, strat in strategies.items():
        full = run_backtest(bars_by_symbol, strat, capital_usd, settings, start=first)["stats"]
        row = {"Strategi": name, "Avkastning %": full["total_return_pct"],
               "Per år %": per_year(full["total_return_pct"], first, times[-1]),
               "S&P 500 %": bench,
               "Slår S&P 500?": None if bench is None else ("ja" if full["total_return_pct"] > bench else "nej"),
               "Köp & behåll %": full["buy_and_hold_pct"], "Affärer": full["trades"],
               "Vinstandel %": full["win_rate_pct"], "Snitt R": full["avg_r"],
               "Profit factor": full["profit_factor"], "Största nedgång %": full["max_drawdown_pct"]}
        if cut is not None:
            a = run_backtest(bars_by_symbol, strat, capital_usd, settings, start=first, end=cut)["stats"]
            b = run_backtest(bars_by_symbol, strat, capital_usd, settings, start=cut)["stats"]
            row["Testdel %"] = a["total_return_pct"]
            row["Kontrolldel %"] = b["total_return_pct"]
            row["Håller i kontroll?"] = "ja" if (a["total_return_pct"] > 0 and b["total_return_pct"] > 0) else "nej"
        row["Testperiod"] = f"{first:%Y-%m-%d} – {times[-1]:%Y-%m-%d}"
        rows.append(row)
    return pd.DataFrame(rows).sort_values("Avkastning %", ascending=False).reset_index(drop=True)


def buy_and_hold_by_symbol(bars_by_symbol, start_bar=220):
    """Köp och behåll per aktie över samma period som jämförelsen (kontroll)."""
    times = sorted(set().union(*[b.index for b in bars_by_symbol.values() if b is not None and len(b)]))
    if not times:
        return pd.DataFrame()
    first = times[min(start_bar, len(times) - 1)]
    rows = []
    for sym, b in bars_by_symbol.items():
        if b is None or not len(b):
            rows.append({"Aktie": sym, "Från": None, "Till": None, "Köp & behåll %": None})
            continue
        c = b.Close[b.index >= first]
        if len(c) < 2:
            rows.append({"Aktie": sym, "Från": None, "Till": None, "Köp & behåll %": None})
            continue
        rows.append({"Aktie": sym, "Från": f"{c.index[0]:%Y-%m-%d}", "Till": f"{c.index[-1]:%Y-%m-%d}",
                     "Startkurs": round(float(c.iloc[0]), 2), "Slutkurs": round(float(c.iloc[-1]), 2),
                     "Köp & behåll %": round((float(c.iloc[-1]) / float(c.iloc[0]) - 1) * 100, 1)})
    return pd.DataFrame(rows)


def find_best(bars_by_symbol, strategies, capital_usd, settings=None, benchmark=None,
              risk_levels=(0.5, 1.0, 2.0), split=0.7, min_trades=20, progress=None):
    """Testar alla strategier på alla risknivåer och rangordnar dem.

    En kombination räknas bara som godkänd om den
    - går plus både i testdelen (första 70 %) och kontrolldelen (sista 30 %),
    - har minst min_trades affärer (färre är för osäkert).
    Poäng = avkastning per år / största nedgång. Det belönar jämn tillväxt
    framför hög avkastning med stora ras.
    """
    from dataclasses import replace

    settings = settings or Settings()
    times = sorted(set().union(*[b.index for b in bars_by_symbol.values() if b is not None and len(b)]))
    if not times:
        return pd.DataFrame()
    first = times[min(220, len(times) - 1)]
    usable = [t for t in times if t >= first]
    cut = usable[int(len(usable) * split)] if len(usable) > 10 else None
    bench = period_return(benchmark, first)
    total = len(strategies) * len(risk_levels)
    rows, done = [], 0
    for name, strat in strategies.items():
        for risk in risk_levels:
            s = replace(settings, risk_per_trade_pct=risk)
            full = run_backtest(bars_by_symbol, strat, capital_usd, s, start=first)["stats"]
            a = run_backtest(bars_by_symbol, strat, capital_usd, s, start=first, end=cut)["stats"] if cut else full
            b = run_backtest(bars_by_symbol, strat, capital_usd, s, start=cut)["stats"] if cut else full
            yearly = per_year(full["total_return_pct"], first, times[-1])
            dd = abs(full["max_drawdown_pct"]) or 0.0
            ok = a["total_return_pct"] > 0 and b["total_return_pct"] > 0 and full["trades"] >= min_trades
            rows.append({
                "Strategi": name, "Risk per affär %": risk, "Per år %": yearly,
                "Avkastning %": full["total_return_pct"], "S&P 500 %": bench,
                "Slår S&P 500?": None if bench is None else ("ja" if full["total_return_pct"] > bench else "nej"),
                "Största nedgång %": full["max_drawdown_pct"], "Affärer": full["trades"],
                "Vinstandel %": full["win_rate_pct"], "Testdel %": a["total_return_pct"],
                "Kontrolldel %": b["total_return_pct"], "Godkänd": "ja" if ok else "nej",
                "Poäng": round((yearly or 0) / max(dd, 5.0), 2),
            })
            done += 1
            if progress:
                progress(done, total)
    df = pd.DataFrame(rows)
    df["_ok"] = df["Godkänd"] == "ja"
    df = df.sort_values(["_ok", "Poäng"], ascending=[False, False]).drop(columns="_ok").reset_index(drop=True)
    df.attrs["period"] = f"{first:%Y-%m-%d} – {times[-1]:%Y-%m-%d}"
    return df
