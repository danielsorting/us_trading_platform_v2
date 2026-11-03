"""Varianter av swing-strategin som kan jämföras i backtestet.

Alla varianter använder swing-strategins grundvillkor och kan dessutom ha:
- marknadsfilter: köp bara när S&P 500 (SPY) ligger över sitt SMA200,
- relativ styrka: köp bara aktier som gått bättre än SPY senaste 3 månaderna,
- egna regler för stop loss och kursmål (ATR-baserat eller fast procent),
- "låt vinnare löpa": inget fast mål, stop loss följer med uppåt (trailing stop).

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
                 target_pct=None, market_filter=False, relative_strength=False, max_hold=swing.MAX_HOLD_BARS,
                 trail_atr=None):
        self.label = label
        self.STOP_ATR = stop_atr
        self.TARGET_R = target_r
        self.STOP_PCT = stop_pct
        self.TARGET_PCT = target_pct
        self.MAX_HOLD_BARS = max_hold
        self.TRAIL_ATR = trail_atr
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


class BreakoutVariant(SwingVariant):
    """Trendföljning: köp när aktien bryter upp till ny högsta nivå.

    Passar aktier i starka trender som sällan gör stora rekyler. Följer
    samma grundregel som swing: köp aldrig när RSI eller MACD är på väg ner.
    """

    def __init__(self, label, lookback=50, volume_factor=1.5, **kw):
        kw.setdefault("trail_atr", 3.0)
        kw.setdefault("max_hold", 120)
        super().__init__(label, **kw)
        self.lookback = lookback
        self.volume_factor = volume_factor

    def conditions(self, x):
        c = pd.DataFrame(
            {
                "upptrend": (x.Close > x.SMA50) & (x.SMA50 > x.SMA200),
                f"ny {self.lookback}-dagars högsta": x.Close > x.High.shift(1).rolling(self.lookback).max(),
                "stark volym": x.Volume >= self.volume_factor * x.VOL20,
                "RSI stiger": (x.RSI14 > x.RSI14.shift(1)) & (x.RSI14 > 50) & (x.RSI14 < 80),
                "MACD stiger": (x.MACD > x.MACD.shift(1)) & (x.MACD_HIST > x.MACD_HIST.shift(1)),
            },
            index=x.index,
        )
        if self.market_filter:
            c["marknaden i upptrend"] = (x.MKT_CLOSE > sma(x.MKT_CLOSE, 200)) if "MKT_CLOSE" in x else False
        return c


def swing_variants():
    """Varianterna som jämförs i dashboarden."""
    return {
        "Swing – grund": SwingVariant("Swing – grund"),
        "Swing + marknadsfilter": SwingVariant("Swing + marknadsfilter", market_filter=True),
        "Swing + marknadsfilter + relativ styrka": SwingVariant(
            "Swing + marknadsfilter + relativ styrka", market_filter=True, relative_strength=True),
        "Swing + marknadsfilter, mål 3R": SwingVariant(
            "Swing + marknadsfilter, mål 3R", market_filter=True, target_r=3.0),
        "Swing – låt vinnare löpa": SwingVariant(
            "Swing – låt vinnare löpa", trail_atr=3.0, max_hold=120),
        "Swing + marknadsfilter – låt vinnare löpa": SwingVariant(
            "Swing + marknadsfilter – låt vinnare löpa", market_filter=True, trail_atr=3.0, max_hold=120),
        "Utbrott – låt vinnare löpa": BreakoutVariant("Utbrott – låt vinnare löpa"),
        "Utbrott + marknadsfilter – låt vinnare löpa": BreakoutVariant(
            "Utbrott + marknadsfilter – låt vinnare löpa", market_filter=True),
        "Ditt upplägg: +2 % / −2 %": SwingVariant(
            "Ditt upplägg: +2 % / −2 %", stop_pct=2.0, target_pct=2.0),
    }


def needs_market(strategies):
    return any(getattr(s, "market_filter", False) or getattr(s, "relative_strength", False)
               for s in strategies)
