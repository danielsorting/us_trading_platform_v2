"""Tekniska indikatorer: SMA, EMA, RSI, MACD och ATR.

Alla funktioner tar en pandas-serie/tabell och returnerar nya kolumner.
Ingen funktion tittar framåt i tiden – varje värde räknas bara på data
fram till och med den aktuella stapeln.
"""
import numpy as np
import pandas as pd


def sma(s, period):
    """Enkelt glidande medelvärde (Simple Moving Average)."""
    return s.rolling(period, min_periods=period).mean()


def ema(s, span):
    """Exponentiellt glidande medelvärde (Exponential Moving Average)."""
    return s.ewm(span=span, adjust=False).mean()


def rsi(close, period=14):
    """Relative Strength Index enligt Wilder (0–100)."""
    d = close.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    al = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # Bara uppgångar (ingen förlust) ger RSI 100; för lite data ger 50.
    out = out.where(~((al == 0) & (ag > 0)), 100.0)
    return out.fillna(50)


def macd(close, fast=12, slow=26, signal=9):
    """Returnerar (MACD-linje, signallinje, histogram)."""
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def atr(df, period=14):
    """Average True Range – hur mycket aktien rör sig per stapel."""
    pc = df.Close.shift(1)
    tr = pd.concat(
        [df.High - df.Low, (df.High - pc).abs(), (df.Low - pc).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def enrich(df):
    """Lägger till alla indikatorer som kolumner i en kopia av kurstabellen."""
    x = df.copy()
    x["SMA20"] = sma(x.Close, 20)
    x["SMA50"] = sma(x.Close, 50)
    x["SMA200"] = sma(x.Close, 200)
    x["EMA9"] = ema(x.Close, 9)
    x["EMA20"] = ema(x.Close, 20)
    x["EMA50"] = ema(x.Close, 50)
    x["EMA200"] = ema(x.Close, 200)
    x["RSI14"] = rsi(x.Close)
    m, s, h = macd(x.Close)
    x["MACD"] = m
    x["MACD_SIGNAL"] = s
    x["MACD_HIST"] = h
    x["ATR14"] = atr(x)
    x["VOL20"] = x.Volume.rolling(20).mean()
    x["DOLLAR_VOL20"] = (x.Close * x.Volume).rolling(20).mean()
    return x
