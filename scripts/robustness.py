"""Hur stabil är en inställning? Testar Daniels och robotens inställning på 10 olika slumpade
listor (250 av de 400 mest omsatta, 5 år). En bra inställning vinner på de flesta listor."""
import random
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd  # noqa: E402
from trading_platform.backtest import run_backtest  # noqa: E402
from trading_platform.config import load_settings  # noqa: E402
from trading_platform.data import YahooResearchProvider  # noqa: E402
from trading_platform.risk import sek_to_usd  # noqa: E402
from trading_platform.strategies.variants import add_market, swing_variants  # noqa: E402

s0 = load_settings()
p = YahooResearchProvider()
fx = p.usd_sek() or s0.usd_sek_fallback
spy = p.history("SPY", "5y", "1d")
syms = p.most_active(400)
allb = {k: add_market(v, spy) for k, v in p.history_many(syms, "5y", "1d").items() if v is not None and len(v) > 250}
V = swing_variants()
setups = {
    "Daniels (5 av 6, risk 2 %, max 35 %)": ("Swing – 5 av 6 (RSI, MACD och botten krävs)", 2.0, 35),
    "Robotens (Utbrott, risk 1 %, max 70 %)": ("Utbrott – låt vinnare löpa", 1.0, 70),
}
rows = []
pool = [k for k in syms if k in allb]
for i in range(10):
    random.seed(i)
    pick = random.sample(pool, min(250, len(pool)))
    bars = {k: allb[k] for k in pick}
    times = sorted(set().union(*[b.index for b in bars.values()]))
    first = times[220]
    for name, (var, risk, mx) in setups.items():
        s = replace(s0, risk_per_trade_pct=risk, max_position_pct_of_strategy_capital=mx)
        st = run_backtest(bars, V[var], sek_to_usd(100_000, fx), s, start=first)["stats"]
        years = (times[-1] - first).days / 365.25
        rows.append({"Lista": i + 1, "Inställning": name,
                     "Per år %": round(((1 + st["total_return_pct"] / 100) ** (1 / years) - 1) * 100, 1),
                     "Totalt %": st["total_return_pct"], "Största nedgång %": st["max_drawdown_pct"]})
df = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print(df.pivot(index="Lista", columns="Inställning", values="Per år %").to_string())
print("\nSammanfattning (per år %):")
print(df.groupby("Inställning")["Per år %"].describe()[["mean", "min", "50%", "max"]].round(1).to_string())
print("\nStörsta nedgång (snitt %):")
print(df.groupby("Inställning")["Största nedgång %"].mean().round(1).to_string())
w = df.pivot(index="Lista", columns="Inställning", values="Per år %")
print("\nDaniels vinner på", int((w.iloc[:, 0] > w.iloc[:, 1]).sum()), "av", len(w), "listor"
      if w.columns[0].startswith("Daniels") else "")
