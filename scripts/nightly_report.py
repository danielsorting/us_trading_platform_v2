"""Nattlig rapport med RIKTIGA kurser. Körs automatiskt av GitHub Actions
efter att den amerikanska börsen stängt (se .github/workflows/nightly.yml).

Rapporten sparas i reports/ och läses av den schemalagda morgonsessionen,
som använder den för att förbättra roboten.

Kör lokalt:  python scripts/nightly_report.py
"""
import csv
import sys
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading_platform.backtest import find_best, per_year, period_return, run_backtest  # noqa: E402
from trading_platform.config import load_settings  # noqa: E402
from trading_platform.daytop import most_liquid, yahoo_symbol  # noqa: E402
from trading_platform.data import YahooResearchProvider  # noqa: E402
from trading_platform.risk import sek_to_usd  # noqa: E402
from trading_platform.strategies.day_variants import day_variants  # noqa: E402
from trading_platform.strategies.variants import add_market, swing_variants  # noqa: E402
from trading_platform.universe import get_symbols  # noqa: E402

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
        uni = get_symbols("us_market", [], False)
        syms = [yahoo_symbol(x) for x in uni["symbol"].tolist() if "$" not in x]
        liquid = most_liquid(p.history_many(syms, "1mo", "1d"), max(SWING_LIQUID, DAY_UNIVERSE))
    except Exception as e:
        lines += [f"> Kunde inte hämta de mest omsatta aktierna: {e}", ""]

    # ---------------- swing
    spy5 = p.history("SPY", "5y", "1d")
    lists = {"Bevakningslistan": list(s.watchlist)}
    if liquid:
        lists[f"{SWING_LIQUID} mest omsatta"] = liquid[:SWING_LIQUID]
    lines += ["## Swingtrading (5 år, dagsdata)", ""]
    for list_name, syms in lists.items():
        bars = {k: add_market(v, spy5) for k, v in p.history_many(syms, "5y", "1d").items() if v is not None}
        df = find_best(bars, swing_variants(), swing_cap, s, benchmark=spy5.Close,
                       risk_levels=(s.risk_per_trade_pct,))
        lines += [f"### {list_name} ({len(bars)} aktier, testperiod {df.attrs.get('period', '')})", "",
                  md_table(df, ["Strategi", "Per år %", "Avkastning %", "S&P 500 %", "Slår S&P 500?",
                                "Största nedgång %", "Affärer", "Vinstandel %", "Testdel %", "Kontrolldel %",
                                "Godkänd", "Poäng"]), ""]
        for _, r in df.iterrows():
            history_rows.append([f"{started:%Y-%m-%d}", "swing", list_name, r["Strategi"], r["Per år %"],
                                 r["Avkastning %"], r["S&P 500 %"], r["Största nedgång %"], r["Affärer"],
                                 r["Godkänd"]])

    # ---------------- daytrading
    lines += ["## Daytrading (senaste ca 60 dagarna, 5-minutersdata)", "",
              f"Testas som experiment med {DAY_SHARE:.0%} av kapitalet på de {DAY_UNIVERSE} mest omsatta aktierna. "
              "Courtage och slippage ingår. Inga positioner över natten.", ""]
    if liquid:
        dbars = {k: v for k, v in p.history_many(liquid[:DAY_UNIVERSE], "60d", "5m").items() if v is not None}
        s_day = replace(s, max_positions_per_strategy=10, max_position_pct_of_strategy_capital=10.0,
                        park_idle_in_index=False)
        spy_d = p.history("SPY", "60d", "5m")
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
        lines += ["Hoppades över (ingen lista över mest omsatta aktier).", ""]

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
    print(text)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        REPORTS.mkdir(exist_ok=True)
        (REPORTS / "latest_error.txt").write_text(traceback.format_exc())
        sys.exit(1)
