from dataclasses import dataclass, field
import json
from pathlib import Path

@dataclass
class Settings:
    account_currency: str = "SEK"
    total_capital: float = 10_000
    day_allocation_pct: float = 50
    swing_allocation_pct: float = 50
    risk_per_trade_pct: float = 0.5
    max_positions_per_strategy: int = 3
    max_position_pct_of_strategy_capital: float = 25
    scan_mode: str = "watchlist"
    watchlist: list[str] = field(default_factory=lambda: ["AAPL","MSFT","NVDA","TSLA","PLTR","COIN"])
    include_etfs: bool = False
    minimum_average_daily_dollar_volume: float = 5_000_000
    mode: str = "scan_only"

    @property
    def day_capital(self): return self.total_capital * self.day_allocation_pct / 100
    @property
    def swing_capital(self): return self.total_capital * self.swing_allocation_pct / 100

    def validate(self):
        if self.total_capital <= 0: raise ValueError("Total capital must be positive.")
        if not 0 <= self.day_allocation_pct <= 100: raise ValueError("Day allocation must be 0–100%.")
        if not 0 <= self.swing_allocation_pct <= 100: raise ValueError("Swing allocation must be 0–100%.")
        if abs(self.day_allocation_pct + self.swing_allocation_pct - 100) > 1e-6:
            raise ValueError("Day and swing allocations must add up to 100%.")
        if not 0 < self.risk_per_trade_pct <= 2: raise ValueError("Risk per trade must be >0 and <=2%.")
        if self.mode != "scan_only":
            raise ValueError("This build only supports scan_only mode; live trading is disabled.")

def load_settings(path="config.json"):
    p = Path(path)
    if not p.exists(): return Settings()
    data = json.loads(p.read_text())
    s = Settings(**data)
    s.validate()
    return s
