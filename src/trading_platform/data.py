"""Kursdata. Yahoo Finance (yfinance) används för forskning och test.

yfinance importeras först när data verkligen hämtas, så att resten av
programmet (och testerna) fungerar även där paketet saknas.
"""
from abc import ABC, abstractmethod


class MarketDataProvider(ABC):
    @abstractmethod
    def history(self, symbol: str, period: str, interval: str): ...

    def usd_sek(self):
        """Aktuell växelkurs USD→SEK, eller None om den inte kan hämtas."""
        return None


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
