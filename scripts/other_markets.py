"""Testar robotens swing-strategier på andra marknader än aktier (5 år, dagsdata).

Råvaror (terminer), krypto och valutor från Yahoo. Skriver ut en tabell per grupp.
Körs från GitHub Actions: Actions → Testa andra marknader → Run workflow.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from trading_platform.backtest import buy_and_hold_by_symbol, find_best  # noqa: E402
from trading_platform.config import load_settings  # noqa: E402
from trading_platform.data import YahooResearchProvider  # noqa: E402
from trading_platform.risk import sek_to_usd  # noqa: E402
from trading_platform.strategies.variants import add_market, daily_variants  # noqa: E402

GROUPS = {
    "Råvaror (terminer)": {
        "GC=F": "guld", "SI=F": "silver", "HG=F": "koppar", "CL=F": "olja (WTI)", "NG=F": "naturgas",
        "ZC=F": "majs", "ZW=F": "vete", "ZS=F": "sojabönor", "ZM=F": "sojamjöl (foder)", "CT=F": "bomull",
        "KC=F": "kaffe", "SB=F": "socker", "CC=F": "kakao", "LE=F": "levande boskap",
    },
    "Krypto": {"BTC-USD": "bitcoin", "ETH-USD": "ether", "SOL-USD": "solana", "XRP-USD": "xrp",
               "DOGE-USD": "dogecoin"},
    "Valutor": {"EURUSD=X": "euro/dollar", "USDJPY=X": "dollar/yen", "GBPUSD=X": "pund/dollar",
                "USDSEK=X": "dollar/krona"},
}
COLS = ["Strategi", "Per år %", "Avkastning %", "S&P 500 %", "Största nedgång %", "Affärer", "Vinstandel %",
        "Testdel %", "Kontrolldel %", "Godkänd"]


def main():
    s = load_settings()
    p = YahooResearchProvider()
    fx = p.usd_sek() or s.usd_sek_fallback
    cap = sek_to_usd(s.swing_capital or s.total_capital, fx)
    spy = p.history("SPY", "5y", "1d")
    for group, names in GROUPS.items():
        try:
            bars = {k: add_market(v, spy) for k, v in p.history_many(list(names), "5y", "1d").items()
                    if v is not None and len(v) > 250}
            df = find_best(bars, daily_variants(), cap, s, benchmark=spy.Close, risk_levels=(s.risk_per_trade_pct,),
                           min_trades=10)
            bh = buy_and_hold_by_symbol(bars)
        except Exception as e:
            print(f"### {group}: fel {type(e).__name__}: {e}")
            continue
        print(f"\n### {group} ({len(bars)} st: {', '.join(names[k] for k in bars)}) – {df.attrs.get('period')}")
        print(df[[c for c in COLS if c in df]].head(6).to_string(index=False))
        if len(bh):
            print("Köp och behåll:", ", ".join(f"{names.get(r['Aktie'], r['Aktie'])} {r['Köp & behåll %']:+.0f} %"
                                                for _, r in bh.iterrows() if r["Köp & behåll %"] == r["Köp & behåll %"]))


if __name__ == "__main__":
    main()
