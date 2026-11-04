import numpy as np
import pandas as pd

from trading_platform.backtest import run_backtest
from trading_platform.config import Settings
from trading_platform.pit import pit_members
from trading_platform.rotation import run_rotation, spy_stats


def mk(trend, n=600, vol=1e6):
    idx = pd.bdate_range("2021-01-01", periods=n)
    c = 20 * np.exp(np.arange(n) * trend)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": vol}, index=idx)


def test_rotation_picks_strongest_and_uses_spy_when_weak():
    bars = {"UP": mk(0.002), "FLAT": mk(0.0), "DOWN": mk(-0.001)}
    spy = mk(0.0005)
    members = pit_members(bars, n=3)
    r = run_rotation(bars, spy, members, 10_000, start=bars["UP"].index[300], k=1)
    assert all(p == ["UP"] for _, p in r["picks"] if p)
    assert r["total_pct"] > spy_stats(spy, bars["UP"].index[300])["total_pct"]
    weak = mk(-0.001)  # S&P under 200-dagars snitt: inga aktier
    r2 = run_rotation(bars, weak, members, 10_000, start=bars["UP"].index[300], k=1)
    assert all(p == [] for _, p in r2["picks"])


def test_momentum_rank_option_runs():
    from trading_platform.strategies import swing
    rng = np.random.default_rng(0)
    bars = {}
    for i in range(6):
        idx = pd.bdate_range("2021-01-01", periods=400)
        c = pd.Series(30 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 400))), index=idx)
        bars[f"S{i}"] = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6})
    a = run_backtest(bars, swing, 10_000, Settings(), max_positions=1)
    b = run_backtest(bars, swing, 10_000, Settings(), max_positions=1, rank="momentum")
    assert "total_return_pct" in a["stats"] and "total_return_pct" in b["stats"]
