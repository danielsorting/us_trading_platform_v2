from datetime import datetime
from zoneinfo import ZoneInfo

from trading_platform.backtest import run_backtest
from trading_platform.brokers.paper import PaperBroker
from trading_platform.config import Settings
from trading_platform.strategies import entry_signals
from trading_platform.strategies.variants import (SwingShort, daily_variants, data_for, hourly_variants,
                                                  swing_variants)

NY = ZoneInfo("America/New_York")


def test_short_swing_uses_hourly_data_and_user_rules(hourly):
    v = swing_variants()["Swing kort (timgraf) – låt vinnare löpa"]
    assert isinstance(v, SwingShort) and data_for(v) == ("6mo", "60m") and data_for(v, True) == ("730d", "60m")
    assert set(hourly_variants()) & set(daily_variants()) == set()
    total = 0
    for seed in range(8):
        x, sig = entry_signals(v, hourly(160, seed=seed))
        # köper aldrig när RSI eller MACD är på väg ner
        assert (x.RSI14[sig] > x.RSI14.shift(1)[sig]).all() and (x.MACD[sig] > x.MACD.shift(1)[sig]).all()
        assert (x.Close[sig] > x.High.shift(1)[sig]).all()  # bekräftad botten
        total += int(sig.sum())
    assert total > 0
    r = run_backtest({f"H{i}": hourly(160, seed=i) for i in range(5)}, v, 5000, Settings())
    assert r["stats"]["trades"] > 0
    row = v.evaluate("H1", hourly(160, seed=1))
    assert row["trail_atr"] == 6.0 and row["stop_reference"] < row["price"]


def test_tick_with_short_swing_fetches_hourly(tmp_path, provider):
    from trading_platform.autotrader import tick
    calls = []

    class P(provider):
        def history_many(self, symbols, period, interval, progress=None):
            calls.append(interval)
            return super().history_many(symbols, period, interval, progress)

    b = PaperBroker(5000, tmp_path / "a.json", slippage_pct=0)
    s = Settings(watchlist=["AAA", "BBB"], swing_universe="watchlist")
    v = swing_variants()["Swing kort (timgraf) – låt vinnare löpa"]
    state = {}
    tick(P(), b, s, state, ["AAA", "BBB"], swing_strategy=v, now_ny=datetime(2026, 11, 3, 11, 0, tzinfo=NY))
    assert "60m" in calls and len(state["swing_top"]) == 2
    assert all(r.get("conditions_met") for r in state["swing_top"])


def test_five_of_six_keeps_rsi_macd_bottom_and_buys_more(bars):
    from trading_platform.strategies.variants import SwingFiveOfSix
    strict = swing_variants()["Swing – låt vinnare löpa"]
    soft = swing_variants()["Swing – 5 av 6 (RSI, MACD och botten krävs)"]
    assert isinstance(soft, SwingFiveOfSix)
    n_strict = n_soft = 0
    for seed in range(10):
        b = bars(600, seed=seed)
        x, s1 = entry_signals(strict, b)
        _, s2 = entry_signals(soft, b)
        assert not (s1 & ~s2).any()  # allt som strikta köper, köper även den mjuka
        assert (x.RSI14[s2] > x.RSI14.shift(1)[s2]).all() and (x.MACD[s2] > x.MACD.shift(1)[s2]).all()
        n_strict += int(s1.sum())
        n_soft += int(s2.sum())
    assert n_soft > n_strict
