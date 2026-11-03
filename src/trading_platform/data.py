from abc import ABC, abstractmethod
import yfinance as yf

class MarketDataProvider(ABC):
    @abstractmethod
    def history(self, symbol: str, period: str, interval: str): ...

class YahooResearchProvider(MarketDataProvider):
    """Research/demo data adapter. Not a real-time execution feed."""
    def history(self, symbol, period="1y", interval="1d"):
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty: return None
        df.columns = [str(c).title() for c in df.columns]
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)
        return df
