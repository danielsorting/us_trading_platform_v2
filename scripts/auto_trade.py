"""Bakgrundsrobot: handlar med LÅTSASPENGAR automatiskt, även när ingen
webbläsare är öppen. Körs av GitHub Actions var 5:e minut under börsens
öppettider (se .github/workflows/auto_trade.yml).

Kontot sparas i paper/auto_account.json och händelserna i paper/auto_log.csv.
Webbsidan visar dem under fliken "🤖 Automatisk handel".
"""
import csv
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading_platform.autotrader import tick  # noqa: E402
from trading_platform.brokers.paper import PaperBroker  # noqa: E402
from trading_platform.config import load_settings  # noqa: E402
from trading_platform.daytop import liquid_universe, market_status  # noqa: E402
from trading_platform.data import YahooResearchProvider  # noqa: E402
from trading_platform.risk import sek_to_usd  # noqa: E402
from trading_platform.strategies.variants import MARKET_SYMBOL, needs_market, swing_variants  # noqa: E402

PAPER = ROOT / "paper"
ACCOUNT = PAPER / "auto_account.json"
STATE = PAPER / "auto_state.json"
LOG = PAPER / "auto_log.csv"
LIQUID = PAPER / "liquid_ranking.json"
LIQUID_MAX_AGE = 20 * 3600  # uppdateras en gång per handelsdag


def load_json(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def liquid_list(provider, n=300):
    saved = load_json(LIQUID, {})
    if (len(saved.get("symbols") or []) >= min(n, 100)
            and time.time() - saved.get("time", 0) < LIQUID_MAX_AGE):
        return saved["symbols"][:n]
    ranking = liquid_universe(provider, max(n, 300), fallback_symbols=load_settings().watchlist, allow_heavy=False)
    LIQUID.write_text(json.dumps({"time": time.time(), "symbols": ranking}))
    return ranking[:n]


T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


def main():
    s = load_settings()
    if not s.auto_trading_enabled:
        print("Automatisk handel är avstängd i config.json (auto_trading_enabled).")
        return
    is_open, text = market_status()
    PAPER.mkdir(exist_ok=True)
    provider = YahooResearchProvider()
    fx = provider.usd_sek() or s.usd_sek_fallback
    broker = PaperBroker(sek_to_usd(s.total_capital, fx), ACCOUNT, s.commission_per_share_usd,
                         s.min_commission_usd, s.slippage_pct, fx, s.commission_pct, s.fx_fee_pct)
    has_day_pos = any(p.get("strategy") == "day" for p in broker.positions.values())
    if not is_open and not has_day_pos:
        print(text, "Inget att göra.")
        return
    log(f"{text} Startar. USD/SEK {fx:.2f}.")
    state = load_json(STATE, {})
    variant = swing_variants().get(s.swing_variant)
    mkt = provider.history(MARKET_SYMBOL, "2y", "1d") if variant is not None and needs_market([variant]) else None
    need_liquid = s.day_allocation_pct > 0 or s.swing_universe == "liquid"
    liquid = liquid_list(provider) if (is_open and need_liquid) else []
    log(f"Lista över mest omsatta: {len(liquid)} aktier.")
    events = tick(provider, broker, s, state, liquid, swing_strategy=variant, market_bars=mkt, usd_sek=fx,
                  top_n=s.auto_day_top_n, max_move_pct=s.auto_max_move_pct)
    log(f"Runda klar: {len(events)} händelser.")
    from trading_platform import notify
    for title, msg in notify.messages_for(events, fx):
        log(f"Notis: {title} – {'skickad' if notify.send(title, msg) else 'inte skickad (NTFY_TOPIC saknas?)'}")
    broker.save()
    state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATE.write_text(json.dumps(state, indent=2, default=str, ensure_ascii=False))
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tid", "typ", "symbol", "action", "detail"], extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(events)
    acc = broker.get_account()
    print(f"{text} Händelser: {len(events)}. Kontovärde (inköpspris) {acc['equity']:.2f} USD.")
    for e in events:
        print(e)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
