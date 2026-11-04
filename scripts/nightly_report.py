"""Nattlig rapport med RIKTIGA kurser. Körs automatiskt av GitHub Actions
efter att den amerikanska börsen stängt (se .github/workflows/nightly.yml).

Rapporten sparas i reports/ och läses av den schemalagda morgonsessionen,
som använder den för att förbättra roboten.

Kör lokalt:  python scripts/nightly_report.py
"""
import csv
import json
import sys
import time
import traceback

import pandas as pd
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading_platform.backtest import find_best, per_year, period_return, run_backtest  # noqa: E402
from trading_platform.config import load_settings  # noqa: E402
from trading_platform.daytop import liquid_universe  # noqa: E402
from trading_platform.data import YahooResearchProvider  # noqa: E402
from trading_platform.risk import sek_to_usd  # noqa: E402
from trading_platform.strategies.day_variants import day_variants  # noqa: E402
from trading_platform.strategies.variants import add_market, daily_variants, data_for, hourly_variants  # noqa: E402

REPORTS = ROOT / "reports"
DAY_SHARE = 0.20          # daytrading testas som experiment med 20 % av kapitalet
DAY_UNIVERSE = 50         # de 50 mest omsatta aktierna för daytrading
SWING_LIQUID = 100        # de 100 mest omsatta aktierna för swing


def md_table(df, cols):
    cols = [c for c in cols if c in df]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df[cols].iterrows():
        out.append("| " + " | ".join("" if v is None or v != v else str(v) for v in r.values) + " |")
    return "\n".join(out)


