"""Simulerad handel: en "handelsrunda" med papper-mäklaren.

En runda gör två saker:
1. Säljer positioner som nått stop loss, kursmål eller tidsgräns.
2. Köper nya köpkandidater från scannern, inom risk- och positionsgränser.

Köp och sälj sker till senaste kända kurs från datakällan (kan vara
fördröjd). Inga riktiga order skickas.
"""
from datetime import datetime

from .risk import position_size

SWING_MAX_CALENDAR_DAYS = 28  # ungefär 20 handelsdagar


def latest_prices(provider, symbols):
    """Senaste kurs per aktie (5-minutersdata, annars dagsdata)."""
    prices = {}
    for sym in symbols:
        for period, interval in (("5d", "5m"), ("5d", "1d")):
            try:
                df = provider.history(sym, period, interval)
                if df is not None and len(df):
                    prices[sym] = float(df.Close.iloc[-1])
                    break
            except Exception:
                continue
    return prices


def run_paper_cycle(broker, scan_rows, settings, prices, now=None):
    """Kör en handelsrunda. Returnerar en lista med vad som hände."""
    now = now or datetime.now()
    actions = []

    # 1) Sälj.
    for sym, pos in broker.get_positions().items():
        price = prices.get(sym)
        if price is None:
            actions.append({"symbol": sym, "action": "ingen kurs", "detail": "Kunde inte hämta aktuell kurs."})
            continue
        # Följande stop: flytta upp stop loss när kursen stiger (aldrig ned).
        if pos.get("trail_atr") and pos.get("atr") and pos.get("stop") is not None:
            new_stop = round(price - pos["trail_atr"] * pos["atr"], 2)
            if new_stop > pos["stop"] and price > new_stop:
                broker.positions[sym]["stop"] = new_stop
                broker.save()
                actions.append({"symbol": sym, "action": "stop flyttad", "detail": f"Ny stop loss {new_stop:.2f}"})
                pos["stop"] = new_stop
        opened = datetime.fromisoformat(pos["opened"])
        reason = None
        if pos.get("stop") is not None and price <= pos["stop"]:
            reason = "stop loss"
        elif pos.get("target") is not None and price >= pos["target"]:
            reason = "kursmål"
        elif pos.get("strategy") == "day" and opened.date() < now.date():
            reason = "dagposition från tidigare dag"
        elif (pos.get("strategy") == "swing" and not pos.get("trail_atr")
              and (now - opened).days >= SWING_MAX_CALENDAR_DAYS):
            reason = "tidsgräns"
        if reason:
            order = broker.submit_order(sym, "sell", pos["quantity"], price=price, reason=reason,
                                        strategy=pos.get("strategy"))
            actions.append({"symbol": sym, "action": "SÄLJ", "detail": f"{reason}, resultat {order['pnl']:+.2f} USD"})

    # 2) Köp.
    held = broker.get_positions()
    equity = broker.get_account(prices)["equity"]
    alloc = {"day": settings.day_allocation_pct, "swing": settings.swing_allocation_pct}
    for row in scan_rows:
        sym, strat = row.get("symbol"), row.get("strategy")
        if row.get("signal") != "CANDIDATE" or sym in held:
            continue
        if sum(1 for p in held.values() if p.get("strategy") == strat) >= settings.max_positions_per_strategy:
            actions.append({"symbol": sym, "action": "hoppar över", "detail": f"Max antal {strat}-positioner nått."})
            continue
        price = prices.get(sym, row.get("price"))
        stop = row.get("stop_reference")
        if not price or stop is None or stop >= price:
            continue
        cap = equity * alloc.get(strat, 0) / 100
        qty = position_size(cap, price, stop, settings.risk_per_trade_pct,
                            settings.max_position_pct_of_strategy_capital,
                            settings.allow_fractional_shares)
        if qty <= 0:
            actions.append({"symbol": sym, "action": "hoppar över", "detail": "För lite kapital för positionen."})
            continue
        target = price + (row["target_reference"] - row["price"]) if row.get("target_reference") else None
        try:
            broker.submit_order(sym, "buy", qty, price=price, stop=stop, target=target, strategy=strat,
                                trail_atr=row.get("trail_atr"), atr=row.get("atr"))
            held = broker.get_positions()
            actions.append({"symbol": sym, "action": "KÖP",
                            "detail": f"{qty} st à {price:.2f} USD ({strat}), stop {stop:.2f}"})
        except ValueError as e:
            actions.append({"symbol": sym, "action": "hoppar över", "detail": str(e)})
    if not actions:
        actions.append({"symbol": "", "action": "inget att göra", "detail": "Inga säljsignaler och inga nya köpkandidater."})
    return actions
