"""Test UTAN facit i efterhand: roboten får varje månad bara välja bland de 300
aktier som var mest omsatta JUST DÅ (inte i dag). Jämförs med det vanliga testet
(dagens 300 mest omsatta) och med S&P 500.

Kvarvarande brist: bolag som har avnoterats (gått i konkurs, köpts upp) finns inte
hos Yahoo och saknas därför helt. Det gör resultatet fortfarande något för bra."""
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
from trading_platform.pit import make_allowed, pit_members, today_members  # noqa: E402
from trading_platform.risk import sek_to_usd  # noqa: E402
from trading_platform.strategies.variants import add_market, swing_variants  # noqa: E402
from trading_platform.universe import load_us_equity_symbols  # noqa: E402

T0 = time.time()


def log(m):
    print(f"[{time.time() - T0:6.0f}s] {m}", flush=True)


s0 = load_settings()
import json  # noqa: E402
td = json.loads((ROOT / "config.json").read_text()).get("test_defaults", {})
VAR = td.get("swing_variant", "Swing – 5 av 6 (RSI, MACD och botten krävs)")
s = replace(s0, risk_per_trade_pct=float(td.get("risk_per_trade_pct", 2.0)),
            max_position_pct_of_strategy_capital=float(td.get("max_position_pct_of_strategy_capital", 35)))
p = YahooResearchProvider()
fx = p.usd_sek() or s0.usd_sek_fallback
cap = sek_to_usd(100_000, fx)

rows = load_us_equity_symbols()
bad = re.compile(r"warrant|unit|right|preferred|notes|debenture|%", re.I)
syms = [x for x, n in zip(rows["symbol"], rows["name"].astype(str)) if "." not in x and "-" not in x
        and not bad.search(n)]
log(f"{len(syms)} amerikanska aktier att hämta (6 år).")
spy = p.history("SPY", "6y", "1d")
raw = p.history_many(syms, "6y", "1d", chunk=200)
raw = {k: v for k, v in raw.items() if v is not None and len(v) > 80}
log(f"Fick kurser för {len(raw)} aktier.")

members = pit_members(raw, n=300)
now_set = today_members(raw, n=300)
ever = set().union(*[m for _, m in members]) | now_set
log(f"{len(members)} månader. {len(ever)} olika aktier har någon gång varit bland de 300 mest omsatta.")
bars = {k: add_market(raw[k], spy) for k in ever}
times = sorted(set().union(*[b.index for b in bars.values()]))
q0 = pd.Timestamp(members[0][0])
if times[0].tzinfo is not None:
    q0 = q0.tz_localize(times[0].tzinfo)
start = max(times[220], q0)
V = swing_variants()[VAR]

pit = run_backtest(bars, V, cap, s, start=start, allowed=make_allowed(members))
hind = run_backtest({k: bars[k] for k in now_set}, V, cap, s, start=start)
years = (times[-1] - start).days / 365.25


def per_year(st):
    return ((1 + st["total_return_pct"] / 100) ** (1 / years) - 1) * 100


spy_c = spy["Close"][spy.index >= start]
spy_tot = (spy_c.iloc[-1] / spy_c.iloc[0] - 1) * 100
spy_dd = ((spy_c / spy_c.cummax()) - 1).min() * 100
print(f"\nInställning: {VAR}, risk {s.risk_per_trade_pct} %, max {s.max_position_pct_of_strategy_capital:.0f} %")
print(f"Period: {pd.Timestamp(start).date()} – {pd.Timestamp(times[-1]).date()} ({years:.1f} år)\n")
tab = pd.DataFrame([
    {"Test": "UTAN facit (lista vald varje månad)", "Totalt %": pit["stats"]["total_return_pct"],
     "Per år %": round(per_year(pit["stats"]), 1), "Största nedgång %": pit["stats"]["max_drawdown_pct"],
     "Affärer": pit["stats"]["trades"]},
    {"Test": "MED facit (dagens 300, som vanligt)", "Totalt %": hind["stats"]["total_return_pct"],
     "Per år %": round(per_year(hind["stats"]), 1), "Största nedgång %": hind["stats"]["max_drawdown_pct"],
     "Affärer": hind["stats"]["trades"]},
    {"Test": "S&P 500 (köp och behåll)", "Totalt %": round(spy_tot, 1),
     "Per år %": round(((1 + spy_tot / 100) ** (1 / years) - 1) * 100, 1), "Största nedgång %": round(spy_dd, 1),
     "Affärer": 1},
])
pd.set_option("display.width", 200)
print(tab.to_string(index=False))


def yearly(eq):
    e = eq.copy()
    e.index = pd.DatetimeIndex(e.index)
    y = e.groupby(e.index.year).last()
    first = e.iloc[0]
    return (y / y.shift(1).fillna(first) - 1) * 100


ys = pd.DataFrame({"Utan facit %": yearly(pit["equity"]), "Med facit %": yearly(hind["equity"]),
                   "S&P 500 %": yearly(spy_c)}).round(1)
print("\nÅr för år:")
print(ys.to_string())
print("\nObs: avnoterade bolag saknas hos Yahoo, så även testet utan facit är något för bra.")

if "--alla" in sys.argv:
    from trading_platform.strategies.variants import daily_variants
    print("\nALLA strategier, UTAN facit (lista vald varje månad):")
    allowed = make_allowed(members)
    out = []
    for name, mod in daily_variants().items():
        for risk in (1.0, 2.0):
            for park in (False, True):
                ss = replace(s, risk_per_trade_pct=risk, park_idle_in_index=park)
                st = run_backtest(bars, mod, cap, ss, start=start, allowed=allowed)["stats"]
                out.append({"Strategi": name, "Risk %": risk, "Ledig kassa i S&P": "ja" if park else "nej",
                            "Per år %": round(per_year(st), 1), "Största nedgång %": st["max_drawdown_pct"],
                            "Affärer": st["trades"]})
                log(f"{name} risk {risk} park {park}: {out[-1]['Per år %']} %/år")
    res = pd.DataFrame(out).sort_values("Per år %", ascending=False)
    print(res.to_string(index=False))
    spy_py = ((1 + spy_tot / 100) ** (1 / years) - 1) * 100
    print(f"\nS&P 500: {spy_py:.1f} %/år. Strategier som slår S&P 500: {int((res['Per år %'] > spy_py).sum())} av {len(res)}")
