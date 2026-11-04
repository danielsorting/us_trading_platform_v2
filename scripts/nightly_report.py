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

    # ---------------- automatiskt val för roboten (strategi + risk + max storlek + antal aktier)
    lines += ["## Robotens automatiska val", "",
              "Steg 1: alla swing-strategier × risk 0,5/1/2 % på de 300 och 600 mest omsatta (5 år). "
              "Steg 2 (stabilitetstest): de 5 bästa + ditt förval körs på **10 slumpade aktielistor** med max "
              "35 % och 70 % per affär. Bäst snittpoäng (avkastning per år / största nedgång) vinner – så att "
              "valet inte bygger på tur med en enda lista.", ""]
    try:
        from trading_platform.robust import robust_score, with_setup  # noqa: E402
        cand, bars_n = [], {}
        for n in (300, 600):
            syms = liquid[:n]
            if len(syms) < n * 0.9:
                continue  # Yahoo gav inte så många
            bars = {k: add_market(v, spy5) for k, v in p.history_many(syms, "5y", "1d").items() if v is not None}
            bars_n[n] = bars
            df = find_best(bars, daily_variants(), swing_cap, s, benchmark=spy5.Close, risk_levels=(0.5, 1.0, 2.0))
            df.insert(0, "Aktier", f"{n} mest omsatta")
            df["n"] = n
            cand.append(df)
        allc = pd.concat(cand, ignore_index=True)
        allc["_ok"] = allc["Godkänd"] == "ja"
        allc = allc.sort_values(["_ok", "Poäng"], ascending=[False, False]).drop(columns="_ok")
        REPORTS.mkdir(exist_ok=True)
        allc.drop(columns=["n"], errors="ignore").head(30).assign(Testat=f"{started:%Y-%m-%d %H:%M} UTC").to_csv(
            REPORTS / "best_table.csv", index=False)
        # Steg 2: stabilitetstest
        tops = [(r["Strategi"], float(r["Risk per affär %"]), int(r["n"]))
                for _, r in allc[allc["Godkänd"] == "ja"].head(5).iterrows()]
        try:
            td = json.loads((ROOT / "config.json").read_text()).get("test_defaults", {})
            if td.get("swing_variant") in daily_variants():
                for n in bars_n:
                    tops.append((td["swing_variant"], float(td.get("risk_per_trade_pct", 2.0)), n))
        except Exception:
            pass
        tops = list(dict.fromkeys(tops))
        rob = []
        for strat, risk, n in tops:
            for mx in (35, 70):
                r = robust_score(bars_n[n], daily_variants()[strat], swing_cap, with_setup(s, risk, mx))
                if r:
                    rob.append({"Aktier": n, "Strategi": strat, "Risk %": risk, "Max/affär %": mx, **r})
        rob_df = pd.DataFrame(rob).sort_values("score", ascending=False)
        lines += ["### Stabilitetstest (10 aktielistor per rad)", "",
                  md_table(rob_df.head(10), ["Aktier", "Strategi", "Risk %", "Max/affär %", "per_year_pct",
                                             "worst_per_year_pct", "best_per_year_pct", "max_drawdown_pct",
                                             "plus_lists", "score"]), ""]
        best = rob_df.iloc[0] if len(rob_df) else None
        # Ditt förval (config.json → test_defaults) jämförs alltid med det som väljs.
        mine = None
        try:
            td = json.loads((ROOT / "config.json").read_text()).get("test_defaults", {})
            m = rob_df[(rob_df["Strategi"] == td.get("swing_variant"))
                       & (rob_df["Risk %"] == float(td.get("risk_per_trade_pct", 2.0)))
                       & (rob_df["Max/affär %"] == float(td.get("max_position_pct_of_strategy_capital", 35)))]
            if len(m):
                mine = m.sort_values("score", ascending=False).iloc[0]
                lines += [f"**Ditt förval** ({mine['Strategi']}, risk {mine['Risk %']} %, max {mine['Max/affär %']} %, "
                          f"{int(mine['Aktier'])} aktier): snitt {mine['per_year_pct']} % per år, sämsta lista "
                          f"{mine['worst_per_year_pct']} %, största nedgång {mine['max_drawdown_pct']} %, poäng "
                          f"{mine['score']}.", ""]
        except Exception:
            pass
        if best is not None and best["plus_lists"] >= 8 and best["per_year_pct"] > 0:
            choice = {"date": f"{started:%Y-%m-%d}", "swing_variant": best["Strategi"],
                      "risk_per_trade_pct": float(best["Risk %"]), "swing_liquid_n": int(best["Aktier"]),
                      "max_position_pct_of_strategy_capital": float(best["Max/affär %"]),
                      "per_year_pct": float(best["per_year_pct"]), "max_drawdown_pct": float(best["max_drawdown_pct"]),
                      "worst_per_year_pct": float(best["worst_per_year_pct"]), "lists": int(best["lists"]),
                      "score": float(best["score"]), "is_yours": bool(mine is not None and mine.equals(best))}
            if mine is not None:
                choice["yours"] = {"swing_variant": mine["Strategi"], "risk_per_trade_pct": float(mine["Risk %"]),
                                   "max_position_pct_of_strategy_capital": float(mine["Max/affär %"]),
                                   "swing_liquid_n": int(mine["Aktier"]), "per_year_pct": float(mine["per_year_pct"]),
                                   "worst_per_year_pct": float(mine["worst_per_year_pct"]),
                                   "max_drawdown_pct": float(mine["max_drawdown_pct"]), "score": float(mine["score"])}
            (REPORTS / "best_choice.json").write_text(json.dumps(choice, indent=2, ensure_ascii=False))
            lines += [f"**Valt:** {best['Strategi']}, risk {best['Risk %']} %, max {best['Max/affär %']} % per affär, "
                      f"de {int(best['Aktier'])} mest omsatta – snitt {best['per_year_pct']} % per år på 10 listor "
                      f"(sämsta {best['worst_per_year_pct']} %), största nedgång {best['max_drawdown_pct']} %.", ""]
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
