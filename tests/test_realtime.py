import pandas as pd

from trading_platform.data import AlpacaRealtime, HybridProvider
from trading_platform.paper_trader import latest_prices


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_alpaca_latest_trades_maps_symbols(monkeypatch):
    import requests
    seen = {}

    def fake_get(url, headers, timeout, params):
        seen.update(params=params, headers=headers)
        return FakeResp({"trades": {"AAPL": {"p": 270.5, "t": "2026-11-03T15:00:00Z"},
                                    "BRK.B": {"p": 480.0, "t": "2026-11-03T15:00:00Z"}}})

    monkeypatch.setattr(requests, "get", fake_get)
    rt = AlpacaRealtime("key", "secret")
    out = rt.latest_trades(["AAPL", "BRK-B", "NOPE"])
    assert out["AAPL"][0] == 270.5 and out["BRK-B"][0] == 480.0 and "NOPE" not in out
    assert seen["params"]["feed"] == "iex" and "BRK.B" in seen["params"]["symbols"]
    assert seen["headers"]["APCA-API-KEY-ID"] == "key"


def test_hybrid_uses_realtime_and_falls_back(provider):
    class RT:
        def latest_trades(self, symbols):
            return {"AAPL": (111.0, pd.Timestamp("2026-11-03"))}

    hp = HybridProvider(provider(), RT())
    prices = latest_prices(hp, ["AAPL", "MSFT"])
    assert prices["AAPL"] == 111.0  # realtid
    assert "MSFT" in prices  # fallback till staplar


def test_hybrid_survives_realtime_error(provider):
    class Broken:
        def latest_trades(self, symbols):
            raise RuntimeError("nätverk")

    hp = HybridProvider(provider(), Broken())
    assert "AAPL" in latest_prices(hp, ["AAPL"])


def test_retry_on_rate_limit(monkeypatch):
    import time
    from trading_platform import data
    monkeypatch.setattr(time, "sleep", lambda s: None)
    calls = []

    class YFRateLimitError(Exception):
        pass

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise YFRateLimitError("Too Many Requests. Rate limited.")
        return "ok"

    assert data._with_retry(flaky) == "ok" and len(calls) == 3


def test_most_active_parses_yahoo_screen(monkeypatch):
    import sys
    import types
    from trading_platform.data import YahooResearchProvider
    fake = types.ModuleType("yfinance")
    quotes = [{"symbol": f"S{i}", "regularMarketPrice": 10 + i, "regularMarketVolume": 1_000_000,
               "quoteType": "EQUITY"} for i in range(30)]
    quotes.append({"symbol": "PENNY", "regularMarketPrice": 1.0, "regularMarketVolume": 9e9, "quoteType": "EQUITY"})
    quotes.append({"symbol": "SOMEETF", "regularMarketPrice": 50, "regularMarketVolume": 9e9, "quoteType": "ETF"})
    # Yahoo skickar bara 25 per sida – roboten ska bläddra vidare
    fake.screen = lambda q, offset=None, count=None: {"quotes": quotes[offset:offset + 25]}
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    out = YahooResearchProvider().most_active(28)
    assert out[0] == "S29" and len(out) == 28 and "PENNY" not in out and "SOMEETF" not in out


def test_liquid_universe_falls_back(provider):
    from trading_platform.daytop import liquid_universe

    class P(provider):
        def most_active(self, n):
            raise RuntimeError("spärrad")

        def history_many(self, *a, **k):
            raise RuntimeError("spärrad")

    assert liquid_universe(P(), 5, fallback_symbols=["AAPL", "MSFT"]) == ["AAPL", "MSFT"]
