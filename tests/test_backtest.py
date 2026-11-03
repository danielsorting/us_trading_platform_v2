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
    r = run_backtest({f"D{i}": intraday(15, seed=i) for i in range(4)}, day, 1000, Settings())
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
