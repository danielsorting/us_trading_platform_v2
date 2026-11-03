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


def test_trailing_stop_lets_winners_run_and_only_moves_up(bars):
    from trading_platform.strategies.variants import SwingVariant
    v = SwingVariant("löpa", trail_atr=3.0, max_hold=120)
    up = {f"U{i}": bars(1200, seed=i, drift=0.0015) for i in range(5)}
    r = run_backtest(up, v, 1000, Settings())
    t = r["trades"]
    assert len(t) and t.target.isna().all()  # inget fast kursmål
    assert "följande stop" in set(t.exit_reason)
    assert t.bars_held.max() > 20  # vinnare får ligga längre än 20 dagar


def test_paper_cycle_moves_trailing_stop_up(tmp_path):
    from trading_platform.brokers.paper import PaperBroker
    from trading_platform.paper_trader import run_paper_cycle
    b = PaperBroker(1000, tmp_path / "a.json", slippage_pct=0)
    b.submit_order("A", "buy", 1, price=100, stop=94, target=None, strategy="swing", trail_atr=3.0, atr=2.0)
    run_paper_cycle(b, [], Settings(), {"A": 110})
    assert b.positions["A"]["stop"] == 104  # 110 − 3 × 2
    run_paper_cycle(b, [], Settings(), {"A": 107})
    assert b.positions["A"]["stop"] == 104  # flyttas aldrig ned
    run_paper_cycle(b, [], Settings(), {"A": 103})
    assert "A" not in b.positions  # såld på följande stop


def test_breakout_buys_new_highs_with_rising_rsi_and_macd(bars):
    from trading_platform.strategies.variants import BreakoutVariant
    v = BreakoutVariant("utbrott")
    total = 0
    for seed in range(5):
        x, sig = entry_signals(v, bars(1500, seed=seed, drift=0.001))
        b, prev = x[sig], x.shift(1)[sig]
        assert (b.Close > x.High.shift(1).rolling(50).max()[sig]).all()
        assert (b.RSI14 > prev.RSI14).all() and (b.MACD > prev.MACD).all()
        total += int(sig.sum())
    assert total > 10


def test_compare_includes_sp500_benchmark(bars):
    mkt = bars(900, seed=50, drift=0.0004, vol=0.01)
    data = {f"S{i}": add_market(bars(900, seed=i), mkt) for i in range(3)}
    df = compare_strategies(data, swing_variants(), 1000, Settings(), benchmark=mkt.Close)
    assert df["S&P 500 %"].notna().all()
    assert set(df["Slår S&P 500?"]) <= {"ja", "nej"}
    assert "Per år %" in df


def test_find_best_ranks_approved_first(bars):
    from trading_platform.backtest import find_best
    mkt = bars(900, seed=50, drift=0.0004, vol=0.01)
    data = {f"S{i}": add_market(bars(900, seed=i, drift=0.001), mkt) for i in range(6)}
    calls = []
    df = find_best(data, swing_variants(), 1000, Settings(), benchmark=mkt.Close,
                   progress=lambda i, n: calls.append((i, n)))
    assert len(df) == len(swing_variants()) * 3 and calls[-1][0] == calls[-1][1]
    ok = (df["Godkänd"] == "ja").tolist()
    assert ok == sorted(ok, reverse=True)  # godkända först
    approved = df[df["Godkänd"] == "ja"]
    assert (approved["Testdel %"] > 0).all() and (approved["Kontrolldel %"] > 0).all()
    assert (approved["Affärer"] >= 20).all()
