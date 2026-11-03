"""Papper-mäklare: simulerad handel med låtsaspengar.

Ansluter INTE till någon mäklare och skickar inga riktiga order.
Kontot (kassa, positioner, affärshistorik) sparas i en JSON-fil så att
det finns kvar mellan körningar. Alla belopp är i USD.
"""
import json
import uuid
from datetime import datetime
from pathlib import Path

from ..risk import commission
from .base import BrokerAdapter


class PaperBroker(BrokerAdapter):
    """Local mock adapter. Does not connect to any broker or send orders."""

    def __init__(self, starting_cash=10_000, state_file=None, commission_per_share=0.005,
                 min_commission=1.0, slippage_pct=0.05, usd_sek_at_start=None):
        self.state_file = Path(state_file) if state_file else None
        self.commission_per_share = commission_per_share
        self.min_commission = min_commission
        self.slippage = slippage_pct / 100
        if self.state_file and self.state_file.exists():
            self.load()
        else:
            self.reset(starting_cash, usd_sek_at_start)

    # ---- kontot ---------------------------------------------------------
    def reset(self, starting_cash, usd_sek_at_start=None):
        self.cash = float(starting_cash)
        self.starting_cash = float(starting_cash)
        self.usd_sek_at_start = usd_sek_at_start
        self.created = datetime.now().isoformat(timespec="seconds")
        self.positions = {}
        self.orders = []
        self.save()

    def to_dict(self):
        return {"cash": self.cash, "starting_cash": self.starting_cash,
                "usd_sek_at_start": self.usd_sek_at_start, "created": self.created,
                "positions": self.positions, "orders": self.orders}

    def from_dict(self, d):
        self.cash = float(d["cash"])
        self.starting_cash = float(d.get("starting_cash", d["cash"]))
        self.usd_sek_at_start = d.get("usd_sek_at_start")
        self.created = d.get("created", "")
        self.positions = d.get("positions", {})
        self.orders = d.get("orders", [])

    def save(self):
        if self.state_file:
            self.state_file.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))

    def load(self):
        self.from_dict(json.loads(self.state_file.read_text()))

    def get_account(self, prices=None):
        prices = prices or {}
        value = sum(p["quantity"] * prices.get(s, p["avg_price"]) for s, p in self.positions.items())
        return {"cash": round(self.cash, 2), "positions_value": round(value, 2),
                "equity": round(self.cash + value, 2), "starting_cash": self.starting_cash,
                "currency": "USD", "environment": "LOCAL_PAPER"}

    def get_positions(self):
        return {s: dict(p) for s, p in self.positions.items()}

    # ---- order ----------------------------------------------------------
    def submit_order(self, symbol, side, quantity, order_type="market", price=None, **info):
        """Fyller ordern direkt till angivet pris (+/- slippage) med courtage.

        info: extra uppgifter som sparas med positionen, t.ex. stop, target, strategy.
        """
        if order_type != "market":
            raise ValueError("Paper broker supports market orders only.")
        if price is None or price <= 0:
            raise ValueError("Paper broker needs a positive price to simulate a fill.")
        side = side.lower()
        quantity = float(quantity)
        if quantity <= 0:
            raise ValueError("Quantity must be positive.")
        now = datetime.now().isoformat(timespec="seconds")

        if side == "buy":
            fill = price * (1 + self.slippage)
            fee = commission(quantity, self.commission_per_share, self.min_commission, fill)
            cost = quantity * fill + fee
            if cost > self.cash + 1e-9:
                raise ValueError(f"Insufficient cash: need {cost:.2f}, have {self.cash:.2f} USD.")
            if symbol in self.positions:
                raise ValueError(f"Already holding {symbol}.")
            self.cash -= cost
            self.positions[symbol] = {"quantity": quantity, "avg_price": fill, "opened": now,
                                      "entry_fee": fee, **info}
            pnl = None
        elif side == "sell":
            pos = self.positions.get(symbol)
            if not pos or quantity > pos["quantity"] + 1e-9:
                raise ValueError(f"Not enough {symbol} to sell.")
            fill = price * (1 - self.slippage)
            fee = commission(quantity, self.commission_per_share, self.min_commission, fill)
            self.cash += quantity * fill - fee
            share = quantity / pos["quantity"]
            pnl = quantity * (fill - pos["avg_price"]) - fee - pos.get("entry_fee", 0) * share
            pos["quantity"] -= quantity
            pos["entry_fee"] = pos.get("entry_fee", 0) * (1 - share)
            if pos["quantity"] <= 1e-9:
                del self.positions[symbol]
        else:
            raise ValueError("side must be 'buy' or 'sell'")

        order = {"id": uuid.uuid4().hex[:10], "time": now, "symbol": symbol, "side": side,
                 "quantity": quantity, "price": round(fill, 4), "fee": fee,
                 "pnl": None if pnl is None else round(pnl, 2), **info}
        self.orders.append(order)
        self.save()
        return order

    def cancel_order(self, order_id):
        raise RuntimeError("Paper orders are filled immediately; nothing to cancel.")
