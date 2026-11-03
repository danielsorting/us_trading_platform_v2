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


def test_tick_day_then_swing_near_close(tmp_path, provider):
    b = PaperBroker(5000, tmp_path / "a.json", slippage_pct=0)
    state = {}
    universe = [f"S{i}" for i in range(20)]
    ev = tick(provider(), b, settings(), state, universe, now_ny=datetime(2026, 11, 3, 11, 0, tzinfo=NY))
    assert state.get("day_list") and "last_swing_date" not in state  # swing väntar till nära stängning
    assert all(e["tid"] and e["typ"] in ("day", "swing") for e in ev)
    tick(provider(), b, settings(), state, universe, now_ny=datetime(2026, 11, 3, 15, 35, tzinfo=NY))
    assert state["last_swing_date"] == "2026-11-03"
    # efter 15:50 finns inga dagpositioner kvar
    tick(provider(), b, settings(), state, universe, now_ny=datetime(2026, 11, 3, 15, 55, tzinfo=NY))
    assert not any(p.get("strategy") == "day" for p in b.positions.values())


def test_tick_closes_leftover_day_positions_when_closed(tmp_path, provider):
    b = PaperBroker(5000, tmp_path / "a.json", slippage_pct=0)
    b.submit_order("AAPL", "buy", 1, price=100, strategy="day")
    ev = tick(provider(), b, settings(), {}, [], now_ny=datetime(2026, 11, 3, 17, 0, tzinfo=NY))
    assert "AAPL" not in b.positions and ev and ev[0]["action"] == "SÄLJ"
