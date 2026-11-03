from datetime import datetime
from zoneinfo import ZoneInfo

from trading_platform.brokers.paper import PaperBroker
from trading_platform.config import Settings
from trading_platform.daytop import market_status, most_liquid, rank_day_candidates, yahoo_symbol
from trading_platform.paper_trader import close_all_day_positions, run_paper_cycle

NY = ZoneInfo("America/New_York")


def test_market_status():
    assert market_status(datetime(2026, 11, 3, 10, 0, tzinfo=NY))[0] is True
    assert market_status(datetime(2026, 11, 3, 8, 0, tzinfo=NY))[0] is False
    assert market_status(datetime(2026, 11, 7, 12, 0, tzinfo=NY))[0] is False  # lördag


def test_most_liquid_picks_highest_dollar_volume(bars):
    a, b, c = bars(30, seed=1), bars(30, seed=2), bars(30, seed=3)
    b["Volume"] = b["Volume"] * 10
    c["Close"] = c["Close"] / 50  # billig aktie (< 5 USD) ska bort
    assert most_liquid({"A": a, "B": b, "C": c}, n=2) == ["B", "A"]


def test_yahoo_symbol():
    assert yahoo_symbol("BRK.B") == "BRK-B"


def test_rank_puts_candidates_first(intraday):
    ranked = rank_day_candidates({f"S{i}": intraday(5, seed=i) for i in range(15)})
    flags = ranked.is_candidate.tolist()
    assert flags == sorted(flags, reverse=True)
    assert "score" in ranked


def test_day_top_buys_limit_and_closes_before_market_close(tmp_path):
    b = PaperBroker(10_000, tmp_path / "a.json", slippage_pct=0)
    rows = [{"symbol": f"S{i}", "strategy": "day", "signal": "CANDIDATE", "price": 100.0,
             "stop_reference": 99.0, "target_reference": 102.0} for i in range(12)]
    midday = datetime(2026, 11, 3, 11, 0, tzinfo=NY)
    run_paper_cycle(b, rows, Settings(max_position_pct_of_strategy_capital=10, max_fee_share_of_profit_pct=1000), {}, limits={"day": 10}, ny_now=midday)
    assert sum(1 for p in b.positions.values() if p["strategy"] == "day") == 10
    late = datetime(2026, 11, 3, 15, 55, tzinfo=NY)
    acts = run_paper_cycle(b, rows, Settings(), {f"S{i}": 100.5 for i in range(12)}, ny_now=late)
    assert b.positions == {} and all("dagens slut" in a["detail"] for a in acts if a["action"] == "SÄLJ")


def test_close_all_day_positions(tmp_path):
    b = PaperBroker(1000, tmp_path / "a.json", slippage_pct=0)
    b.submit_order("D", "buy", 1, price=100, strategy="day")
    b.submit_order("S", "buy", 1, price=100, strategy="swing")
    close_all_day_positions(b, {"D": 101})
    assert list(b.positions) == ["S"]


def test_extended_check_blocks_big_premarket_moves():
    import pandas as pd
    from trading_platform.daytop import add_extended_check
    ranked = pd.DataFrame({"symbol": ["AAPL", "MSFT", "X"], "price": [338.32, 100.0, 50.0]})
    ext = {"AAPL": (324.51, None), "MSFT": (101.0, None)}
    out = add_extended_check(ranked, ext, 2.0)
    assert out.set_index("symbol").loc["AAPL", "ext_move_pct"] == -4.08
    assert out.move_ok.tolist() == [False, True, True]  # X saknar pris → inte blockerad


def test_orb_buys_only_after_opening_range_and_above_its_high(intraday):
    import pandas as pd
    from trading_platform.backtest import run_backtest
    from trading_platform.strategies import entry_signals
    from trading_platform.strategies.day_variants import DayORB, day_variants
    v = DayORB("orb", opening_bars=3)
    total = 0
    for seed in range(6):
        bars = intraday(20, seed=seed, vol=0.004)
        x, sig = entry_signals(v, bars)
        date = pd.Series(x.index.normalize(), index=x.index)
        bar_no = x.groupby(date).cumcount()
        or_high = x.High.where(bar_no < 3).groupby(date).transform("max")
        assert (bar_no[sig] >= 3).all() and (x.Close[sig] > or_high[sig]).all()
        assert (x.RSI14[sig] > x.RSI14.shift(1)[sig]).all()
        total += int(sig.sum())
    assert total > 0
    r = run_backtest({f"D{i}": intraday(20, seed=i, vol=0.004) for i in range(4)}, v, 1000, Settings())
    t = r["trades"]
    if len(t):
        assert (t.entry_time.dt.date == t.exit_time.dt.date).all()
    assert len(day_variants()) == 4


def test_configured_day_variant_is_used(intraday):
    from trading_platform.autotrader import day_strategy_for
    from trading_platform.config import load_settings
    from trading_platform.strategies.day_variants import DayORB
    s = load_settings()
    strat = day_strategy_for(s)
    assert isinstance(strat, DayORB) and strat.label == s.day_variant
    ranked = rank_day_candidates({"A": intraday(5, seed=1)}, strat)
    assert "efter öppningsintervallet" in ranked.conditions.iloc[0]
