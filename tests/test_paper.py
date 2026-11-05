from datetime import datetime, timedelta

import pytest

from trading_platform.brokers.paper import PaperBroker
from trading_platform.config import Settings
from trading_platform.paper_trader import run_paper_cycle


def broker(tmp_path, cash=1000):
    return PaperBroker(cash, tmp_path / "acct.json", slippage_pct=0)


def test_buy_sell_and_persist(tmp_path):
    b = broker(tmp_path)
    b.submit_order("AAPL", "buy", 0.5, price=200, stop=190, strategy="swing")
    o = b.submit_order("AAPL", "sell", 0.5, price=210)
    assert o["pnl"] == pytest.approx(5 - 2 * 1.0)  # +5 USD minus 2 × courtage (1 % tak = 1,0/1,05)
    again = PaperBroker(state_file=tmp_path / "acct.json")
    assert again.cash == pytest.approx(b.cash)
    assert len(again.orders) == 2


def test_cannot_overspend_or_oversell(tmp_path):
    b = broker(tmp_path, cash=100)
    with pytest.raises(ValueError):
        b.submit_order("AAPL", "buy", 1, price=200)
    with pytest.raises(ValueError):
        b.submit_order("MSFT", "sell", 1, price=10)


def test_cycle_buys_candidates_and_respects_limits(tmp_path):
    b = broker(tmp_path)
    rows = [{"symbol": s, "strategy": "swing", "signal": "CANDIDATE", "price": 100.0,
             "stop_reference": 95.0, "target_reference": 110.0} for s in ["A", "B", "C", "D"]]
    rows.append({"symbol": "E", "strategy": "swing", "signal": "WATCH", "price": 100.0, "stop_reference": 95.0})
    acts = run_paper_cycle(b, rows, Settings(max_positions_per_strategy=3, max_fee_share_of_profit_pct=1000), {})
    assert sorted(b.positions) == ["A", "B", "C"]
    assert any(a["action"] == "hoppar över" for a in acts)


def test_cycle_sells_on_stop_target_and_old_day_positions(tmp_path):
    b = broker(tmp_path)
    b.submit_order("S", "buy", 1, price=100, stop=95, target=110, strategy="swing")
    b.submit_order("T", "buy", 1, price=100, stop=95, target=110, strategy="swing")
    b.submit_order("D", "buy", 1, price=100, stop=95, target=110, strategy="day")
    tomorrow = datetime.now() + timedelta(days=1)
    acts = run_paper_cycle(b, [], Settings(), {"S": 94, "T": 111, "D": 100}, now=tomorrow)
    reasons = {a["symbol"]: a["detail"] for a in acts if a["action"] == "SÄLJ"}
    assert "stop" in reasons["S"] and "kursmål" in reasons["T"] and "dagposition" in reasons["D"]
    assert b.positions == {}


def test_parking_buys_spy_and_sells_it_to_fund_trades(tmp_path):
    from dataclasses import replace
    b = broker(tmp_path, cash=1000)
    s = replace(Settings(), park_idle_in_index=True, day_allocation_pct=0, swing_allocation_pct=100,
                max_fee_share_of_profit_pct=1000)
    run_paper_cycle(b, [], s, {"SPY": 500})
    assert b.positions["SPY"]["strategy"] == "park"
    assert b.cash < 50  # nästan allt parkerat
    rows = [{"symbol": "A", "strategy": "swing", "signal": "CANDIDATE", "price": 100.0,
             "stop_reference": 95.0, "target_reference": 110.0}]
    acts = run_paper_cycle(b, rows, s, {"SPY": 500, "A": 100})
    assert "A" in b.positions  # köpet gick igenom tack vare såld SPY
    assert any(a["action"] == "SÄLJ (parkering)" for a in acts)
    assert b.positions["SPY"]["strategy"] == "park"  # resten parkerad igen
    run_paper_cycle(b, [], s, {"SPY": 400, "A": 100})
    assert "SPY" in b.positions  # parkering har ingen stop loss


