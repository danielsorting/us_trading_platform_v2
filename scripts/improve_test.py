"""Kan roboten slå index? Ärligt test (utan facit) med DUBBEL kontroll:
inställningar väljs bara på TRÄNINGSÅREN, och sedan kontrolleras de på
KONTROLLÅREN som valet aldrig fick se."""
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import pandas as pd  # noqa: E402
from trading_platform.backtest import run_backtest  # noqa: E402
from trading_platform.config import load_settings  # noqa: E402
from trading_platform.data import YahooResearchProvider  # noqa: E402
from trading_platform.honest import start_time  # noqa: E402
from trading_platform.pit import make_allowed, pit_members  # noqa: E402
from trading_platform.risk import sek_to_usd  # noqa: E402
from trading_platform.rotation import run_rotation, spy_stats  # noqa: E402
from trading_platform.strategies.variants import add_market, daily_variants  # noqa: E402
from trading_platform.universe import load_us_equity_symbols  # noqa: E402

T0 = time.time()
SPLIT = "2024-06-01"


def log(m):
    print(f"[{time.time() - T0:6.0f}s] {m}", flush=True)


s0 = load_settings()
p = YahooResearchProvider()
fx = p.usd_sek() or s0.usd_sek_fallback
cap = sek_to_usd(100_000, fx)
rows = load_us_equity_symbols()
bad = re.compile(r"warrant|unit|right|preferred|notes|debenture|%", re.I)
syms = [x for x, n in zip(rows["symbol"], rows["name"].astype(str)) if "." not in x and "-" not in x
        and not bad.search(n)]
spy = p.history("SPY", "6y", "1d")
raw = {k: v for k, v in p.history_many(syms, "6y", "1d", chunk=200).items() if v is not None and len(v) > 80}
log(f"{len(raw)} aktier.")
members = pit_members(raw, n=300)
ever = set().union(*[m for _, m in members])
bars = {k: add_market(raw[k], spy) for k in ever}
del raw
start, _ = start_time(bars, members)
tz = start.tzinfo
split = pd.Timestamp(SPLIT).tz_localize(tz) if tz else pd.Timestamp(SPLIT)
end_t = max(b.index[-1] for b in bars.values())
windows = {"Träning": (start, split), "Kontroll": (split, None)}
allowed = make_allowed(members)
spy_w = {w: spy_stats(spy, a, b) for w, (a, b) in windows.items()}
print(f"\nTräning: {pd.Timestamp(start).date()} – {SPLIT}   Kontroll: {SPLIT} – {pd.Timestamp(end_t).date()}")
print(f"S&P 500: träning {spy_w['Träning']['per_year_pct']} %/år, kontroll {spy_w['Kontroll']['per_year_pct']} %/år\n")


def py(total, a, b):
    b = b or end_t
    yrs = max((pd.Timestamp(b) - pd.Timestamp(a)).days / 365.25, 0.1)
    return round(((1 + total / 100) ** (1 / yrs) - 1) * 100, 1)


out = []
V = daily_variants()
s = replace(s0, risk_per_trade_pct=1.0, park_idle_in_index=True, max_position_pct_of_strategy_capital=35)
for name in ("Utbrott + marknadsfilter – låt vinnare löpa", "Utbrott – låt vinnare löpa", "Swing – låt vinnare löpa"):
    for rank in (None, "momentum"):
        row = {"Metod": name + (" + välj starkaste först" if rank else "")}
        for w, (a, b) in windows.items():
            st = run_backtest(bars, V[name], cap, s, start=a, end=b, allowed=allowed, rank=rank)["stats"]
            row[f"{w} %/år"] = py(st["total_return_pct"], a, b)
            row[f"{w} nedgång %"] = st["max_drawdown_pct"]
        out.append(row)
        log(row["Metod"])

rot = []
for k in (5, 10, 20):
    for lb in (126, 252):
        for d in ("spy", "cash"):
            r = run_rotation(bars, spy, members, cap, *windows["Träning"], k=k, lookback=lb, defensive=d)
            rot.append({"k": k, "lb": lb, "d": d, "py": r["per_year_pct"], "dd": r["max_drawdown_pct"],
                        "score": r["per_year_pct"] / max(abs(r["max_drawdown_pct"]), 5)})
rot = pd.DataFrame(rot).sort_values("score", ascending=False)
print("Månadsrotation – alla inställningar på TRÄNINGSÅREN:")
print(rot.round(2).to_string(index=False))
for _, b in rot.head(3).iterrows():
    row = {"Metod": f"Månadsrotation: {int(b.k)} starkaste, {int(b.lb)} dagar, svag börs → "
                    f"{'S&P 500' if b.d == 'spy' else 'kontanter'}"}
    for w, (a, e) in windows.items():
        r = run_rotation(bars, spy, members, cap, a, e, k=int(b.k), lookback=int(b.lb), defensive=b.d)
        row[f"{w} %/år"] = r["per_year_pct"]
        row[f"{w} nedgång %"] = r["max_drawdown_pct"]
    out.append(row)
out.append({"Metod": "S&P 500 (indexfond)", "Träning %/år": spy_w["Träning"]["per_year_pct"],
            "Träning nedgång %": spy_w["Träning"]["max_drawdown_pct"],
            "Kontroll %/år": spy_w["Kontroll"]["per_year_pct"], "Kontroll nedgång %": spy_w["Kontroll"]["max_drawdown_pct"]})
res = pd.DataFrame(out)
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 90)
print("\nRESULTAT (utan facit):")
print(res.to_string(index=False))
sp = spy_w["Kontroll"]["per_year_pct"]
print(f"\nSlår S&P 500 på både träning och kontroll: "
      + ", ".join(r["Metod"] for r in out if r["Metod"] != "S&P 500 (indexfond)"
                  and r["Träning %/år"] > spy_w["Träning"]["per_year_pct"] and r["Kontroll %/år"] > sp) or "ingen")