def main():
    started = datetime.now(timezone.utc)
    s = load_settings()
    p = YahooResearchProvider()
    fx = p.usd_sek() or s.usd_sek_fallback
    swing_cap = sek_to_usd(s.swing_capital or s.total_capital, fx)
    day_cap = sek_to_usd(s.total_capital * DAY_SHARE, fx)
    lines = [f"# Nattrapport {started:%Y-%m-%d}",
             "",
             f"Skapad {started:%Y-%m-%d %H:%M} UTC med riktiga kurser från Yahoo. "
             f"Inställningar: kapital {s.total_capital:,.0f} SEK, risk {s.risk_per_trade_pct} % per affär, "
             f"max {s.max_positions_per_strategy} positioner, max {s.max_position_pct_of_strategy_capital:.0f} % "
             f"per position, parkering i SPY: {'på' if s.park_idle_in_index else 'av'}. USD/SEK {fx:.2f}.",
             ""]
    history_rows = []

    liquid = []
    try:
        saved = json.loads((ROOT / "paper" / "liquid_ranking.json").read_text())
        if time.time() - saved.get("time", 0) < 24 * 3600 and len(saved.get("symbols", [])) >= 550:
            liquid = saved.get("symbols", [])
    except Exception:
        pass
    if not liquid:
        try:
            liquid = liquid_universe(p, 600, allow_heavy=False)
        except Exception as e:
            lines += [f"> Kunde inte hämta de mest omsatta aktierna: {e}", ""]

    # ---------------- swing
    spy5 = p.history("SPY", "5y", "1d")  # (har inbyggt omförsök vid spärr)
    lists = {"Bevakningslistan": list(s.watchlist)}
    if liquid:
        lists[f"{SWING_LIQUID} mest omsatta"] = liquid[:SWING_LIQUID]
    lines += ["## Swingtrading (5 år, dagsdata)", ""]
    for list_name, syms in lists.items():
        try:
            bars = {k: add_market(v, spy5) for k, v in p.history_many(syms, "5y", "1d").items() if v is not None}
            df = find_best(bars, daily_variants(), swing_cap, s, benchmark=spy5.Close,
                           risk_levels=(s.risk_per_trade_pct,))
        except Exception as e:
            lines += [f"### {list_name}", "", f"> Fel: {type(e).__name__}: {e}", ""]
            continue
        lines += [f"### {list_name} ({len(bars)} aktier, testperiod {df.attrs.get('period', '')})", "",
                  md_table(df, ["Strategi", "Per år %", "Avkastning %", "S&P 500 %", "Slår S&P 500?",
                                "Största nedgång %", "Affärer", "Vinstandel %", "Testdel %", "Kontrolldel %",
                                "Godkänd", "Poäng"]), ""]
        for _, r in df.iterrows():
            history_rows.append([f"{started:%Y-%m-%d}", "swing", list_name, r["Strategi"], r["Per år %"],
                                 r["Avkastning %"], r["S&P 500 %"], r["Största nedgång %"], r["Affärer"],
                                 r["Godkänd"]])

    # ---------------- kort swing (timgraf, ca 2 år)
    lines += ["## Kort swing (timgraf, ca 2 år)", "",
              "Samma regler som swing men på timstaplar: trend senaste månaden, rekyl senaste veckan, "
              "RSI/MACD vänder upp på timgrafen.", ""]
    spy_h = None
    for list_name, syms in lists.items():
        try:
            per, iv = data_for(next(iter(hourly_variants().values())), backtest=True)
            hb = {k: v for k, v in p.history_many(syms, per, iv).items() if v is not None}
            if spy_h is None:
                spy_h = p.history("SPY", per, iv)
            df = find_best(hb, hourly_variants(), swing_cap, s,
                           benchmark=None if spy_h is None else spy_h.Close, risk_levels=(s.risk_per_trade_pct,))
        except Exception as e:
            lines += [f"### {list_name}", "", f"> Fel: {type(e).__name__}: {e}", ""]
            continue
        lines += [f"### {list_name} ({len(hb)} aktier, testperiod {df.attrs.get('period', '')})", "",
                  md_table(df, ["Strategi", "Per år %", "Avkastning %", "S&P 500 %", "Slår S&P 500?",
                                "Största nedgång %", "Affärer", "Vinstandel %", "Testdel %", "Kontrolldel %",
                                "Godkänd", "Poäng"]), ""]
        for _, r in df.iterrows():
            history_rows.append([f"{started:%Y-%m-%d}", "swing-kort", list_name, r["Strategi"], r["Per år %"],
                                 r["Avkastning %"], r["S&P 500 %"], r["Största nedgång %"], r["Affärer"],
                                 r["Godkänd"]])

    # ---------------- daytrading
    lines += ["## Daytrading (senaste ca 60 dagarna, 5-minutersdata)", "",
              f"Testas som experiment med {DAY_SHARE:.0%} av kapitalet på de {DAY_UNIVERSE} mest omsatta aktierna. "
              "Courtage och slippage ingår. Inga positioner över natten.", ""]
    try:
        dbars = ({k: v for k, v in p.history_many(liquid[:DAY_UNIVERSE], "60d", "5m").items() if v is not None}
                 if liquid else {})
    except Exception as e:
        dbars = {}
        lines += [f"> Fel vid hämtning av 5-minutersdata: {type(e).__name__}: {e}", ""]
    if dbars:
        s_day = replace(s, max_positions_per_strategy=10, max_position_pct_of_strategy_capital=10.0,
                        park_idle_in_index=False)
        try:
            spy_d = p.history("SPY", "60d", "5m")
        except Exception:
            spy_d = None
        rows = []
        for name, strat in day_variants().items():
            st = run_backtest(dbars, strat, day_cap, s_day)
            stats = st["stats"]
            eq = st["equity"]
            bench = period_return(spy_d.Close, eq.index[0]) if spy_d is not None and len(eq) else None
            rows.append({"Strategi": name, "Avkastning %": stats["total_return_pct"], "S&P 500 %": bench,
                         "Affärer": stats["trades"], "Vinstandel %": stats["win_rate_pct"],
                         "Snitt R": stats["avg_r"], "Profit factor": stats["profit_factor"],
                         "Största nedgång %": stats["max_drawdown_pct"], "Courtage USD": stats["fees_usd"]})
            history_rows.append([f"{started:%Y-%m-%d}", "day", f"{DAY_UNIVERSE} mest omsatta", name, None,
                                 stats["total_return_pct"], bench, stats["max_drawdown_pct"], stats["trades"], None])
        import pandas as pd
        lines += [md_table(pd.DataFrame(rows), ["Strategi", "Avkastning %", "S&P 500 %", "Affärer", "Vinstandel %",
                                                "Snitt R", "Profit factor", "Största nedgång %", "Courtage USD"]), ""]
    else:
        lines += ["Hoppades över (ingen data).", ""]

    # ---------------- automatiskt val för roboten – ÄRLIGT test (utan facit i efterhand)
    lines += ["## Robotens automatiska val (ärligt test, utan facit)", "",
              "Listan med mest omsatta aktier väljs om **varje månad** utifrån handeln just då – inte dagens "
              "lista. Steg 1: alla swing-strategier × risk 1/2 % × ledig kassa i S&P 500 (ja/nej) × 300/600 aktier. "
              "Steg 2: de 5 bästa + ditt förval testas på 10 slumpade delar av aktierna. Bäst poäng (avkastning "
              "per år / största nedgång) bland dem som gick plus på minst 8 av 10 listor vinner.", ""]
    try:
        import re
        from trading_platform import honest
        from trading_platform.universe import load_us_equity_symbols
        rows_u = load_us_equity_symbols()
        bad = re.compile(r"warrant|unit|right|preferred|notes|debenture|%", re.I)
        all_syms = [x for x, nm in zip(rows_u["symbol"], rows_u["name"].astype(str))
                    if "." not in x and "-" not in x and not bad.search(nm)]
        spy6 = p.history("SPY", "6y", "1d")
        raw = {k: v for k, v in p.history_many(all_syms, "6y", "1d", chunk=200).items()
               if v is not None and len(v) > 80}
        prep = honest.prepare(raw, (300, 600))
        prep = {n: ({k: add_market(v, spy6) for k, v in bars.items()}, mem) for n, (bars, mem) in prep.items()}
        td = json.loads((ROOT / "config.json").read_text()).get("test_defaults", {})
        s1, s2, best, mine = honest.choose(prep, daily_variants(), swing_cap, s, yours=td)
        bars300, mem300 = prep[min(prep)]
        start, years = honest.start_time(bars300, mem300)
        sc = spy6["Close"][spy6.index >= start]
        spy_py = round(((sc.iloc[-1] / sc.iloc[0]) ** (1 / years) - 1) * 100, 1)
        REPORTS.mkdir(exist_ok=True)
        s1.head(30).assign(Testat=f"{started:%Y-%m-%d %H:%M} UTC").to_csv(REPORTS / "best_table.csv", index=False)
        lines += [f"{len(raw)} aktier hämtade. S&P 500 samma period: **{spy_py} % per år**.", "",
                  "### Steg 1 (topp 10)", "", md_table(s1.head(10), list(s1.columns)), "",
                  "### Steg 2: stabilitetstest (10 listor per rad)", "",
                  md_table(s2, ["Aktier", "Strategi", "Risk %", "Kassa i S&P", "per_year_pct", "worst_per_year_pct",
                                "max_drawdown_pct", "plus_lists", "score"]), ""]

        def pack(r):
            return {"swing_variant": r["Strategi"], "risk_per_trade_pct": float(r["Risk %"]),
                    "park_idle_in_index": bool(r["Kassa i S&P"]), "swing_liquid_n": int(r["Aktier"]),
                    "max_position_pct_of_strategy_capital": float(s.max_position_pct_of_strategy_capital),
                    "per_year_pct": float(r["per_year_pct"]), "worst_per_year_pct": float(r["worst_per_year_pct"]),
                    "max_drawdown_pct": float(r["max_drawdown_pct"]), "score": float(r["score"])}
        if mine is not None:
            lines += [f"**Ditt förval** ({mine['Strategi']}, risk {mine['Risk %']} %): {mine['per_year_pct']} % per år, "
                      f"största nedgång {mine['max_drawdown_pct']} %, poäng {mine['score']}.", ""]
        if best is not None:
            choice = {"date": f"{started:%Y-%m-%d}", **pack(best), "lists": int(best["lists"]),
                      "honest": True, "sp500_per_year_pct": spy_py,
                      "beats_index": bool(best["per_year_pct"] > spy_py),
                      "is_yours": bool(mine is not None and mine.equals(best))}
            if mine is not None:
                choice["yours"] = pack(mine)
            (REPORTS / "best_choice.json").write_text(json.dumps(choice, indent=2, ensure_ascii=False))
            lines += [f"**Valt:** {best['Strategi']}, risk {best['Risk %']} %, ledig kassa i S&P: "
                      f"{'ja' if best['Kassa i S&P'] else 'nej'}, de {int(best['Aktier'])} mest omsatta – "
                      f"{best['per_year_pct']} % per år (S&P 500: {spy_py} %).", ""]
        else:
            lines += ["Ingen kombination var stabil nog (plus på minst 8 av 10 listor) – roboten behåller sitt "
                      "tidigare val.", ""]
    except Exception as e:
        lines += [f"> Fel i automatiska valet: {type(e).__name__}: {e}", ""]

    lines += ["---", "Rapporten är underlag för morgonsessionen. Historiska resultat är ingen garanti för framtiden."]
    REPORTS.mkdir(exist_ok=True)
    text = "\n".join(lines) + "\n"
    (REPORTS / f"{started:%Y-%m-%d}.md").write_text(text)
    (REPORTS / "latest.md").write_text(text)
    hist = REPORTS / "history.csv"
    new = not hist.exists()
    with hist.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["datum", "typ", "lista", "strategi", "per_ar_pct", "avkastning_pct", "sp500_pct",
                        "storsta_nedgang_pct", "affarer", "godkand"])
        w.writerows(history_rows)
    (REPORTS / "latest_error.txt").unlink(missing_ok=True)  # gammalt fel är inte längre aktuellt
    print(text)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        REPORTS.mkdir(exist_ok=True)
        (REPORTS / "latest_error.txt").write_text(traceback.format_exc())
        sys.exit(1)
