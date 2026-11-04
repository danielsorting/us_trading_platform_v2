"""Stabilitetstest: samma inställning på flera slumpade aktielistor.

Ett enda backtest kan ha tur med just de aktier som var med. Här körs varje
inställning på `lists` olika slumpade urval (t.ex. 250 av 300 aktier) och
snittet räknas. En inställning som är bra på de flesta listor är att lita på."""
import random
from dataclasses import replace

from .backtest import run_backtest


def robust_score(bars_by_symbol, strategy, capital_usd, settings, lists=10, sample_frac=0.83, warmup=220, seed=0):
    """Returnerar dict med snitt per år, sämsta/bästa per år, snitt största nedgång och poäng."""
    pool = sorted(k for k, v in bars_by_symbol.items() if v is not None and len(v) > warmup)
    if len(pool) < 20:
        return None
    per_year, dds = [], []
    for i in range(lists):
        rnd = random.Random(seed + i)
        pick = rnd.sample(pool, max(20, int(len(pool) * sample_frac)))
        bars = {k: bars_by_symbol[k] for k in pick}
        times = sorted(set().union(*[b.index for b in bars.values()]))
        first = times[min(warmup, len(times) - 1)]
        st = run_backtest(bars, strategy, capital_usd, settings, start=first)["stats"]
        years = max((times[-1] - first).days / 365.25, 0.1)
        per_year.append(((1 + st["total_return_pct"] / 100) ** (1 / years) - 1) * 100)
        dds.append(abs(st["max_drawdown_pct"]))
    mean_py = sum(per_year) / len(per_year)
    mean_dd = sum(dds) / len(dds)
    return {"per_year_pct": round(mean_py, 1), "worst_per_year_pct": round(min(per_year), 1),
            "best_per_year_pct": round(max(per_year), 1), "max_drawdown_pct": round(-mean_dd, 1),
            "plus_lists": sum(1 for x in per_year if x > 0), "lists": lists,
            "score": round(mean_py / max(mean_dd, 5.0), 2)}


def with_setup(settings, risk, max_pct):
    return replace(settings, risk_per_trade_pct=float(risk), max_position_pct_of_strategy_capital=float(max_pct))
