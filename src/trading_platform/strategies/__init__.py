"""Strategier.

Varje strategi-modul har samma uppbyggnad:
- conditions(x): en tabell med ett sant/falskt-värde per villkor och stapel.
  Samma funktion används av både scannern (sista stapeln) och backtestet
  (alla staplar), så att de alltid följer exakt samma regler.
- evaluate(symbol, bars): bedömer den senaste stapeln och returnerar en rad
  till scannerns resultattabell.
- STOP_ATR, TARGET_R, MAX_HOLD_BARS: regler för stop loss, kursmål och
  hur länge en position får ligga.
"""
from ..indicators import enrich


def entry_signals(module, bars):
    """Returnerar (indikatortabell, sant/falskt-serie för köpsignal)."""
    x = enrich(bars.dropna(subset=["Open", "High", "Low", "Close", "Volume"]))
    cond = module.conditions(x)
    return x, cond.all(axis=1) & cond.notna().all(axis=1)


def evaluate_last(module, symbol, bars):
    """Gemensam scannerbedömning av senaste stapeln för en strategi."""
    name = module.NAME
    if bars is None or len(bars) < module.MIN_BARS:
        return {"symbol": symbol, "strategy": name, "signal": "INSUFFICIENT_DATA"}
    x, _ = entry_signals(module, bars)
    cond = module.conditions(x).iloc[-1]
    a = x.iloc[-1]
    ok = int(cond.sum())
    stop_pct = getattr(module, "STOP_PCT", None)
    target_pct = getattr(module, "TARGET_PCT", None)
    stop = float(a.Close * (1 - stop_pct / 100)) if stop_pct else float(a.Close - module.STOP_ATR * a.ATR14)
    if getattr(module, "TRAIL_ATR", None):
        target = None  # inget fast mål – följande stop
    elif target_pct:
        target = float(a.Close * (1 + target_pct / 100))
    else:
        target = float(a.Close + module.TARGET_R * (a.Close - stop))
    return {
        "symbol": symbol,
        "strategy": name,
        "signal": "CANDIDATE" if ok == len(cond) else "WATCH",
        "price": round(float(a.Close), 2),
        "rsi": round(float(a.RSI14), 1),
        "atr": round(float(a.ATR14), 3),
        "stop_reference": round(stop, 2),
        "target_reference": None if target is None else round(target, 2),
        "trail_atr": getattr(module, "TRAIL_ATR", None),
        "conditions_met": f"{ok}/{len(cond)}",
        "conditions": ", ".join(f"{k}: {'ja' if v else 'nej'}" for k, v in cond.items()),
    }
