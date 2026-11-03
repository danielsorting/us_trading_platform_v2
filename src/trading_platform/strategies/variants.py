"""Varianter av swing-strategin som kan jämföras i backtestet.

Alla varianter använder swing-strategins grundvillkor och kan dessutom ha:
- marknadsfilter: köp bara när S&P 500 (SPY) ligger över sitt SMA200,
- relativ styrka: köp bara aktier som gått bättre än SPY senaste 3 månaderna,
- egna regler för stop loss och kursmål (ATR-baserat eller fast procent).

Marknadsdata läggs till som kolumnen MKT_CLOSE med add_market().
"""
import pandas as pd

from ..indicators import sma
from . import evaluate_last, swing

MARKET_SYMBOL = "SPY"


def add_market(bars, market_bars):
    """Lägger till marknadens stängningskurs (MKT_CLOSE) i en aktiens kurstabell."""
    if bars is None or market_bars is None or not len(market_bars):
        return bars
    mkt = market_bars.Close.rename("MKT_CLOSE")
    out = bars.drop(columns=["MKT_CLOSE"], errors="ignore").join(mkt, how="left")
    out["MKT_CLOSE"] = out["MKT_CLOSE"].ffill()
    return out


class SwingVariant:
    NAME = "swing"
    MIN_BARS = swing.MIN_BARS

    def __init__(self, label, stop_atr=swing.STOP_ATR, target_r=swing.TARGET_R, stop_pct=None,
                 target_pct=None, market_filter=False, relative_strength=False, max_hold=swing.MAX_HOLD_BARS):
        self.label = label
        self.STOP_ATR = stop_atr
        self.TARGET_R = target_r
        self.STOP_PCT = stop_pct
        self.TARGET_PCT = target_pct
        self.MAX_HOLD_BARS = max_hold
        self.market_filter = market_filter
        self.relative_strength = relative_strength

    def conditions(self, x):
        c = swing.conditions(x)
        has_mkt = "MKT_CLOSE" in x
        if self.market_filter:
            c["marknaden i upptrend"] = (x.MKT_CLOSE > sma(x.MKT_CLOSE, 200)) if has_mkt else False
        if self.relative_strength:
            c["starkare än marknaden"] = (
                (x.Close.pct_change(63) > x.MKT_CLOSE.pct_change(63)) if has_mkt else False
            )
        return c

    def evaluate(self, symbol, bars):
        return evaluate_last(self, symbol, bars)

    def __repr__(self):
        return f"SwingVariant({self.label!r})"


def swing_variants():
    """Varianterna som jämförs i dashboarden."""
    return {
        "Swing – grund": SwingVariant("Swing – grund"),
        "Swing + marknadsfilter": SwingVariant("Swing + marknadsfilter", market_filter=True),
        "Swing + marknadsfilter + relativ styrka": SwingVariant(
            "Swing + marknadsfilter + relativ styrka", market_filter=True, relative_strength=True),
        "Swing + marknadsfilter, mål 3R": SwingVariant(
            "Swing + marknadsfilter, mål 3R", market_filter=True, target_r=3.0),
        "Ditt upplägg: +2 % / −2 %": SwingVariant(
            "Ditt upplägg: +2 % / −2 %", stop_pct=2.0, target_pct=2.0),
    }


def needs_market(strategies):
    return any(getattr(s, "market_filter", False) or getattr(s, "relative_strength", False)
               for s in strategies)
