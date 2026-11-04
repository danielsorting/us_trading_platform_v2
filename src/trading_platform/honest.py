"""Nattens ÄRLIGA val av strategi (utan facit i efterhand).

Steg 1: varje swing-strategi × risk × ledig kassa i S&P × listans storlek (300/600)
        testas där listan väljs om varje månad utifrån handeln JUST DÅ.
Steg 2: de 5 bästa + Daniels förval testas på 10 slumpade delar av aktierna.
Valet: bäst poäng (avkastning per år / största nedgång) bland dem som gick plus på
minst 8 av 10 listor. Om inget klarar det behåller roboten sitt tidigare val.
"""
import random
from dataclasses import replace

import pandas as pd

from .backtest import run_backtest
from .pit import make_allowed, pit_members

WARMUP = 220


def _per_year(total_pct, years):
    return ((1 + total_pct / 100) ** (1 / max(years, 0.1)) - 1) * 100


def _setup(settings, risk, park):
    return replace(settings, risk_per_trade_pct=float(risk), park_idle_in_index=bool(park))


def prepare(raw, n_values=(300, 600)):
    """Returnerar {n: (bars_för_alla_som_någon_gång_var_med, members)} och starttid."""
    out = {}
    for n in n_values:
        members = pit_members(raw, n=n)
        if not members:
            continue
        ever = set().union(*[m for _, m in members])
        out[n] = ({k: raw[k] for k in ever if k in raw}, members)
    return out


def start_time(bars, members):
    times = sorted(set().union(*[b.index for b in bars.values()]))
    q0 = pd.Timestamp(members[0][0])
    if times[0].tzinfo is not None:
        q0 = q0.tz_localize(times[0].tzinfo)
    start = max(times[min(WARMUP, len(times) - 1)], q0)
    return start, (times[-1] - start).days / 365.25


def stage1(prepared, variants, cap, settings, risks=(1.0, 2.0), parks=(False, True)):
    rows = []
    for n, (bars, members) in prepared.items():
        allowed = make_allowed(members)
        start, years = start_time(bars, members)
        for name, mod in variants.items():
            for risk in risks:
                for park in parks:
                    st = run_backtest(bars, mod, cap, _setup(settings, risk, park), start=start,
                                      allowed=allowed)["stats"]
                    dd = abs(st["max_drawdown_pct"])
                    py = _per_year(st["total_return_pct"], years)
                    rows.append({"Aktier": n, "Strategi": name, "Risk %": risk, "Kassa i S&P": park,
                                 "Per år %": round(py, 1), "Största nedgång %": st["max_drawdown_pct"],
                                 "Affärer": st["trades"], "Poäng": round(py / max(dd, 5.0), 2)})
    return pd.DataFrame(rows).sort_values("Poäng", ascending=False).reset_index(drop=True)


def robust(bars, members, strategy, cap, settings, lists=10, sample_frac=0.83, seed=0):
    allowed_q = make_allowed(members)
    start, years = start_time(bars, members)
    pool = sorted(bars)
    per_year, dds = [], []
    for i in range(lists):
        pick = set(random.Random(seed + i).sample(pool, max(20, int(len(pool) * sample_frac))))
        st = run_backtest({k: bars[k] for k in pick}, strategy, cap, settings, start=start,
                          allowed=lambda s, t: allowed_q(s, t))["stats"]
        per_year.append(_per_year(st["total_return_pct"], years))
        dds.append(abs(st["max_drawdown_pct"]))
    mean_py, mean_dd = sum(per_year) / lists, sum(dds) / lists
    return {"per_year_pct": round(mean_py, 1), "worst_per_year_pct": round(min(per_year), 1),
            "best_per_year_pct": round(max(per_year), 1), "max_drawdown_pct": round(-mean_dd, 1),
            "plus_lists": sum(1 for x in per_year if x > 0), "lists": lists,
            "score": round(mean_py / max(mean_dd, 5.0), 2)}


def choose(prepared, variants, cap, settings, yours=None, top=5, lists=10, **kw):
    """Returnerar (steg1_tabell, steg2_tabell, val eller None, ditt_resultat eller None)."""
    s1 = stage1(prepared, variants, cap, settings, **kw)
    cands = [(r["Strategi"], float(r["Risk %"]), bool(r["Kassa i S&P"]), int(r["Aktier"]))
             for _, r in s1.head(top).iterrows()]
    mine_key = None
    if yours and yours.get("swing_variant") in variants:
        for n in prepared:
            k = (yours["swing_variant"], float(yours.get("risk_per_trade_pct", 2.0)),
                 bool(yours.get("park_idle_in_index", False)), n)
            cands.append(k)
        mine_key = cands[-len(prepared):]
    rob = []
    for strat, risk, park, n in dict.fromkeys(cands):
        bars, members = prepared[n]
        r = robust(bars, members, variants[strat], cap, _setup(settings, risk, park), lists=lists)
        rob.append({"Aktier": n, "Strategi": strat, "Risk %": risk, "Kassa i S&P": park, **r})
    s2 = pd.DataFrame(rob).sort_values("score", ascending=False).reset_index(drop=True)
    mine = None
    if mine_key:
        m = s2[[(r["Strategi"], r["Risk %"], r["Kassa i S&P"], r["Aktier"]) in mine_key for _, r in s2.iterrows()]]
        mine = m.iloc[0] if len(m) else None
    ok = s2[(s2["plus_lists"] >= 8) & (s2["per_year_pct"] > 0)]
    best = ok.iloc[0] if len(ok) else None
    return s1, s2, best, mine
