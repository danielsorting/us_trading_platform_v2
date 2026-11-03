import numpy as np
import pandas as pd

from trading_platform.indicators import enrich, rsi, sma


def test_sma_matches_manual_average():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = sma(s, 3)
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == 2 and out.iloc[4] == 4


def test_rsi_stays_between_0_and_100(bars):
    r = rsi(bars(500).Close)
    assert r.between(0, 100).all()


def test_rsi_is_100_when_price_only_rises():
    r = rsi(pd.Series(np.arange(1, 50, dtype=float)))
    assert r.iloc[-1] == 100


def test_indicators_do_not_look_ahead(bars):
    """Ett värde får inte ändras när framtida staplar läggs till."""
    full = bars(400)
    a = enrich(full.iloc[:300]).iloc[-1]
    b = enrich(full).iloc[299]
    for col in ["SMA50", "SMA200", "EMA20", "RSI14", "MACD_HIST", "ATR14"]:
        assert abs(a[col] - b[col]) < 1e-9, col
