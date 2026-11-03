import pandas as pd

from trading_platform.backtest import compare_strategies, run_backtest
from trading_platform.config import Settings
from trading_platform.scanner import scan
from trading_platform.strategies import entry_signals
from trading_platform.strategies.variants import SwingVariant, add_market, swing_variants


def test_market_filter_blocks_buys_in_falling_market(bars):
    stock = bars(800, seed=1)
    falling = bars(800, seed=2, drift=-0.003, vol=0.005)  # börsen faller hela tiden
    with_mkt = add_market(stock, falling)
    base = entry_signals(SwingVariant("grund"), with_mkt)[1].sum()
    filtered = entry_signals(SwingVariant("filter", market_filter=True), with_mkt)[1].sum()
    assert base > 0 and filtered == 0


def test_market_filter_without_market_data_never_buys(bars):
    assert entry_signals(SwingVariant("f", market_filter=True), bars(800))[1].sum() == 0


def test_fixed_percent_stop_and_target(bars):
    v = SwingVariant("2/2", stop_pct=2.0, target_pct=2.0)
    r = run_backtest({"A": bars(800, seed=4)}, v, 1000, Settings(slippage_pct=0))
    t = r["trades"]
    assert len(t)
    planned = t[t.exit_reason.isin(["stop", "mål"])]
    assert ((planned.target / planned.entry_price - 1).round(4) == 0.02).all()


def test_compare_has_test_and_control_period(bars):
    mkt = bars(900, seed=50, drift=0.0004, vol=0.01)
    data = {f"S{i}": add_market(bars(900, seed=i), mkt) for i in range(4)}
    df = compare_strategies(data, swing_variants(), 1000, Settings())
    assert len(df) == len(swing_variants())
    assert {"Testdel %", "Kontrolldel %", "Håller i kontroll?"} <= set(df.columns)


def test_trading_window_is_respected(bars):
    data = {"A": bars(900, seed=7)}
    start = data["A"].index[500]
    r = run_backtest(data, SwingVariant("g"), 1000, Settings(), start=start)
    assert (pd.to_datetime(r["trades"].entry_time) >= start).all()


def test_scanner_can_use_a_variant(provider, bars):
    mkt = bars(600, seed=60)
    rows = scan(["AAPL"], include_day=False, provider=provider(), settings=Settings(),
                swing_strategy=SwingVariant("f", market_filter=True), market_bars=mkt)
    assert rows[0]["strategy"] == "swing" and "marknaden i upptrend" in rows[0]["conditions"]
