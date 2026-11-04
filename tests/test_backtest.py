import pandas as pd

from trading_platform.backtest import run_backtest
from trading_platform.config import Settings
from trading_platform.strategies import day, swing


def test_profit_and_loss_add_up(bars):
    r = run_backtest({f"S{i}": bars(800, seed=i) for i in range(6)}, swing, 1000, Settings())
    assert r["stats"]["trades"] > 0
    assert abs(r["trades"].pnl_usd.sum() - (r["stats"]["end_capital_usd"] - 1000)) < 0.5


def test_entry_is_after_signal_bar(bars):
    """Köp ska ske på stapeln EFTER signalen – aldrig samma stapel."""
    data = {"A": bars(800, seed=3)}
    r = run_backtest(data, swing, 1000, Settings())
    from trading_platform.strategies import entry_signals
    _, sig = entry_signals(swing, data["A"])
    idx = list(data["A"].index)
    for t in r["trades"].entry_time:
        assert sig.iloc[idx.index(t) - 1]


def test_never_more_positions_than_allowed(bars):
    s = Settings(max_positions_per_strategy=2)
    r = run_backtest({f"S{i}": bars(800, seed=i) for i in range(8)}, swing, 1000, s)
    tr = r["trades"]
    for t in pd.to_datetime(tr.entry_time):
        open_now = ((tr.entry_time <= t) & (tr.exit_time > t)).sum()
        assert open_now <= 2


def test_day_trades_never_held_overnight(intraday):
    r = run_backtest({f"D{i}": intraday(15, seed=i) for i in range(4)}, day, 1000,
                     Settings(max_fee_share_of_profit_pct=1000))
    tr = r["trades"]
    assert len(tr) > 0
    assert (tr.entry_time.dt.date == tr.exit_time.dt.date).all()


def test_stop_loss_and_gap_handling():
    """Konstruerad kurs: signal, köp, sedan gap ned under stop."""
    from trading_platform import backtest as bt

    class Always:
        NAME, MIN_BARS, STOP_ATR, TARGET_R, MAX_HOLD_BARS = "swing", 30, 1.0, 2.0, 50

        @staticmethod
        def conditions(x):
            ok = pd.Series(False, index=x.index)
            ok.iloc[29] = True  # en enda signal
            return pd.DataFrame({"signal": ok})

    idx = pd.date_range("2024-01-01", periods=40, freq="B")
    close = [100.0] * 30 + [100.0, 80.0] + [80.0] * 8
    df = pd.DataFrame({"Open": close, "High": [c + 1 for c in close], "Low": [c - 1 for c in close],
                       "Close": close, "Volume": [1e6] * 40}, index=idx)
    df.loc[idx[30], ["Open", "High", "Low", "Close"]] = [100, 101, 99.5, 100]
    s = Settings(slippage_pct=0, commission_per_share_usd=0, min_commission_usd=0, allow_fractional_shares=True)
    r = bt.run_backtest({"X": df}, Always, 1000, s)
    t = r["trades"].iloc[0]
    assert t.exit_reason == "stop (gap)"
    assert t.exit_price == 80.0  # säljs till öppningskursen, inte till stop-nivån
    assert t.r_multiple < -1  # gap ger större förlust än planerat


def test_parking_follows_index_when_robot_is_idle(bars):
    """Utan signaler ska parkerad kassa följa SPY (minus små avgifter)."""
    from dataclasses import replace
    from trading_platform.strategies.variants import add_market

    class Never:
        NAME, MIN_BARS, STOP_ATR, TARGET_R, MAX_HOLD_BARS = "swing", 30, 2.0, 2.0, 20

        @staticmethod
        def conditions(x):
            return pd.DataFrame({"aldrig": pd.Series(False, index=x.index)})

    mkt = bars(600, seed=9, drift=0.001, vol=0.01)
    data = {"A": add_market(bars(600, seed=1), mkt)}
    off = run_backtest(data, Never, 1000, Settings())["stats"]
    on = run_backtest(data, Never, 1000, replace(Settings(), park_idle_in_index=True))["stats"]
    spy = (mkt.Close.iloc[-1] / mkt.Close.iloc[0] - 1) * 100
    assert off["total_return_pct"] == 0
    assert abs(on["total_return_pct"] - spy) < 1.5


def test_split_returns_from_one_equity_curve():
    import pandas as pd
    from trading_platform.backtest import split_returns
    eq = pd.Series([100, 110, 120, 108, 132], index=pd.date_range("2024-01-01", periods=5))
    a, b = split_returns(eq, pd.Timestamp("2024-01-03"), 100)
    assert a == 20.0 and b == 10.0   # 100 → 120 (+20 %), sedan 120 → 132 (+10 %)
