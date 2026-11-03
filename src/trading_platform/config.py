"""Inställningar för plattformen. Värdena läses från config.json."""
from dataclasses import dataclass, field, fields
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config.json"

# Tillåtna lägen. "live" (riktiga pengar) finns medvetet inte här ännu.
ALLOWED_MODES = ("scan_only", "paper")


@dataclass
class Settings:
    account_currency: str = "SEK"
    total_capital: float = 10_000
    day_allocation_pct: float = 50
    swing_allocation_pct: float = 50
    risk_per_trade_pct: float = 0.5
    max_positions_per_strategy: int = 3
    max_position_pct_of_strategy_capital: float = 25
    news_filter_enabled: bool = True        # köp inte aktier med tydligt dåliga nyheter senaste 48 h
    news_market_risk_cut: bool = True       # halvera risken per affär när världsnyheterna är tydligt negativa
    extended_hours_swing: bool = True       # swing handlar även i för- och efterhandel (Avanza)
    extended_slippage_pct: float = 0.15     # större skillnad köp/sälj utanför ordinarie tid
    day_max_position_pct: float | None = None   # egen gräns för daytrades (None = samma som ovan)
    # Delaktier (t.ex. 0,35 aktie). Behövs ofta med litet kapital.
    allow_fractional_shares: bool = True
    scan_mode: str = "watchlist"
    watchlist: list[str] = field(default_factory=lambda: ["AAPL", "MSFT", "NVDA", "TSLA", "PLTR", "COIN"])
    include_etfs: bool = False
    minimum_average_daily_dollar_volume: float = 5_000_000
    mode: str = "scan_only"
    # Valuta: används om aktuell USD/SEK-kurs inte kan hämtas.
    usd_sek_fallback: float = 10.0
    # Handelskostnader (ungefär som Interactive Brokers "Fixed" för USA-aktier).
    commission_per_share_usd: float = 0.005
    min_commission_usd: float = 1.0
    # Courtage i procent av beloppet (t.ex. Avanza 0,04 %) och valutaväxlingsavgift i %.
    commission_pct: float = 0.0
    fx_fee_pct: float = 0.0
    slippage_pct: float = 0.05
    # Kostnadsfilter: hoppa över affärer där avgifterna äter mer än så här (%) av förväntad vinst.
    max_fee_share_of_profit_pct: float = 20.0
    # Parkera ledigt kapital i en indexfond (SPY) när roboten inte har affärer.
    park_idle_in_index: bool = False
    park_symbol: str = "SPY"
    # Automatisk handel i bakgrunden (GitHub Actions, scripts/auto_trade.py).
    auto_trading_enabled: bool = True
    auto_day_top_n: int = 10
    auto_max_move_pct: float = 2.0
    # Ändra detta värde för att nollställa det simulerade kontot på webbsidan (en gång).
    paper_reset_id: str = ""
    # Vilka aktier swing söker bland: "liquid" = de mest omsatta + bevakningslistan, "watchlist" = bara listan.
    swing_universe: str = "watchlist"
    # Vilken swing-variant scanner och simulerad handel använder som standard.
    swing_variant: str = "Swing – grund"
    # Vilken daytrading-variant som används (se strategies/day_variants.py).
    day_variant: str = "Day – momentum (nuvarande)"
    # Max antal aktier per skanning när hela marknaden väljs.
    max_symbols_per_scan: int = 300

    @property
    def day_capital(self):
        return self.total_capital * self.day_allocation_pct / 100

    @property
    def swing_capital(self):
        return self.total_capital * self.swing_allocation_pct / 100

    def validate(self):
        if self.total_capital <= 0:
            raise ValueError("Total capital must be positive.")
        if not 0 <= self.day_allocation_pct <= 100:
            raise ValueError("Day allocation must be 0–100%.")
        if not 0 <= self.swing_allocation_pct <= 100:
            raise ValueError("Swing allocation must be 0–100%.")
        if abs(self.day_allocation_pct + self.swing_allocation_pct - 100) > 1e-6:
            raise ValueError("Day and swing allocations must add up to 100%.")
        if not 0 < self.risk_per_trade_pct <= 2:
            raise ValueError("Risk per trade must be >0 and <=2%.")
        if self.usd_sek_fallback <= 0:
            raise ValueError("USD/SEK fallback rate must be positive.")
        if self.mode not in ALLOWED_MODES:
            raise ValueError(
                f"Mode must be one of {ALLOWED_MODES}; live trading with real money is disabled."
            )


def load_settings(path=None):
    p = Path(path) if path else DEFAULT_CONFIG
    if not p.exists():
        return Settings()
    data = json.loads(p.read_text())
    known = {f.name for f in fields(Settings)}
    s = Settings(**{k: v for k, v in data.items() if k in known})
    s.validate()
    return s
