"""Daytrading-varianten "ett köp per aktie och dag" (påhittad data)."""
from trading_platform.backtest import run_backtest
from trading_platform.config import Settings
from trading_platform.strategies import entry_signals
from trading_platform.strategies.day_variants import DayORB, day_variants

ONE = "Day – ORB hela dagen, lås vinst, ett köp per aktie och dag"
BASE = "Day – ORB hela dagen, lås vinst (inget mål)"


def _signals(v, bars):
    x, sig = entry_signals(v, bars)
    return x, sig


def test_variant_is_listed_and_like_current_robot_variant():
    v, base = day_variants()[ONE], day_variants()[BASE]
    assert isinstance(v, DayORB) and v.one_entry_per_day and not base.one_entry_per_day
    assert (v.opening_bars, v.last_entry_bar, v.TRAIL_ATR, v.KEEP_TARGET) == \
           (base.opening_bars, base.last_entry_bar, base.TRAIL_ATR, base.KEEP_TARGET)


def test_at_most_one_signal_per_stock_and_day(intraday):
    v = DayORB("ett", opening_bars=3, volume_factor=0.9, one_entry_per_day=True)
    many = DayORB("många", opening_bars=3, volume_factor=0.9)
    found_extra = False
    for seed in range(8):
        bars = intraday(20, seed=seed, vol=0.004)
        x, sig = _signals(v, bars)
        _, sig_many = _signals(many, bars)
        per_day = sig.groupby(x.index.normalize()).sum()
        assert (per_day <= 1).all()
        # Varje signal i nya varianten är också en signal i grundvarianten, och den är dagens första.
        assert (sig_many[sig]).all()
        first_many = sig_many & (sig_many.astype(int).groupby(x.index.normalize()).cumsum() == 1)
        assert sig.equals(first_many)
        found_extra |= bool(sig_many.groupby(x.index.normalize()).sum().gt(1).any())
    assert found_extra  # testdatan innehåller dagar med flera signaler, så testet prövar något


def test_rules_still_hold_rsi_and_macd_rising(intraday):
    """Användarens regel: aldrig köp när RSI eller MACD är på väg ner."""
    v = day_variants()[ONE]
    for seed in range(4):
        x, sig = _signals(v, intraday(20, seed=seed, vol=0.004))
        assert (x.RSI14[sig] > x.RSI14.shift(1)[sig]).all()
        assert (x.MACD[sig] > x.MACD.shift(1)[sig]).all()


def test_backtest_runs_with_fewer_or_equal_buys_per_day(intraday):
    bars = {f"D{i}": intraday(20, seed=i, vol=0.004) for i in range(4)}
    s = Settings(max_positions_per_strategy=10, park_idle_in_index=False)
    r = run_backtest(bars, day_variants()[ONE], 2000, s)
    t = r["trades"]
    if len(t):
        assert (t.entry_time.dt.date == t.exit_time.dt.date).all()  # inga positioner över natten
        per = t.groupby([t.symbol, t.entry_time.dt.date]).size()
        assert (per <= 1).all()


def test_scanner_row_shows_new_condition(intraday):
    row = day_variants()[ONE].evaluate("X", intraday(5, seed=3))
    assert "första köpsignalen i dag" in row["conditions"]
    assert row["conditions_met"].endswith("/7")
    assert row["target_reference"] is None and row["trail_atr"] == 2.0
