"""Swingtrading: köp rekyler i en stigande långsiktig trend (dagliga staplar).

Idé: aktien ligger i en upptrend (över SMA200, SMA50 över SMA200), har
nyligen fallit tillbaka (RSI var låg de senaste dagarna) och har nu
BEKRÄFTAT en vändning upp:
- RSI har stigit två dagar i rad (RSI-botten bekräftad),
- MACD-linjen och MACD-histogrammet stiger (inte på väg ner),
- kursen gör inga nya bottnar och stänger över gårdagens högsta kurs.
Köp aldrig en aktie som fortfarande är på väg ner.

Tidigare version krävde att RSI skulle ha varit ≤40 exakt i går samtidigt
som kursen låg över EMA50 – en kombination som nästan aldrig inträffar.
"""
import sys

import pandas as pd

from . import evaluate_last

NAME = "swing"
MIN_BARS = 220          # behövs för SMA200
STOP_ATR = 2.0          # stop loss = pris − 2 × ATR
TARGET_R = 2.0          # kursmål = 2 × risken (2R)
MAX_HOLD_BARS = 20      # sälj senast efter 20 handelsdagar
PULLBACK_RSI = 45       # RSI måste ha varit under detta nyligen
PULLBACK_LOOKBACK = 5   # ... någon gång de senaste 5 dagarna
BOTTOM_LOOKBACK = 10    # bottnen = lägsta kursen de senaste 10 dagarna


def conditions(x):
    rsi1, rsi2 = x.RSI14.shift(1), x.RSI14.shift(2)
    return pd.DataFrame(
        {
            "lång trend": (x.Close > x.SMA200) & (x.SMA50 > x.SMA200),
            "rekyl nyligen": x.RSI14.rolling(PULLBACK_LOOKBACK).min() <= PULLBACK_RSI,
            # RSI-botten bekräftad: RSI har stigit två dagar i rad (inte på väg ner).
            "RSI stiger": (x.RSI14 > rsi1) & (rsi1 > rsi2) & (x.RSI14 > 40) & (x.RSI14 < 65),
            # MACD får inte vara på väg ner: både linjen och histogrammet stiger.
            "MACD stiger": (x.MACD > x.MACD.shift(1)) & (x.MACD_HIST > x.MACD_HIST.shift(1)),
            # Kursen har verkligen vänt: inga nya bottnar de senaste 2 dagarna
            # och stängning över gårdagens högsta kurs.
            "bekräftad botten": (x.Low.rolling(2).min() > x.Low.rolling(BOTTOM_LOOKBACK).min())
                                & (x.Close > x.High.shift(1)),
            "volym": x.Volume >= 0.8 * x.VOL20,
        },
        index=x.index,
    )


def evaluate(symbol, bars):
    return evaluate_last(sys.modules[__name__], symbol, bars)
