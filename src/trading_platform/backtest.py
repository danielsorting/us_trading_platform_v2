"""Backtesting: hur hade en strategi gått på historiska kurser?

Regler (för att resultatet ska vara ärligt):
- Ingen framtidsinformation: signalen räknas på en stapels stängningskurs
  och köpet sker först vid NÄSTA stapels öppningskurs.
- Stop loss = signalkurs − STOP_ATR × ATR. Kursmål = TARGET_R × risken.
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


def run_backtest(bars_by_symbol, module, capital_usd, settings=None, max_positions=None):
    """Kör ett backtest. bars_by_symbol: {"AAPL": kurstabell, ...}.

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
    pos_in = {s: {t: i for i, t in enumerate(x.index)} for s, x in data.items()}
    cash = float(capital_usd)
    open_pos, trades, equity_points, last_close = {}, [], [], {}

    def close(sym, p, t, price, reason):
        nonlocal cash
        exit_px = price * (1 - slip)
        c = fee(p["qty"], exit_px)
        cash += p["qty"] * exit_px - c
        pnl = p["qty"] * (exit_px - p["entry"]) - p["fees"] - c
        risk = p["qty"] * (p["entry"] - p["stop"])
        trades.append({
            "symbol": sym, "entry_time": p["time"], "exit_time": t,
            "entry_price": round(p["entry"], 4), "exit_price": round(exit_px, 4),
            "shares": p["qty"], "stop": round(p["stop"], 4), "target": round(p["target"], 4),
            "pnl_usd": round(pnl, 2), "r_multiple": round(pnl / risk, 2) if risk > 0 else 0.0,
            "fees_usd": round(p["fees"] + c, 2), "bars_held": p["bars"], "exit_reason": reason,
        })
        del open_pos[sym]

    def check_exit(sym, p, bar, t, allow_time_exit=True):
        if bar.Open <= p["stop"]:
            return close(sym, p, t, bar.Open, "stop (gap)") or True
        if bar.Low <= p["stop"]:
            return close(sym, p, t, p["stop"], "stop") or True
        if bar.Open >= p["target"]:
            return close(sym, p, t, bar.Open, "mål (gap)") or True
        if bar.High >= p["target"]:
            return close(sym, p, t, p["target"], "mål") or True
        if allow_time_exit and p["bars"] >= module.MAX_HOLD_BARS:
            return close(sym, p, t, bar.Close, "tidsgräns") or True
        if is_day and bar.LAST_OF_DAY:
            return close(sym, p, t, bar.Close, "dagens slut") or True
        return False

    for t in timeline:
        # 1) Hantera öppna positioner.
        for sym in list(open_pos):
            i = pos_in[sym].get(t)
            if i is None:
                continue
            p = open_pos[sym]
            p["bars"] += 1
            check_exit(sym, p, data[sym].iloc[i], t)

        # 2) Nya köp på signaler från föregående stapel.
        for sym in sorted(data):
            i = pos_in[sym].get(t)
            if i is None or i == 0 or sym in open_pos or len(open_pos) >= max_positions:
                continue
            x = data[sym]
            prev, bar = x.iloc[i - 1], x.iloc[i]
            if not prev.SIGNAL:
                continue
            if is_day and (prev.LAST_OF_DAY or bar.LAST_OF_DAY):
                continue  # ingen signal över natten, inget köp på dagens sista stapel
            stop = float(prev.Close - module.STOP_ATR * prev.ATR14)
            entry = float(bar.Open) * (1 + slip)
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
            p = {"entry": entry, "qty": qty, "stop": stop,
                 "target": entry + module.TARGET_R * (entry - stop),
                 "time": t, "fees": c, "bars": 0}
            open_pos[sym] = p
            check_exit(sym, p, bar, t, allow_time_exit=False)  # stop/mål kan nås redan första stapeln

        # 3) Värdera portföljen.
        for sym, x in data.items():
            i = pos_in[sym].get(t)
            if i is not None:
                last_close[sym] = float(x.Close.iloc[i])
        equity_points.append((t, cash + sum(q["qty"] * last_close[s] for s, q in open_pos.items())))

    # Stäng det som är kvar vid testets slut.
    end_t = timeline[-1]
    for sym in list(open_pos):
        close(sym, open_pos[sym], end_t, last_close[sym], "testets slut")
    equity = pd.Series(dict(equity_points), name="equity")
    equity.iloc[-1] = cash
    trades_df = pd.DataFrame(trades)
    return {"trades": trades_df, "equity": equity, "stats": _stats(trades_df, equity, capital_usd, data)}


def _empty_stats(capital):
    return {"start_capital_usd": capital, "end_capital_usd": capital, "total_return_pct": 0.0,
            "trades": 0, "win_rate_pct": 0.0, "avg_r": 0.0, "profit_factor": None,
            "max_drawdown_pct": 0.0, "fees_usd": 0.0, "buy_and_hold_pct": None}


def _stats(trades, equity, capital, data):
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
    rets = [x.Close.iloc[-1] / x.Close.iloc[0] - 1 for x in data.values() if len(x)]
    if rets:
        s["buy_and_hold_pct"] = round(sum(rets) / len(rets) * 100, 2)
    return {k: (float(v) if hasattr(v, "item") else v) for k, v in s.items()}
