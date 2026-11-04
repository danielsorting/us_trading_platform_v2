"""Låtsasdata för automatiska tester av webbsidan (ingen internetanslutning behövs).

Används när miljövariabeln TP_FAKE_DATA=1 är satt. Kurserna är påhittade men
ser ut som riktiga (slumpmässig vandring), så alla delar av sidan kan provas.
"""
import numpy as np
import pandas as pd

from .data import MarketDataProvider


def _bars(n, idx, seed, vol):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0004, vol, n)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.004, "Low": np.minimum(o, c) * 0.996,
                         "Close": c, "Volume": rng.integers(2_000_000, 6_000_000, n).astype(float)}, index=idx)


class FakeProvider(MarketDataProvider):
    """Ger påhittade kurser för alla aktier, alla perioder och intervall."""

    def history(self, symbol, period, interval):
        seed = sum(map(ord, symbol))
        end = pd.Timestamp("2026-11-03")
        if interval == "1d":
            years = {"1y": 1, "2y": 2, "5y": 5, "10y": 10}.get(period, 2)
            idx = pd.bdate_range(end=end, periods=252 * years)
            return _bars(len(idx), idx, seed, 0.02)
        days = {"5d": 5, "1mo": 21, "60d": 60, "6mo": 126, "730d": 500}.get(period, 5)
        step = {"5m": 5, "15m": 15, "30m": 30, "60m": 60}.get(interval, 5)
        per_day = 390 // step
        idx = pd.DatetimeIndex([d + pd.Timedelta(hours=9, minutes=30 + step * k)
                                for d in pd.bdate_range(end=end, periods=days) for k in range(per_day)])
        return _bars(len(idx), idx, seed, 0.003 if step == 5 else 0.006)

    def most_active(self, n=300, min_price=5.0):
        return [f"T{i:03d}" for i in range(min(n, 60))]

    def usd_sek(self):
        return 10.0

    def latest_extended(self, symbols):
        out = {}
        for s in symbols:
            df = self.history(s, "5d", "5m")
            out[s] = (float(df.Close.iloc[-1]), df.index[-1])
        return out
