"""Kör roboten mot Interactive Brokers LÅTSASKONTO.

Körs på en dator/server där IB Gateway är igång och inloggad (se IBKR_GUIDE.md).
Samma strategi och samma skydd som den vanliga roboten, men orderna går till IBKR.

    python scripts/ibkr_trade.py --check      # testa anslutningen, visa konto
    python scripts/ibkr_trade.py --once       # en handelsrunda
    python scripts/ibkr_trade.py              # kör hela dagen, en runda var 5:e minut
    python scripts/ibkr_trade.py --close-all  # NÖDKNAPP: sälj allt roboten äger

Nödstopp utan att stänga programmet: skapa en fil som heter STOPP i projektmappen.
Då görs inga nya köp (sälj fungerar). Ta bort filen för att fortsätta.
"""
import argparse
import csv
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from trading_platform.autotrader import tick  # noqa: E402
from trading_platform.brokers.ibkr import IBClient, IBKRBroker, LiveAccountRefused  # noqa: E402
from trading_platform.config import load_settings  # noqa: E402
from trading_platform.daytop import market_status  # noqa: E402
from trading_platform.data import YahooResearchProvider  # noqa: E402
from trading_platform.paper_trader import latest_prices  # noqa: E402

PAPER = ROOT / "paper"
ACCOUNT = PAPER / "ibkr_account.json"
STATE = PAPER / "ibkr_state.json"
LOG = PAPER / "ibkr_log.csv"
STOP_FILE = ROOT / "STOPP"
ROUND_SECONDS = 300


def load_json(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def write_log(events):
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tid", "typ", "symbol", "action", "detail"], extrasaction="ignore")
        if new:
            w.writeheader()
        now = datetime.now().isoformat(timespec="seconds")
        w.writerows({"tid": now, **e} for e in events)


def make_broker(s, fx):
    client = IBClient(s.ibkr_host, s.ibkr_port, s.ibkr_client_id)
    broker = IBKRBroker(client, ACCOUNT, s, stop_file=STOP_FILE, usd_sek=fx)
    broker.connect_and_sync()
    return broker


def one_round(s, provider, fx):
    from auto_trade import liquid_list
    from trading_platform import autopick, notify
    from trading_platform.strategies.variants import MARKET_SYMBOL, needs_market, swing_variants

    broker = make_broker(s, fx)
    try:
        events = [{"typ": "ibkr", "symbol": "", "action": "info", "detail": n} for n in broker.notes]
        state = load_json(STATE, {})
        s2, _ = autopick.apply(s, swing_variants())
        variant = swing_variants().get(s2.swing_variant)
        mkt = provider.history(MARKET_SYMBOL, "2y", "1d") if variant is not None and needs_market([variant]) else None
        liquid = liquid_list(provider, max(300, s2.swing_liquid_n))
        swing_syms = list(liquid[:s2.swing_liquid_n]) if s2.swing_universe == "liquid" and liquid else None
        events += tick(provider, broker, s2, state, liquid[:300], swing_strategy=variant, market_bars=mkt,
                       usd_sek=fx, top_n=s2.auto_day_top_n, max_move_pct=s2.auto_max_move_pct,
                       swing_symbols=swing_syms)
        broker.save()
        state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        STATE.write_text(json.dumps(state, indent=2, default=str, ensure_ascii=False))
        write_log(events)
        for title, msg in notify.messages_for(events, fx):
            notify.send("IBKR: " + title, msg)
        for e in events:
            print(e)
        acc = broker.get_account()
        print(f"Konto {broker.account_id}: kassa {acc['cash']:.2f} USD, {len(broker.positions)} positioner.")
    finally:
        broker.client.disconnect()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--close-all", action="store_true")
    a = ap.parse_args()
    s = load_settings()
    if not s.ibkr_enabled and not a.check:
        print('IBKR är avstängt. Sätt "ibkr_enabled": true i config.json när du är redo (se IBKR_GUIDE.md).')
        return
    PAPER.mkdir(exist_ok=True)
    provider = YahooResearchProvider()
    fx = provider.usd_sek() or s.usd_sek_fallback

    if a.check:
        broker = make_broker(s, fx)
        print(f"✅ Ansluten till IBKR-låtsaskonto {broker.account_id}. Kassa {broker.cash:.2f} USD.")
        for n in broker.notes:
            print("•", n)
        broker.client.disconnect()
        return
    if a.close_all:
        broker = make_broker(s, fx)
        prices = latest_prices(provider, list(broker.positions))
        events = broker.close_all(prices)
        write_log([{"typ": "nödstopp", **e} for e in events])
        for e in events:
            print(e)
        broker.client.disconnect()
        return
    while True:
        is_open, text = market_status()
        if is_open:
            try:
                one_round(s, provider, fx)
            except LiveAccountRefused:
                raise
            except Exception:
                traceback.print_exc()  # en felande runda stoppar inte roboten
        else:
            print(text, "Väntar.")
        if a.once:
            return
        time.sleep(ROUND_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except LiveAccountRefused as e:
        print("⛔", e)
        sys.exit(2)
    except ImportError:
        print("⛔ Biblioteket för IBKR saknas. Kör:  pip install -r requirements-ibkr.txt")
        sys.exit(3)
    except (ConnectionRefusedError, TimeoutError, OSError) as e:
        print(f"⛔ Får ingen kontakt med IB Gateway ({e}). Är IB Gateway igång och inloggad på låtsaskontot, "
              "och står API-porten på 4002?")
        sys.exit(4)
