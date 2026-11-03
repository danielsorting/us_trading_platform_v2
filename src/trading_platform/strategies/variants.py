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


class SwingFiveOfSix(SwingVariant):
    """Mjukare swing: RSI stiger, MACD stiger och bekräftad botten MÅSTE vara uppfyllda
    (köp aldrig när RSI eller MACD är på väg ner). Av de övriga tre (lång trend,
    rekyl nyligen, volym) räcker två – alltså minst 5 av 6."""

    REQUIRED = ("RSI stiger", "MACD stiger", "bekräftad botten")

    def conditions(self, x):
        c = swing.conditions(x)
        soft = [k for k in c.columns if k not in self.REQUIRED]
        out = c[list(self.REQUIRED)].copy()
        out["minst 2 av: trend, rekyl, volym"] = c[soft].fillna(False).astype(int).sum(axis=1) >= len(soft) - 1
        return out


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


class SwingShort(SwingVariant):
    """Kort swing på TIMGRAF: samma regler som din swing, men på timstaplar
    över de senaste dagarna till en månad (som 5D/1M-graferna i Webull).

    - trend: kursen över sitt snitt för senaste månaden (140 timstaplar ≈ 20 dagar)
    - rekyl: RSI har varit lågt någon gång senaste veckan (35 timstaplar ≈ 5 dagar)
    - RSI och MACD har vänt upp och bottnen är bekräftad – på timgrafen
    - volym: dagens volym hittills är normal eller högre jämfört med samma tid tidigare dagar
    Stop loss under bottnen (4 × tim-ATR), sedan följande stop. Säljs senast efter ca en månad.
    """
    DATA = ("6mo", "60m")      # Yahoo: timdata finns ca 2 år bakåt
    BACKTEST_DATA = ("730d", "60m")
    MIN_BARS = 160
    TREND_BARS = 140
    PULLBACK_BARS = 35

    def __init__(self, label, pullback_rsi=40, **kw):
        kw.setdefault("stop_atr", 4.0)
        kw.setdefault("trail_atr", 6.0)
        kw.setdefault("max_hold", 140)
        super().__init__(label, **kw)
        self.pullback_rsi = pullback_rsi

    def conditions(self, x):
        rsi1, rsi2 = x.RSI14.shift(1), x.RSI14.shift(2)
        date = pd.Series(x.index.normalize(), index=x.index)
        bar_no = x.groupby(date).cumcount()
        cum = x.Volume.groupby(date).cumsum()
        usual = cum.groupby(bar_no).transform(lambda v: v.shift(1).rolling(10, min_periods=1).mean())
        c = pd.DataFrame(
            {
                "trend senaste månaden": x.Close > sma(x.Close, self.TREND_BARS),
                "rekyl senaste veckan": x.RSI14.rolling(self.PULLBACK_BARS).min() <= self.pullback_rsi,
                "RSI stiger": (x.RSI14 > rsi1) & (rsi1 > rsi2) & (x.RSI14 > 40) & (x.RSI14 < 65),
                "MACD stiger": (x.MACD > x.MACD.shift(1)) & (x.MACD_HIST > x.MACD_HIST.shift(1)),
                "bekräftad botten": (x.Low.rolling(2).min() > x.Low.rolling(self.PULLBACK_BARS).min())
                                    & (x.Close > x.High.shift(1)),
                "volym": (cum >= 0.8 * usual).where(usual.notna(), x.Volume >= 0.8 * x.VOL20).astype(bool),
            },
            index=x.index,
        )
        if self.market_filter:
            c["marknaden i upptrend"] = (x.MKT_CLOSE > sma(x.MKT_CLOSE, self.TREND_BARS)) if "MKT_CLOSE" in x else False
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
        "Swing – 5 av 6 (RSI, MACD och botten krävs)": SwingFiveOfSix(
            "Swing – 5 av 6 (RSI, MACD och botten krävs)", trail_atr=3.0, max_hold=120),
        "Swing kort (timgraf) – låt vinnare löpa": SwingShort("Swing kort (timgraf) – låt vinnare löpa"),
        "Swing kort (timgraf), mål 2R": SwingShort("Swing kort (timgraf), mål 2R", trail_atr=None,
                                                   target_r=2.0, max_hold=70),
    }


def data_for(strategy, backtest=False):
    """(period, intervall) som strategin behöver. Standard: dagsdata."""
    if backtest:
        return getattr(strategy, "BACKTEST_DATA", None) or getattr(strategy, "DATA", None) or ("5y", "1d")
    return getattr(strategy, "DATA", None) or ("2y", "1d")


def daily_variants():
    return {k: v for k, v in swing_variants().items() if data_for(v) == ("2y", "1d")}


def hourly_variants():
    return {k: v for k, v in swing_variants().items() if data_for(v) != ("2y", "1d")}


def needs_market(strategies):
    return any(getattr(s, "market_filter", False) or getattr(s, "relative_strength", False)
               for s in strategies)
