"""Varianter av daytrading som kan jämföras (av nattrapporten och i backtestet).

ORB = Opening Range Breakout. Ett välkänt och väl undersökt daytrading-upplägg:
1. "Öppningsintervallet" är högsta och lägsta kurs under dagens första minuter
   (t.ex. 15 minuter = de tre första 5-minutersstaplarna).
2. Köp när kursen stänger ÖVER öppningsintervallets högsta, i en aktie med
   ovanligt hög volym i dag och över dagens VWAP (volymviktat snittpris).
3. Användarens grundregel gäller: köp aldrig när RSI eller MACD är på väg ner.
4. Stop loss under öppningsintervallets lägsta (högst 1,5 × ATR bort), mål 2R,
   allt stängs senast vid dagens slut.
"""
import sys

import pandas as pd

from . import day, evaluate_last


class DayORB:
    NAME = "day"
    MIN_BARS = 60
    STOP_ATR = 1.5
    TARGET_R = 2.0
    MAX_HOLD_BARS = 78  # hela dagen; stängs ändå vid dagens slut

    def __init__(self, label, opening_bars=3, last_entry_bar=24, volume_factor=1.5):
        self.label = label
        self.opening_bars = opening_bars      # 3 × 5 min = 15 minuter
        self.last_entry_bar = last_entry_bar  # inga nya köp efter 2 timmar
        self.volume_factor = volume_factor

    def conditions(self, x):
        date = pd.Series(x.index.normalize(), index=x.index)
        bar_no = x.groupby(date).cumcount()
        first = bar_no < self.opening_bars
        or_high = x.High.where(first).groupby(date).transform("max")
        typical = (x.High + x.Low + x.Close) / 3
        vwap = (typical * x.Volume).groupby(date).cumsum() / x.Volume.groupby(date).cumsum()
        return pd.DataFrame(
            {
                "efter öppningsintervallet": (bar_no >= self.opening_bars) & (bar_no <= self.last_entry_bar),
                "bryter öppningens högsta": x.Close > or_high,
                "över VWAP": x.Close > vwap,
                "hög volym": x.Volume >= self.volume_factor * x.VOL20,
                "RSI stiger": (x.RSI14 > x.RSI14.shift(1)) & (x.RSI14 > 50) & (x.RSI14 < 80),
                "MACD stiger": (x.MACD > x.MACD.shift(1)) & (x.MACD_HIST > x.MACD_HIST.shift(1)),
            },
            index=x.index,
        )

    def evaluate(self, symbol, bars):
        return evaluate_last(self, symbol, bars)

    def __repr__(self):
        return f"DayORB({self.label!r})"


def day_variants():
    """Daytrading-varianterna som jämförs."""
    return {
        "Day – momentum (nuvarande)": sys.modules[day.__name__],
        "Day – ORB 15 min": DayORB("Day – ORB 15 min", opening_bars=3),
        "Day – ORB 30 min": DayORB("Day – ORB 30 min", opening_bars=6),
        "Day – ORB 30 min, hela dagen": DayORB("Day – ORB 30 min, hela dagen", opening_bars=6,
                                                last_entry_bar=72),  # köp fram till 15:30 New York
    }