def test_parking_keeps_day_trading_share_as_cash(tmp_path):
    """Daytrading-andelen ska ligga kvar som kontanter (ingen SPY-handel vid varje daytrade)."""
    from dataclasses import replace
    b = broker(tmp_path, cash=10_000)
    s = replace(Settings(), park_idle_in_index=True, day_allocation_pct=20, swing_allocation_pct=80,
                max_fee_share_of_profit_pct=1000)
    run_paper_cycle(b, [], s, {"SPY": 500})
    assert 2_000 <= b.cash <= 2_300  # ca 20 % + 1 % buffert kvar
    rows = [{"symbol": "A", "strategy": "day", "signal": "CANDIDATE", "price": 100.0,
             "stop_reference": 99.0, "target_reference": 102.0}]
    acts = run_paper_cycle(b, rows, replace(s, max_position_pct_of_strategy_capital=10), {"SPY": 500, "A": 100})
    assert "A" in b.positions
    assert not any("parkering" in a["action"] for a in acts)  # ingen SPY-försäljning behövdes


def test_fee_filter_skips_tiny_trades(tmp_path):
    """Användarens fall: ca 500 kr per daytrade → avgifterna äter vinsten → hoppa över."""
    from trading_platform.risk import fees_too_high
    assert fees_too_high(0.5, 100, 99.5, 101)            # 50 USD-affär, mål +1 %: avgifter > vinst
    assert not fees_too_high(20, 100, 97, 106)           # 2 000 USD-affär, mål +6 %: ok
    b = broker(tmp_path, cash=1000)
    rows = [{"symbol": "A", "strategy": "day", "signal": "CANDIDATE", "price": 100.0,
             "stop_reference": 99.5, "target_reference": 101.0}]
    acts = run_paper_cycle(b, rows, Settings(max_position_pct_of_strategy_capital=5), {"A": 100})
    assert "A" not in b.positions and any("Avgifterna" in a["detail"] for a in acts)


def test_pnl_breakdown_separates_swing_day_and_park(tmp_path):
    from trading_platform.paper_trader import pnl_breakdown
    b = broker(tmp_path, cash=10_000)
    b.commission_per_share = 0
    b.min_commission = 0
    b.submit_order("S", "buy", 10, price=100, strategy="swing")
    b.submit_order("S", "sell", 10, price=110, strategy="swing")      # swing +100
    b.submit_order("D", "buy", 10, price=50, strategy="day")
    b.submit_order("D", "sell", 10, price=48, strategy="day")         # day −20
    b.submit_order("SPY", "buy", 2, price=500, strategy="park")
    r = pnl_breakdown(b, {"SPY": 505})                                # park +10 orealiserat
    assert round(r["swing"]["total"], 2) == 100 and r["swing"]["wins"] == 1
    assert round(r["day"]["total"], 2) == -20
    assert round(r["park"]["unrealized"], 2) == 10 and r["park"]["open"] == 1


def test_reset_id_survives_save_and_load(tmp_path):
    b = broker(tmp_path)
    b.submit_order("A", "buy", 1, price=100, strategy="swing")
    b.reset(5000, 10.0, reset_id="x1")
    again = PaperBroker(state_file=tmp_path / "acct.json")
    assert again.reset_id == "x1" and again.positions == {} and again.cash == 5000


def test_strategy_budget_is_never_exceeded(tmp_path):
    """Daytrading får tillsammans högst använda daytrading-andelen – aldrig swingens pengar."""
    from dataclasses import replace
    b = broker(tmp_path, cash=10_000)
    s = replace(Settings(), day_allocation_pct=20, swing_allocation_pct=80, risk_per_trade_pct=2,
                max_position_pct_of_strategy_capital=70, allow_fractional_shares=False,
                max_fee_share_of_profit_pct=1000, max_positions_per_strategy=8)
    rows = [{"symbol": x, "strategy": "day", "signal": "CANDIDATE", "price": 100.0,
             "stop_reference": 99.0, "target_reference": 102.0} for x in "ABCD"]
    acts = run_paper_cycle(b, rows, s, {x: 100.0 for x in "ABCD"})
    used = sum(p["quantity"] * 100 for p in b.positions.values())
    assert used <= 2_000 and b.positions["A"]["quantity"] == 14  # 70 % av 2 000 USD
    assert any("budgeten är fullt använd" in a["detail"] for a in acts)


