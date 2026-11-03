"""Kursdata. Yahoo Finance (yfinance) används för forskning och test.

yfinance importeras först när data verkligen hämtas, så att resten av
programmet (och testerna) fungerar även där paketet saknas.
"""
from abc import ABC, abstractmethod

import pandas as pd


class MarketDataProvider(ABC):
    @abstractmethod
    def history(self, symbol: str, period: str, interval: str): ...

    def usd_sek(self):
        """Aktuell växelkurs USD→SEK, eller None om den inte kan hämtas."""
        return None

    def history_many(self, symbols, period, interval, progress=None):
        """Kurser för många aktier. Standard: en i taget (parallellt)."""
        from concurrent.futures import ThreadPoolExecutor

        def one(sym):
            try:
                return sym, self.history(sym, period, interval)
            except Exception:
                return sym, None

        out = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            for i, (sym, df) in enumerate(pool.map(one, symbols), 1):
                out[sym] = df
                if progress:
                    progress(i, len(symbols))
        return out


class YahooResearchProvider(MarketDataProvider):
    """Research/demo data adapter. Not a real-time execution feed.

    Begränsningar hos Yahoo: dagsdata finns många år bakåt, men
    5-minutersdata bara ungefär 60 dagar bakåt och kan vara fördröjd.
    """

    def history(self, symbol, period="1y", interval="1d"):
        import yfinance as yf

        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            return None
        df.columns = [str(c).title() for c in df.columns]
        if getattr(df.index, "tz", None) is not None:
            # Visa tider i New York-tid (börsens tid) utan tidszonsmärkning.
            df.index = df.index.tz_convert("America/New_York").tz_localize(None)
        return df[["Open", "High", "Low", "Close", "Volume"]]

    def history_many(self, symbols, period, interval, progress=None, chunk=100):
        """Hämtar många aktier i omgångar med yfinance.download (mycket snabbare
        än en i taget). Aktier som saknar data får värdet None."""
        import yfinance as yf

        symbols = list(dict.fromkeys(symbols))
        out = {}
        for start in range(0, len(symbols), chunk):
            part = symbols[start:start + chunk]
            try:
                df = yf.download(part, period=period, interval=interval, group_by="ticker",
                                 auto_adjust=True, threads=True, progress=False)
            except Exception:
                df = None
            for sym in part:
                sub = None
                if df is not None and len(df):
                    try:
                        if isinstance(df.columns, pd.MultiIndex):
                            if sym in df.columns.get_level_values(0):
                                sub = df[sym]
                            elif sym in df.columns.get_level_values(-1):
                                sub = df.xs(sym, axis=1, level=-1)
                        elif len(part) == 1:
                            sub = df
                    except Exception:
                        sub = None
                out[sym] = self._clean(sub)
            if progress:
                progress(min(start + chunk, len(symbols)), len(symbols))
        return out

    @staticmethod
    def _clean(df):
        if df is None or not len(df):
            return None
        df = df.copy()
        df.columns = [str(c).title() for c in df.columns]
        need = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in df.columns for c in need):
            return None
        df = df[need].dropna(subset=["Close"])
        if not len(df):
            return None
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_convert("America/New_York").tz_localize(None)
        return df

    def usd_sek(self):
        try:
            df = self.history("USDSEK=X", "5d", "1d")
            if df is not None and len(df):
                rate = float(df.Close.iloc[-1])
                if 3 < rate < 30:  # rimlighetskontroll
                    return rate
        except Exception:
            pass
        return None


class PrefetchedProvider(MarketDataProvider):
    """Använder redan nedladdade kurser när de finns, annars den vanliga källan.

    Gör det möjligt att ladda ner hundratals aktier i ett svep (history_many)
    och sedan låta scannern läsa dem utan nya anrop.
    """

    def __init__(self, base, prefetched=None):
        self.base = base
        self.cache = dict(prefetched or {})

    def add(self, data_by_symbol, period, interval):
        for sym, df in data_by_symbol.items():
            self.cache[(sym, period, interval)] = df

    def history(self, symbol, period, interval):
        key = (symbol, period, interval)
        if key in self.cache:
            return self.cache[key]
        return self.base.history(symbol, period, interval)

    def history_many(self, symbols, period, interval, progress=None):
        return self.base.history_many(symbols, period, interval, progress=progress)

    def usd_sek(self):
        return self.base.usd_sek()
