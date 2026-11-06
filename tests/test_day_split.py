"""Testdel/kontrolldel för daytrading i nattrapporten (påhittad data)."""
import importlib.util
import sys
from pathlib import Path

import pandas as pd

from trading_platform.backtest import day_split_returns, run_backtest
from trading_platform.config import Settings
from trading_platform.fakedata import FakeProvider
from trading_platform.strategies.day_variants import day_variants

ROOT = Path(__file__).resolve().parents[1]


def _equity(values_per_day):
    idx, vals = [], []
    for d, vs in zip(pd.bdate_range("2026-01-05", periods=len(values_per_day)), values_per_day):
        for k, v in enumerate(vs):
            idx.append(d + pd.Timedelta(hours=9, minutes=35 + 5 * k))
            vals.append(v)
    return pd.Series(vals, index=pd.DatetimeIndex(idx))


def test_split_is_at_end_of_day_70_percent():
    # 10 dagar: 7 dagar testdel (1000 -> 1100), 3 dagar kontrolldel (1100 -> 990)
    days = [[1000 + 100 * (i + 1) / 7] * 3 for i in range(7)] + [[1100 - 110 * (i + 1) / 3] * 3 for i in range(3)]
    a, b = day_split_returns(_equity(days), 1000)
    assert a == 10.0
    assert b == -10.0


def test_split_needs_enough_days():
    assert day_split_returns(_equity([[1000, 1010]] * 3), 1000) == (None, None)
    assert day_split_returns(None, 1000) == (None, None)
    assert day_split_returns(pd.Series(dtype=float), 1000) == (None, None)


def test_parts_add_up_to_whole_run_for_every_day_variant():
    p = FakeProvider()
    bars = {s: p.history(s, "60d", "5m") for s in ("AAA", "BBB", "CCC")}
    s = Settings(max_positions_per_strategy=10, max_position_pct_of_strategy_capital=10.0,
                 park_idle_in_index=False)
    for name, strat in day_variants().items():
        res = run_backtest(bars, strat, 2000, s)
        a, b = day_split_returns(res["equity"], 2000)
        assert a is not None and b is not None, name
        total = (1 + a / 100) * (1 + b / 100) - 1
        assert abs(total * 100 - res["stats"]["total_return_pct"]) < 0.1, name


def test_nightly_report_day_columns():
    spec = importlib.util.spec_from_file_location("nightly_report", ROOT / "scripts" / "nightly_report.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError:  # yfinance saknas i vissa miljöer
        import pytest
        pytest.skip("yfinance saknas")
    eq = _equity([[1000 + 10 * i] * 3 for i in range(10)])
    cols = mod.day_test_control(eq, 1000)
    assert set(cols) == {"Testdel %", "Kontrolldel %", "Håller i kontroll?"}
    assert cols["Håller i kontroll?"] == "ja"
    assert mod.day_test_control(None, 1000)["Håller i kontroll?"] is None
    sys.modules.pop("nightly_report", None)
