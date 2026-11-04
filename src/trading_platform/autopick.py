"""Robotens automatiska val: nattrapporten testar alla swing-strategier ÄRLIGT (utan
facit i efterhand, se honest.py) med risk 1/2 %, ledig kassa i S&P 500 eller inte och
de 300 eller 600 mest omsatta, och sparar den bästa godkända kombinationen i
reports/best_choice.json. Roboten använder den om
auto_pick_best är påslaget i config.json."""
import json
from dataclasses import replace
from datetime import date, datetime

from .config import PROJECT_ROOT

CHOICE_FILE = PROJECT_ROOT / "reports" / "best_choice.json"
MAX_AGE_DAYS = 8          # äldre val än så används inte (nattrapporten har slutat fungera)
MAX_RISK_PCT = 2.0        # säkerhetsgräns: aldrig högre risk än 2 % per affär


def load_choice(path=CHOICE_FILE, today=None):
    """Senaste giltiga valet eller None."""
    try:
        c = json.loads(path.read_text())
        age = ((today or date.today()) - datetime.strptime(c["date"], "%Y-%m-%d").date()).days
        if age > MAX_AGE_DAYS:
            return None
        return c
    except Exception:
        return None


def apply(settings, variants, path=CHOICE_FILE, today=None):
    """Returnerar (inställningar, val). Utan giltigt val: oförändrade inställningar, None."""
    if not getattr(settings, "auto_pick_best", False):
        return settings, None
    c = load_choice(path, today)
    if not c or c.get("swing_variant") not in variants:
        return settings, None
    risk = min(float(c.get("risk_per_trade_pct", settings.risk_per_trade_pct)), MAX_RISK_PCT)
    n = int(c.get("swing_liquid_n", settings.swing_liquid_n))
    mx = float(c.get("max_position_pct_of_strategy_capital", settings.max_position_pct_of_strategy_capital))
    park = bool(c.get("park_idle_in_index", settings.park_idle_in_index))
    return replace(settings, swing_variant=c["swing_variant"], risk_per_trade_pct=risk, swing_liquid_n=n,
                   max_position_pct_of_strategy_capital=mx, park_idle_in_index=park), c
