import numpy as np
import pandas as pd

from trading_platform.pit import make_allowed, pit_members, today_members


def bars(vol_early, vol_late, n=400):
    idx = pd.bdate_range("2021-01-01", periods=n)
    v = np.r_[np.full(n // 2, vol_early), np.full(n - n // 2, vol_late)]
    return pd.DataFrame({"Open": 10.0, "High": 10.0, "Low": 10.0, "Close": 10.0, "Volume": v}, index=idx)


def test_list_changes_over_time_without_future_info():
    data = {"EARLY": bars(1000, 1), "LATE": bars(1, 1000), "CHEAP": bars(5000, 5000)}
    data["CHEAP"]["Close"] = 1.0  # under 5 USD räknas inte
    m = pit_members(data, n=1)
    allowed = make_allowed(m)
    first_q, first_set = m[0]
    assert first_set == {"EARLY"}
    assert m[-1][1] == {"LATE"}
    assert allowed("EARLY", first_q) and not allowed("LATE", first_q)
    assert not allowed("EARLY", pd.Timestamp("2000-01-01"))
    assert today_members(data, n=1) == {"LATE"}  # facit i efterhand väljer dagens vinnare


def test_backtest_respects_allowed(swing_bars=None):
    from trading_platform.backtest import run_backtest
    from trading_platform.config import Settings
    from trading_platform.strategies import swing
    idx = pd.bdate_range("2021-01-01", periods=300)
    c = pd.Series(np.linspace(20, 60, 300) + np.sin(np.arange(300) / 3), index=idx)
    b = pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1e6})
    none = run_backtest({"X": b}, swing, 10_000, Settings(), allowed=lambda s, t: False)
    assert none["stats"]["trades"] == 0
