from datetime import datetime
from zoneinfo import ZoneInfo

from trading_platform.autotrader import tick
from trading_platform.brokers.paper import PaperBroker
from trading_platform.config import Settings

NY = ZoneInfo("America/New_York")


def settings():
    return Settings(total_capital=50_000, day_allocation_pct=20, swing_allocation_pct=80, risk_per_trade_pct=2.0,
                    max_positions_per_strategy=8, max_position_pct_of_strategy_capital=70,
                    watchlist=["AAPL", "MSFT", "NVDA"], park_idle_in_index=True)


def test_tick_closed_market_does_nothing(tmp_path, provider):
    b = PaperBroker(5000, tmp_path / "a.json", slippage_pct=0)
    ev = tick(provider(), b, settings(), {}, ["AAPL"], now_ny=datetime(2026, 11, 7, 12, 0, tzinfo=NY))
    assert ev == [] and b.positions == {}


def test_tick_does_swing_and_day_and_saves_rankings(tmp_path, provider):
    b = PaperBroker(5000, tmp_path / "a.json", slippage_pct=0)
    state = {}
    universe = [f"S{i}" for i in range(20)]
    tick(provider(), b, settings(), state, universe, now_ny=datetime(2026, 11, 3, 11, 0, tzinfo=NY))
    assert state.get("day_list") and state.get("day_top") and "swing_top" in state
    assert state["last_swing_date"] == "2026-11-03"
    first = state["last_search_ts"]
    tick(provider(), b, settings(), state, universe, now_ny=datetime(2026, 11, 3, 11, 5, tzinfo=NY))
    assert state["last_search_ts"] == first  # ingen ny sökning efter 5 min
    tick(provider(), b, settings(), state, universe, now_ny=datetime(2026, 11, 3, 11, 31, tzinfo=NY))
    assert state["last_search_ts"] > first  # ny sökning efter 30 min
    tick(provider(), b, settings(), state, universe, now_ny=datetime(2026, 11, 3, 15, 55, tzinfo=NY))
    assert not any(p.get("strategy") == "day" for p in b.positions.values())
    import json
    json.dumps(state, default=str)  # måste gå att spara


def test_tick_force_searches_again(tmp_path, provider):
    b = PaperBroker(5000, tmp_path / "a.json", slippage_pct=0)
    state = {}
    t = datetime(2026, 11, 3, 11, 0, tzinfo=NY)
    tick(provider(), b, settings(), state, ["S1", "S2"], now_ny=t)
    tick(provider(), b, settings(), state, ["S1", "S2"], now_ny=t.replace(minute=2), force=True)
    assert state["last_search_time"].endswith("17:02")


def test_tick_closes_leftover_day_positions_when_closed(tmp_path, provider):
    b = PaperBroker(5000, tmp_path / "a.json", slippage_pct=0)
    b.submit_order("AAPL", "buy", 1, price=100, strategy="day")
    ev = tick(provider(), b, settings(), {}, [], now_ny=datetime(2026, 11, 3, 17, 0, tzinfo=NY))
    assert "AAPL" not in b.positions and ev and ev[0]["action"] == "SÄLJ"
