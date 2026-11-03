"""Visar vad roboten läser för nyheter just nu (körs från GitHub Actions)."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from trading_platform import news  # noqa: E402

syms = sys.argv[1:] or ["AAPL", "ACN", "NVDA"]
for s in syms:
    raw = news.fetch_symbol_news(s)
    print(f"== {s}: {len(raw)} råa nyheter")
    if raw:
        print("   nycklar:", list(raw[0].keys()), "| content-nycklar:",
              list(raw[0].get("content", {}).keys())[:15] if isinstance(raw[0].get("content"), dict) else "-")
    for it in raw[:5]:
        p = news._parse_item(it)
        print("  ", None if not p else (p["time"], p["title"][:100]))
    print("   bedömning:", json.dumps({k: v for k, v in news.judge(raw).items() if k != "headlines"}, default=str))
m = news.fetch_market_news()
print(f"== världen: {len(m)} nyheter")
for p in m[:20]:
    print("  ", p["time"], news.score_text(p["title"])[0], p["title"][:110])
print("nu:", datetime.now(timezone.utc))
