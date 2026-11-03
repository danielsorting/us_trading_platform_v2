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
