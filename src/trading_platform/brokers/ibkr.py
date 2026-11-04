"""Interactive Brokers (IBKR) – koppling för robotens order.

SÄKERHET (kan inte stängas av i inställningarna):
  * Bara IBKR:s LÅTSASKONTO tillåts. IBKR:s låtsaskonton har kontonummer som
    börjar med "DU". Ansluter roboten till något annat konto kopplar den ned
    direkt och vägrar handla. Riktiga pengar är alltså omöjligt med denna kod.
  * Nödstopp: finns filen STOPP i projektmappen görs inga nya köp (sälj går).
  * Max belopp per köp, max antal köp per dag och max förlust per dag.
  * Kontroll: roboten jämför sina egna positioner med vad IBKR säger. Stämmer
    det inte görs inga nya köp förrän du har tittat på det.
  * Limitorder i stället för marknadsorder: priset får max vara 0,3 % sämre än
    senaste kurs. Fylls ordern inte inom 30 sekunder makuleras den.

Robotens egen bokföring (stop loss, mål, strategi, resultat) sparas lokalt i
samma format som låtsaskontot, så resten av programmet fungerar likadant.
"""
import math
from datetime import datetime
from pathlib import Path

from .paper import PaperBroker

PAPER_ACCOUNT_PREFIX = "DU"


class LiveAccountRefused(RuntimeError):
    """Roboten är ansluten till ett konto med riktiga pengar – vägrar."""


def check_paper_account(account_id):
    if not str(account_id or "").upper().startswith(PAPER_ACCOUNT_PREFIX):
        raise LiveAccountRefused(
            f"Kontot {account_id!r} är inte ett IBKR-låtsaskonto (ska börja med {PAPER_ACCOUNT_PREFIX}). "
            "Roboten handlar inte med riktiga pengar.")


class IBClient:
    """Tunn kapsling runt biblioteket ib_async (pip install ib_async).

    Allt som pratar med IBKR finns här, så att testerna kan byta ut den mot en
    låtsasversion (FakeIBClient i testerna)."""

    def __init__(self, host="127.0.0.1", port=4002, client_id=7):
        self.host, self.port, self.client_id = host, port, client_id
        self.ib = None
        self.account = None

    def connect(self):
        from ib_async import IB  # importeras här så att resten av appen inte kräver biblioteket
        self.ib = IB()
        self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=20)
        accounts = self.ib.managedAccounts()
        self.account = accounts[0] if accounts else None
        return self.account

    def disconnect(self):
        if self.ib is not None:
            self.ib.disconnect()

    def cash_usd(self, usd_sek=None):
        vals = self.ib.accountValues(self.account)
        for v in vals:  # kontanter i USD om sådana finns
            if v.tag == "CashBalance" and v.currency == "USD":
                return float(v.value)
        for v in vals:  # annars hela kassan i kontots basvaluta
            if v.tag == "TotalCashValue" and v.currency in ("USD", "BASE", "SEK"):
                val = float(v.value)
                if v.currency == "USD":
                    return val
                return val / usd_sek if usd_sek else val
        raise RuntimeError("Hittade ingen kassa hos IBKR.")

    def positions(self):
        return {p.contract.symbol: float(p.position) for p in self.ib.positions(self.account)
                if p.contract.secType == "STK" and p.position}

    def place(self, symbol, side, quantity, limit_price, timeout_s=30):
        """Lägger en limitorder och väntar på fyllning. Returnerar
        {"filled": antal, "avg_price": pris, "commission": USD, "status": text}."""
        from ib_async import LimitOrder, Stock
        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        order = LimitOrder("BUY" if side == "buy" else "SELL", quantity, limit_price, tif="DAY",
                           outsideRth=False, account=self.account)
        trade = self.ib.placeOrder(contract, order)
        waited = 0.0
        while not trade.isDone() and waited < timeout_s:
            self.ib.sleep(0.5)
            waited += 0.5
        if not trade.isDone():
            self.ib.cancelOrder(trade.order)
            self.ib.sleep(2)
        self.ib.sleep(1)  # courtagerapporten kommer en stund efter fyllningen
        commission = sum((f.commissionReport.commission or 0.0) for f in trade.fills
                         if f.commissionReport and f.commissionReport.commission < 1e9)
        return {"filled": float(trade.orderStatus.filled or 0), "avg_price": float(trade.orderStatus.avgFillPrice or 0),
                "commission": float(commission), "status": trade.orderStatus.status}


