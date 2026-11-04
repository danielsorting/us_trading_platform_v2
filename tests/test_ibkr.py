"""IBKR-kopplingen testas mot en låtsasversion av IBKR (inget riktigt konto behövs)."""
from dataclasses import replace

import pytest

from trading_platform.brokers.ibkr import IBKRBroker, LiveAccountRefused
from trading_platform.config import Settings
from trading_platform.paper_trader import run_paper_cycle


class FakeIBClient:
    def __init__(self, account="DU1234567", cash=10_000.0, fill=True, partial=None):
        self.account, self.cash, self.fill, self.partial = account, cash, fill, partial
        self.pos, self.placed, self.connected = {}, [], False

    def connect(self):
        self.connected = True
        return self.account

    def disconnect(self):
        self.connected = False

    def cash_usd(self, usd_sek=None):
        return self.cash

    def positions(self):
        return dict(self.pos)

    def place(self, symbol, side, quantity, limit_price, timeout_s=30):
        self.placed.append((symbol, side, quantity, limit_price))
        if not self.fill:
            return {"filled": 0, "avg_price": 0, "commission": 0, "status": "Cancelled"}
        q = self.partial or quantity
        price = limit_price / 1.003 if side == "buy" else limit_price / 0.997
        self.pos[symbol] = self.pos.get(symbol, 0) + (q if side == "buy" else -q)
        self.cash += -q * price - 1 if side == "buy" else q * price - 1
        return {"filled": q, "avg_price": price, "commission": 1.0, "status": "Filled"}


def mk(tmp_path, client=None, **kw):
    s = replace(Settings(), **kw)
    b = IBKRBroker(client or FakeIBClient(), tmp_path / "ib.json", s, stop_file=tmp_path / "STOPP")
    b.connect_and_sync()
    return b


def test_refuses_real_money_account(tmp_path):
    c = FakeIBClient(account="U7654321")  # riktiga konton börjar inte med DU
    with pytest.raises(LiveAccountRefused):
        mk(tmp_path, c)
    assert not c.connected


def test_buy_sell_with_limit_orders_and_whole_shares(tmp_path):
    b = mk(tmp_path)
    assert b.cash == 10_000 and b.get_account()["environment"] == "IBKR_PAPER"
    o = b.submit_order("AAPL", "buy", 5.7, price=100.0, stop=95, strategy="swing")
    assert b.client.placed[0] == ("AAPL", "buy", 5, 100.3)  # hela aktier, limit +0,3 %
    assert b.positions["AAPL"]["quantity"] == 5 and b.positions["AAPL"]["stop"] == 95
    assert o["fee"] == 1.0
    o = b.submit_order("AAPL", "sell", 5, price=110.0)
    assert b.client.placed[1][3] == pytest.approx(109.67)
    assert "AAPL" not in b.positions and o["pnl"] == pytest.approx(5 * 10 - 2)
    again = IBKRBroker(FakeIBClient(), tmp_path / "ib.json", b.settings)  # bokföringen sparas
    assert len(again.orders) == 2


def test_safety_limits_block_buys(tmp_path):
    b = mk(tmp_path, ibkr_max_order_usd=1000)
    with pytest.raises(ValueError, match="max per affär"):
        b.submit_order("AAPL", "buy", 20, price=100.0)
    (tmp_path / "STOPP").write_text("")
    with pytest.raises(ValueError, match="Nödstopp"):
        b.submit_order("AAPL", "buy", 1, price=100.0)
    assert b.client.placed == []


def test_daily_loss_and_order_count_limits(tmp_path):
    b = mk(tmp_path, ibkr_max_daily_loss_usd=20, ibkr_max_orders_per_day=5)
    b.submit_order("A", "buy", 10, price=100.0)
    b.submit_order("A", "sell", 10, price=97.0)  # förlust ca 32 USD
    with pytest.raises(ValueError, match="förlust"):
        b.submit_order("B", "buy", 1, price=100.0)


def test_no_fill_raises_and_cycle_survives(tmp_path):
    b = mk(tmp_path, FakeIBClient(fill=False))
    with pytest.raises(ValueError, match="Ingen fyllning"):
        b.submit_order("A", "buy", 1, price=100.0)
    assert b.positions == {}


def test_partial_fill_is_booked(tmp_path):
    b = mk(tmp_path, FakeIBClient(partial=3))
    b.submit_order("A", "buy", 10, price=100.0)
    assert b.positions["A"]["quantity"] == 3


def test_reconcile_mismatch_halts_buys_but_ignores_own_stocks(tmp_path):
    c = FakeIBClient()
    b = mk(tmp_path, c)
    b.submit_order("A", "buy", 10, price=100.0)
    c.pos["A"] = 4          # någon har sålt 6 st manuellt
    c.pos["VOLV"] = 100     # Daniels egen aktie
    b2 = IBKRBroker(c, tmp_path / "ib.json", b.settings)
    b2.connect_and_sync()
    assert b2.halted and "A" in b2.halted and "VOLV" not in b2.halted
    assert any("VOLV" in n for n in b2.notes)
    with pytest.raises(ValueError, match="Avvikelse"):
        b2.submit_order("B", "buy", 1, price=50.0)
    o = b2.submit_order("A", "sell", 4, price=100.0)  # sälj går fortfarande
    assert o["quantity"] == 4


def test_close_all_and_paper_cycle(tmp_path):
    b = mk(tmp_path)
    s = replace(b.settings, max_fee_share_of_profit_pct=1000, allow_fractional_shares=False)
    rows = [{"symbol": "A", "strategy": "swing", "signal": "CANDIDATE", "price": 100.0,
             "stop_reference": 95.0, "target_reference": 115.0}]
    run_paper_cycle(b, rows, s, {"A": 100.0})
    assert "A" in b.positions
    acts = b.close_all({"A": 101.0})
    assert acts[0]["action"] == "SÄLJ" and b.positions == {}


def test_failed_sell_in_cycle_does_not_crash(tmp_path):
    c = FakeIBClient()
    b = mk(tmp_path, c)
    b.submit_order("A", "buy", 10, price=100.0, stop=95.0, strategy="swing")
    c.fill = False
    acts = run_paper_cycle(b, [], b.settings, {"A": 90.0})
    assert any(a["action"] == "sälj misslyckades" for a in acts) and "A" in b.positions


def test_ibkr_off_by_default():
    from trading_platform.config import load_settings
    assert load_settings().ibkr_enabled is False and Settings().ibkr_port == 4002
