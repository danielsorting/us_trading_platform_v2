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


def test_swing_searches_liquid_list_and_parking_is_labelled(tmp_path, provider):
    from dataclasses import replace
    b = PaperBroker(5000, tmp_path / "a.json", slippage_pct=0)
    s = replace(settings(), swing_universe="liquid")
    state = {}
    liquid = [f"L{i}" for i in range(15)]
    ev = tick(provider(), b, s, state, liquid, now_ny=datetime(2026, 11, 3, 11, 0, tzinfo=NY))
    swung = {r["symbol"] for r in state["swing_top"]}
    assert set(liquid) <= swung and {"AAPL", "MSFT", "NVDA"} <= swung
    assert all(e["typ"] == "park" for e in ev if "parkering" in e["action"])


def test_project_today_volume_scales_partial_day(bars):
    from trading_platform.autotrader import project_today_volume
    df = bars(30, start="2026-10-06")
    df.index = df.index[:-1].append(__import__("pandas").DatetimeIndex(["2026-11-03"]))
    before = df.Volume.iloc[-1]
    out = project_today_volume(df, datetime(2026, 11, 3, 11, 0, tzinfo=NY))  # 1,5 av 6,5 timmar
    assert abs(out.Volume.iloc[-1] - before / (1.5 / 6.5)) < 1
    assert out.Volume.iloc[-2] == df.Volume.iloc[-2]


def test_swing_summary_lists_candidates_and_near_misses():
    from trading_platform.autotrader import swing_summary
    rows = [{"symbol": "A", "signal": "CANDIDATE", "conditions_met": "6/6"},
            {"symbol": "B", "signal": "WATCH", "conditions_met": "5/6"},
            {"symbol": "C", "signal": "WATCH", "conditions_met": "2/6"},
            {"symbol": "D", "signal": "INSUFFICIENT_DATA"}]
    d = swing_summary(rows)["detail"]
    assert "Granskade 3 aktier: 1 köpkandidater (A)" in d and d.index("B (5/6)") < d.index("C (2/6)")


def test_day_summary_explains_closed_orb_window(intraday):
    from trading_platform.autotrader import day_summary
    from trading_platform.config import load_settings
    from trading_platform.daytop import rank_day_candidates
    from trading_platform.autotrader import day_strategy_for
    s = load_settings()
    ranked = rank_day_candidates({f"S{i}": intraday(3, seed=i) for i in range(5)}, day_strategy_for(s))
    d = day_summary(ranked, s, datetime(2026, 11, 3, 12, 30, tzinfo=NY))["detail"]
    assert d.startswith("Granskade 5 aktier") and "fönstret stängt" in d and "15:30–17:30" in d
    d2 = day_summary(ranked, s, datetime(2026, 11, 3, 10, 30, tzinfo=NY))["detail"]
    assert "fönstret stängt" not in d2


def test_skip_reason_when_price_below_stop(tmp_path):
    b = PaperBroker(5000, tmp_path / "a.json", slippage_pct=0)
    from trading_platform.paper_trader import run_paper_cycle
    rows = [{"symbol": "A", "strategy": "swing", "signal": "CANDIDATE", "price": 100.0,
             "stop_reference": 95.0, "target_reference": 110.0}]
    acts = run_paper_cycle(b, rows, settings(), {"A": 94.0})
    assert "A" not in b.positions and any("under stop loss" in a["detail"] for a in acts)