def test_day_trades_have_their_own_max_size(tmp_path):
    from dataclasses import replace
    b = broker(tmp_path, cash=10_000)
    s = replace(Settings(), day_allocation_pct=20, swing_allocation_pct=80, risk_per_trade_pct=2,
                max_position_pct_of_strategy_capital=70, day_max_position_pct=25, allow_fractional_shares=False,
                max_fee_share_of_profit_pct=1000, max_positions_per_strategy=8)
    rows = [{"symbol": x, "strategy": "day", "signal": "CANDIDATE", "price": 100.0,
             "stop_reference": 99.0, "target_reference": 102.0} for x in "ABCD"]
    run_paper_cycle(b, rows, s, {x: 100.0 for x in "ABCD"})
    assert sorted(b.positions) == list("ABCD")  # fyra affärer ryms nu
    assert all(p["quantity"] == 5 for p in b.positions.values())  # 25 % av 2 000 USD = 500 USD


def test_no_tiny_leftover_trades(tmp_path):
    """När bara smulor av budgeten är kvar ska roboten inte köpa en pytteliten affär."""
    from dataclasses import replace
    b = broker(tmp_path, cash=10_000)
    s = replace(Settings(), day_allocation_pct=20, swing_allocation_pct=80, risk_per_trade_pct=2,
                max_position_pct_of_strategy_capital=70, day_max_position_pct=45, allow_fractional_shares=False,
                max_fee_share_of_profit_pct=1000, max_positions_per_strategy=8)
    rows = [{"symbol": x, "strategy": "day", "signal": "CANDIDATE", "price": 100.0,
             "stop_reference": 99.0, "target_reference": 102.0} for x in "ABC"]
    acts = run_paper_cycle(b, rows, s, {x: 100.0 for x in "ABC"})
    assert sorted(b.positions) == ["A", "B"]  # 9 + 9 aktier; C skulle bara få 2 → hoppas över
    assert any("budgeten är fullt använd" in a["detail"] for a in acts)


def test_protections_cooldown_and_stoploss_guard(tmp_path):
    """Skydd: köp inte samma aktie direkt efter stop loss; efter 3 stop loss i daytrading – inga fler i dag."""
    from dataclasses import replace
    from trading_platform.paper_trader import protections
    b = broker(tmp_path, cash=10_000)
    for sym in ("A", "B", "C"):
        b.submit_order(sym, "buy", 1, price=100, strategy="day", stop=99)
        b.submit_order(sym, "sell", 1, price=98, strategy="day", reason="stop loss")
    blocked, guard = protections(b)
    assert set(blocked) == {"A", "B", "C"} and guard
    s = replace(Settings(), max_fee_share_of_profit_pct=1000, allow_fractional_shares=False)
    rows = [{"symbol": x, "strategy": "day", "signal": "CANDIDATE", "price": 100.0, "stop_reference": 99.0,
             "target_reference": 102.0} for x in ("A", "D")]
    acts = run_paper_cycle(b, rows, s, {"A": 100.0, "D": 100.0})
    assert "A" not in b.positions and "D" not in b.positions
    assert any("Paus efter stop loss" in a["detail"] for a in acts)
    assert any("inga fler daytrades i dag" in a["detail"] for a in acts)


def test_tests_do_not_depend_on_time_of_day(tmp_path):
    """Regressionstest: daytrades köps i testerna även om klockan egentligen är efter börsens stängning."""
    from trading_platform import paper_trader
    assert paper_trader.ny_clock().time() < paper_trader.DAY_CLOSE_NY
    b = broker(tmp_path, cash=10_000)
    rows = [{"symbol": "A", "strategy": "day", "signal": "CANDIDATE", "price": 100.0,
             "stop_reference": 99.0, "target_reference": 102.0}]
    run_paper_cycle(b, rows, Settings(max_fee_share_of_profit_pct=1000), {"A": 100.0})
    assert "A" in b.positions


def test_real_clock_closes_day_trades_after_close(tmp_path):
    """Den riktiga regeln gäller fortfarande: efter 15:50 i New York säljs daytrades."""
    from zoneinfo import ZoneInfo
    b = broker(tmp_path, cash=10_000)
    b.submit_order("A", "buy", 1, price=100, strategy="day")
    late = datetime(2026, 11, 4, 18, 20, tzinfo=ZoneInfo("America/New_York"))  # som nattrapporten
    run_paper_cycle(b, [], Settings(), {"A": 100.0}, ny_now=late)
    assert "A" not in b.positions
