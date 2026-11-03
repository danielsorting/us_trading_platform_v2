"""Daytrading: momentum på 5-minutersstaplar under ordinarie börstid.

Idé: kursen ligger över sina korta medelvärden, momentum (MACD) ökar,
RSI visar styrka utan att vara överköpt och volymen är högre än normalt.
Alla positioner stängs senast vid dagens sista stapel.
"""
import sys

import pandas as pd

from . import evaluate_last

NAME = "day"
MIN_BARS = 60
STOP_ATR = 1.5          # stop loss = pris − 1,5 × ATR
TARGET_R = 2.0          # kursmål = 2R
MAX_HOLD_BARS = 24      # max 2 timmar (24 × 5 min)


def conditions(x):
    return pd.DataFrame(
        {
            "intradagstrend": (x.Close > x.EMA20) & (x.EMA9 > x.EMA20),
            "momentum": x.MACD_HIST > x.MACD_HIST.shift(1),
            "RSI-styrka": (x.RSI14 > 50) & (x.RSI14 < 75),
            "relativ volym": x.Volume > x.VOL20,
        },
        index=x.index,
    )


def evaluate(symbol, bars):
    return evaluate_last(sys.modules[__name__], symbol, bars)
