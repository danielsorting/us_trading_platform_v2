import numpy as np
import pandas as pd
import pytest


def make_bars(n=600, freq="B", seed=1, drift=0.0005, vol=0.02, start="2021-01-04"):
    """Påhittade men realistiska kursstaplar för tester."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    open_ = np.r_[100, close[:-1]]
    return pd.DataFrame(
        {"Open": open_, "High": np.maximum(open_, close) * 1.005, "Low": np.minimum(open_, close) * 0.995,
         "Close": close, "Volume": rng.integers(2_000_000, 6_000_000, n)},
        index=pd.date_range(start, periods=n, freq=freq),
    )


def make_intraday(days=10, seed=1, vol=0.003):
    """5-minutersstaplar 09:30–15:55 för ett antal handelsdagar."""
    idx = pd.DatetimeIndex([d + pd.Timedelta(hours=9, minutes=30 + 5 * k)
                            for d in pd.bdate_range("2026-08-03", periods=days) for k in range(78)])
    return make_bars(len(idx), "5min", seed, 0, vol).set_index(idx)


def make_hourly(days=120, seed=1, vol=0.006, drift=0.0004):
    """Timstaplar 09:30–15:30 (7 per dag), som Yahoos 60m-data."""
    idx = pd.DatetimeIndex([d + pd.Timedelta(hours=9, minutes=30) + pd.Timedelta(hours=k)
                            for d in pd.bdate_range("2026-04-01", periods=days) for k in range(7)])
    return make_bars(len(idx), "h", seed, drift, vol).set_index(idx)


from trading_platform.data import MarketDataProvider


class FakeProvider(MarketDataProvider):
    def __init__(self, bars_by_symbol=None, rate=10.0):
        self.bars = bars_by_symbol or {}
        self.rate = rate

    def history(self, symbol, period, interval):
        if symbol not in self.bars:
            if interval == "60m":
                return make_hourly(120, seed=sum(map(ord, symbol)))
            return make_bars(600, seed=sum(map(ord, symbol))) if interval == "1d" else make_intraday(5, seed=len(symbol))
        return self.bars[symbol]

    def usd_sek(self):
        return self.rate


@pytest.fixture
def bars():
    return make_bars


@pytest.fixture
def intraday():
    return make_intraday


@pytest.fixture
def provider():
    return FakeProvider


@pytest.fixture
def hourly():
    return make_hourly


@pytest.fixture(autouse=True)
def no_real_news(monkeypatch):
    """Testerna ska aldrig hämta riktiga nyheter från nätet."""
    from trading_platform import news
    monkeypatch.setattr(news, "fetch_symbol_news", lambda symbol, count=10: [])
    monkeypatch.setattr(news, "fetch_market_news", lambda *a, **k: [])
