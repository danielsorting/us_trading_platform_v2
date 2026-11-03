from .base import BrokerAdapter

class PaperBroker(BrokerAdapter):
    """Local mock adapter. Does not connect to any broker or send orders."""
    def __init__(self, starting_cash=10_000):
        self.cash=float(starting_cash); self.positions={}; self.orders=[]
    def get_account(self):
        return {"cash":self.cash,"currency":"SEK","environment":"LOCAL_PAPER"}
    def get_positions(self): return self.positions.copy()
    def submit_order(self, symbol, side, quantity, order_type="market"):
        raise RuntimeError("Mock broker is scan-only in this release; order execution is disabled.")
    def cancel_order(self, order_id):
        raise RuntimeError("No orders exist in scan-only mode.")
