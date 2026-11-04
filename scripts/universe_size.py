"""Hur många aktier ska roboten leta bland? Testar 100, 300, 600 och 1000 mest omsatta
(5 år, dagsdata) med robotens swing-strategi. Actions → Testa antal aktier → Run workflow."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from trading_platform.backtest import compare_strategies  # noqa: E402
from trading_platform.config import load_settings  # noqa: E402
from trading_platform.data import YahooResearchProvider  # noqa: E402
from trading_platform.risk import sek_to_usd  # noqa: E402
from trading_platform.strategies.variants import add_market, swing_variants  # noqa: E402


def main():
    s = load_settings()
    p = YahooResearchProvider()
    fx = p.usd_sek() or s.usd_sek_fallback
    cap = sek_to_usd(s.swing_capital, fx)
    spy = p.history("SPY", "5y", "1d")
    syms = p.most_active(1000)
    print(f"Yahoo gav {len(syms)} mest omsatta aktier.")
    all_bars = {k: add_market(v, spy) for k, v in p.history_many(syms, "5y", "1d").items()
                if v is not None and len(v) > 250}
    print(f"Kurser för {len(all_bars)} aktier.")
    v = {s.swing_variant: swing_variants()[s.swing_variant],
         "Utbrott – låt vinnare löpa": swing_variants()["Utbrott – låt vinnare löpa"]}
    for n in (100, 300, 600, 1000):
        bars = {k: all_bars[k] for k in syms[:n] if k in all_bars}
        df = compare_strategies(bars, v, cap, s, benchmark=spy.Close)
        print(f"\n### De {n} mest omsatta ({len(bars)} med data) – {df['Testperiod'].iloc[0]}")
        print(df[["Strategi", "Per år %", "Avkastning %", "S&P 500 %", "Största nedgång %", "Affärer",
                  "Vinstandel %", "Testdel %", "Kontrolldel %"]].to_string(index=False))


if __name__ == "__main__":
    main()