class IBKRBroker(PaperBroker):
    """Samma gränssnitt som PaperBroker, men orderna går till IBKR:s låtsaskonto."""

    def __init__(self, client, state_file, settings, stop_file=None, usd_sek=None):
        self.client = client
        self.settings = settings
        self.stop_file = Path(stop_file) if stop_file else None
        self.usd_sek = usd_sek
        self.halted = None   # orsak till att köp är stoppade (t.ex. avvikelse mot IBKR)
        self.notes = []      # meddelanden till loggen
        self.account_id = None
        super().__init__(0, state_file, slippage_pct=0, usd_sek_at_start=usd_sek)

    # ---- anslutning och kontroll ---------------------------------------
    def connect_and_sync(self):
        self.account_id = self.client.connect()
        try:
            check_paper_account(self.account_id)
        except LiveAccountRefused:
            self.client.disconnect()
            raise
        self.cash = float(self.client.cash_usd(self.usd_sek))
        if not self.orders and not self.positions:  # första gången: startkassa = IBKR:s kassa
            self.starting_cash = self.cash
        self.reconcile()
        self.save()
        return self.account_id

    def reconcile(self):
        """Jämför robotens positioner med IBKR:s. Aktier du själv äger i kontot
        (som roboten inte köpt) rörs inte och räknas inte som fel."""
        ib = self.client.positions()
        problems = []
        for sym, pos in self.positions.items():
            have = ib.get(sym, 0.0)
            if abs(have - pos["quantity"]) > 1e-6:
                problems.append(f"{sym}: roboten tror {pos['quantity']:g} st, IBKR har {have:g} st")
        others = sorted(set(ib) - set(self.positions))
        if others:
            self.notes.append("Aktier i kontot som roboten inte köpt (rörs inte): " + ", ".join(others))
        if problems:
            self.halted = "Avvikelse mot IBKR – inga nya köp: " + "; ".join(problems)
            self.notes.append(self.halted)
        return problems

    # ---- regler före varje köp -----------------------------------------
    def _today_orders(self):
        today = datetime.now().date().isoformat()
        return [o for o in self.orders if str(o.get("time", "")).startswith(today)]

    def buy_block_reason(self, cost):
        s = self.settings
        if self.stop_file and self.stop_file.exists():
            return "Nödstopp är på (filen STOPP finns) – inga nya köp."
        if self.halted:
            return self.halted
        if cost > s.ibkr_max_order_usd:
            return f"Köpet ({cost:.0f} USD) är större än max per affär ({s.ibkr_max_order_usd:.0f} USD)."
        today = self._today_orders()
        if sum(1 for o in today if o["side"] == "buy") >= s.ibkr_max_orders_per_day:
            return f"Max {s.ibkr_max_orders_per_day} köp per dag är nått."
        lost = sum(o.get("pnl") or 0 for o in today)
        if lost <= -s.ibkr_max_daily_loss_usd:
            return f"Dagens förlust {lost:.0f} USD har nått gränsen – inga fler köp i dag."
        if cost > self.cash + 1e-9:
            return f"Inte tillräckligt med pengar: behöver {cost:.2f}, har {self.cash:.2f} USD."
        return None

    # ---- order ---------------------------------------------------------
    def submit_order(self, symbol, side, quantity, order_type="market", price=None, slippage_pct=None, **info):
        if price is None or price <= 0:
            raise ValueError("Behöver en kurs för att sätta limitpris.")
        side = side.lower()
        qty = math.floor(float(quantity) + 1e-9)  # IBKR via API: bara hela aktier
        if qty < 1:
            raise ValueError("IBKR handlar bara hela aktier – för litet antal.")
        buf = self.settings.ibkr_limit_buffer_pct / 100
        if side == "buy":
            if symbol in self.positions:
                raise ValueError(f"Äger redan {symbol}.")
            limit = round(price * (1 + buf), 2)
            reason = self.buy_block_reason(qty * limit)
            if reason:
                raise ValueError(reason)
        elif side == "sell":
            pos = self.positions.get(symbol)
            if not pos:
                raise ValueError(f"Roboten äger ingen {symbol}.")
            qty = min(qty, math.floor(pos["quantity"] + 1e-9))
            limit = round(price * (1 - buf), 2)
        else:
            raise ValueError("side måste vara 'buy' eller 'sell'")

        res = self.client.place(symbol, side, qty, limit, timeout_s=self.settings.ibkr_order_timeout_s)
        filled, fill, fee = res["filled"], res["avg_price"], res["commission"]
        if filled <= 0 or fill <= 0:
            raise ValueError(f"Ingen fyllning hos IBKR (status {res.get('status')}) – försöker igen senare.")
        return self._book(symbol, side, filled, fill, fee, info, limit, res.get("status"))

    def _book(self, symbol, side, qty, fill, fee, info, limit, status):
        now = datetime.now().isoformat(timespec="seconds")
        if side == "buy":
            self.cash -= qty * fill + fee
            self.positions[symbol] = {"quantity": qty, "avg_price": fill, "opened": now, "entry_fee": fee, **info}
            pnl = None
        else:
            pos = self.positions[symbol]
            share = qty / pos["quantity"]
            self.cash += qty * fill - fee
            pnl = qty * (fill - pos["avg_price"]) - fee - pos.get("entry_fee", 0) * share
            pos["quantity"] -= qty
            pos["entry_fee"] = pos.get("entry_fee", 0) * (1 - share)
            if pos["quantity"] <= 1e-9:
                del self.positions[symbol]
        order = {"id": f"ib{len(self.orders) + 1}", "time": now, "symbol": symbol, "side": side, "quantity": qty,
                 "price": round(fill, 4), "fee": round(fee, 4), "limit": limit, "ib_status": status,
                 "pnl": None if pnl is None else round(pnl, 2), **info}
        self.orders.append(order)
        self.save()
        return order

    def close_all(self, prices):
        """Nödknappen: säljer allt roboten äger. Returnerar en lista med vad som hände."""
        out = []
        for sym, pos in list(self.positions.items()):
            p = prices.get(sym) or pos["avg_price"]
            try:
                o = self.submit_order(sym, "sell", pos["quantity"], price=p, reason="nödstopp", strategy=pos.get("strategy"))
                out.append({"symbol": sym, "action": "SÄLJ", "detail": f"nödstopp, resultat {o['pnl']:+.2f} USD"})
            except ValueError as e:
                out.append({"symbol": sym, "action": "sälj misslyckades", "detail": str(e)})
        return out

    def get_account(self, prices=None):
        acc = super().get_account(prices)
        acc["environment"] = "IBKR_PAPER"
        return acc

    def to_dict(self):
        d = super().to_dict()
        d["ibkr_account"] = self.account_id
        return d

    def cancel_order(self, order_id):
        raise RuntimeError("Order som inte fylls makuleras automatiskt efter tidsgränsen.")
