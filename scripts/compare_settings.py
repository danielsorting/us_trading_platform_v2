"""Jämför Daniels backtest-inställningar med robotens (riktiga kurser, 5 år, 300 och 600 mest omsatta)."""
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd  # noqa: E402
from trading_platform.backtest import compare_strategies  # noqa: E402
from trading_platform.config import load_settings  # noqa: E402
from trading_platform.data import YahooResearchProvider  # noqa: E402
from trading_platform.risk import sek_to_usd  # noqa: E402
from trading_platform.strategies.variants import add_market, swing_variants  # noqa: E402

pd.set_option("display.width", 250)
s0 = load_settings()
p = YahooResearchProvider()
fx = p.usd_sek() or s0.usd_sek_fallback
spy = p.history("SPY", "5y", "1d")
syms = p.most_active(600)
allb = {k: add_market(v, spy) for k, v in p.history_many(syms, "5y", "1d").items() if v is not None and len(v) > 250}
V = swing_variants()
names = ["Swing – 5 av 6 (RSI, MACD och botten krävs)", "Utbrott – låt vinnare löpa", "Swing – låt vinnare löpa"]
rows = []
for n in (300, 600):
    bars = {k: allb[k] for k in syms[:n] if k in allb}
    for risk in (1.0, 2.0):
        for maxpct in (35, 70):
            s = replace(s0, risk_per_trade_pct=risk, max_position_pct_of_strategy_capital=maxpct)
            df = compare_strategies(bars, {k: V[k] for k in names}, sek_to_usd(100_000, fx), s, benchmark=spy.Close)
            df.insert(0, "Max/affär %", maxpct)
            df.insert(0, "Risk %", risk)
            df.insert(0, "Aktier", n)
            rows.append(df)
out = pd.concat(rows, ignore_index=True)
out["Poäng"] = (out["Per år %"] / out["Största nedgång %"].abs().clip(lower=5)).round(2)
print(out[["Aktier", "Strategi", "Risk %", "Max/affär %", "Per år %", "Avkastning %", "S&P 500 %", "Största nedgång %",
           "Affärer", "Testdel %", "Kontrolldel %", "Håller i kontroll?", "Poäng"]]
      .sort_values(["Aktier", "Poäng"], ascending=[True, False]).to_string(index=False))
