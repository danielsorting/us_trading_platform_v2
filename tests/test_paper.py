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
    acts = run_paper_cycle(b, rows, Settings(max_positions_per_strategy=3), {})
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
