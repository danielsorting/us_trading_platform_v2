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

    def latest_extended(self, symbols):
        """Senaste pris INKLUSIVE förhandel/efterhandel: {aktie: (pris, tid)}.
        Standard: senaste stängningskurs från vanlig data."""
        out = {}
        for sym, df in self.history_many(list(symbols), "5d", "5m").items():
            if df is not None and len(df):
                out[sym] = (float(df.Close.iloc[-1]), df.index[-1])
        return out

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


def _with_retry(fn, tries=4, wait=20):
    """Kör fn(); om Yahoo svarar 'för många anrop' väntar vi och försöker igen."""
    import time
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # yfinance.exceptions.YFRateLimitError m.fl.
            text = f"{type(e).__name__} {e}"
            if ("RateLimit" in text or "Too Many Requests" in text) and i < tries - 1:
                time.sleep(wait * (i + 1))
                continue
            raise


class YahooResearchProvider(MarketDataProvider):
    """Research/demo data adapter. Not a real-time execution feed.

    Begränsningar hos Yahoo: dagsdata finns många år bakåt, men
    5-minutersdata bara ungefär 60 dagar bakåt och kan vara fördröjd.
    """

    def history(self, symbol, period="1y", interval="1d"):
        import yfinance as yf

        df = _with_retry(lambda: yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True))
        if df is None or df.empty:
            return None
        df.columns = [str(c).title() for c in df.columns]
        if getattr(df.index, "tz", None) is not None:
            # Visa tider i New York-tid (börsens tid) utan tidszonsmärkning.
            df.index = df.index.tz_convert("America/New_York").tz_localize(None)
        return df[["Open", "High", "Low", "Close", "Volume"]]

    def latest_extended(self, symbols):
        """Senaste pris inklusive förhandel och efterhandel (prepost)."""
        out = {}
        for sym, df in self.history_many(list(symbols), "2d", "5m", prepost=True).items():
            if df is not None and len(df):
                out[sym] = (float(df.Close.iloc[-1]), df.index[-1])
        return out

    def history_many(self, symbols, period, interval, progress=None, chunk=100, prepost=False):
        """Hämtar många aktier i omgångar med yfinance.download (mycket snabbare
        än en i taget). Aktier som saknar data får värdet None."""
        import yfinance as yf

        symbols = list(dict.fromkeys(symbols))
        out = {}
        for start in range(0, len(symbols), chunk):
            part = symbols[start:start + chunk]
            try:
                df = _with_retry(lambda: yf.download(part, period=period, interval=interval, group_by="ticker",
                                                     auto_adjust=True, threads=True, progress=False,
                                                     prepost=prepost))
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

    def most_active(self, n=300, min_price=5.0):
        """Dagens mest handlade USA-aktier från Yahoos färdiga lista (få anrop).
        Sorterat efter omsättning i dollar. Tom lista om det inte går."""
        import yfinance as yf

        rows = {}
        screen = getattr(yf, "screen", None)
        if screen is None:
            return []
        offset, pages = 0, 0
        while len(rows) < n and pages < 40:
            try:
                # offset gör att yfinance använder sökvägen som stöder sidor. Yahoo kan skicka färre
                # än vi ber om (ofta 25), så vi bläddrar vidare med det antal vi faktiskt fick.
                res = _with_retry(lambda: screen("most_actives", offset=offset, count=250))
            except Exception:
                break
            quotes = (res or {}).get("quotes") or []
            if not quotes:
                break
            for q in quotes:
                sym, price, vol = q.get("symbol"), q.get("regularMarketPrice"), q.get("regularMarketVolume")
                if sym and price and vol and price >= min_price and q.get("quoteType", "EQUITY") == "EQUITY":
                    rows[sym] = float(price) * float(vol)
            offset += len(quotes)
            pages += 1
        return [s for s, _ in sorted(rows.items(), key=lambda kv: kv[1], reverse=True)][:n]

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

    def latest_extended(self, symbols):
        return self.base.latest_extended(symbols)

    @property
    def has_realtime(self):
        return getattr(self.base, "has_realtime", False)

    def usd_sek(self):
        return self.base.usd_sek()


class AlpacaRealtime:
    """Realtidspriser från Alpaca (gratis konto, börsen IEX).

    Nycklarna läses från Streamlit Secrets eller miljövariabler – aldrig från koden.
    Dokumentation: https://docs.alpaca.markets/docs/about-market-data-api
    """

    URL = "https://data.alpaca.markets/v2/stocks/trades/latest"

    def __init__(self, key_id, secret_key, feed="iex", timeout=10):
        self.headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self.feed = feed
        self.timeout = timeout

    @staticmethod
    def to_alpaca(sym):
        return sym.replace("-", ".")  # Yahoo BRK-B → Alpaca BRK.B

    def latest_trades(self, symbols, chunk=100):
        """{aktie: (pris, tid)} för senaste avslut. Saknade aktier utelämnas."""
        import requests

        symbols = list(dict.fromkeys(symbols))
        back = {self.to_alpaca(s): s for s in symbols}
        out = {}
        for i in range(0, len(symbols), chunk):
            part = [self.to_alpaca(s) for s in symbols[i:i + chunk]]
            r = requests.get(self.URL, headers=self.headers, timeout=self.timeout,
                             params={"symbols": ",".join(part), "feed": self.feed})
            r.raise_for_status()
            for sym, tr in (r.json().get("trades") or {}).items():
                price = tr.get("p")
                if price:
                    out[back.get(sym, sym)] = (float(price), pd.Timestamp(tr.get("t")))
        return out

    def check(self):
        """Testar nycklarna. Returnerar (ok, text)."""
        try:
            got = self.latest_trades(["SPY"])
            return (True, "Alpaca fungerar") if "SPY" in got else (False, "Alpaca svarade utan pris")
        except Exception as e:
            return False, f"Alpaca fungerar inte: {e}"


class HybridProvider(MarketDataProvider):
    """Historik och staplar från en källa (Yahoo) + senaste pris i realtid (Alpaca)."""

    has_realtime = True

    def __init__(self, base, realtime):
        self.base = base
        self.realtime = realtime

    def history(self, symbol, period, interval):
        return self.base.history(symbol, period, interval)

    def history_many(self, symbols, period, interval, progress=None):
        return self.base.history_many(symbols, period, interval, progress=progress)

    def usd_sek(self):
        return self.base.usd_sek()

    def latest_extended(self, symbols):
        out = {}
        try:
            out = self.realtime.latest_trades(symbols)
        except Exception:
            out = {}
        missing = [s for s in symbols if s not in out]
        if missing:
            try:
                out.update(self.base.latest_extended(missing))
            except Exception:
                pass
        return out
