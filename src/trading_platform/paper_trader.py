"""Simulerad handel: en "handelsrunda" med papper-mäklaren.

En runda gör två saker:
1. Säljer positioner som nått stop loss, kursmål eller tidsgräns.
2. Köper nya köpkandidater från scannern, inom risk- och positionsgränser.
3. Om parkering är på: ledig kassa köps in i SPY (S&P 500). SPY säljs
   automatiskt när pengarna behövs till ett aktieköp.

Köp och sälj sker till senaste kända kurs från datakällan (kan vara
fördröjd). Inga riktiga order skickas.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .risk import position_size

SWING_MAX_CALENDAR_DAYS = 28  # ungefär 20 handelsdagar
PARK = "park"                 # strategi-märkning för parkerat kapital
PARK_BUFFER_PCT = 1.0         # så mycket kontanter (%) som alltid lämnas kvar


def _free_cash_from_park(broker, settings, prices, needed, actions):
    """Säljer SPY så att det finns minst `needed` USD i kassan."""
    sym = settings.park_symbol
    pos = broker.positions.get(sym)
    price = prices.get(sym)
    if broker.cash >= needed or not pos or pos.get("strategy") != PARK or not price:
        return
    qty = min(pos["quantity"], round((needed - broker.cash) * 1.02 / price + 0.00005, 4))
    if qty > 0:
        broker.submit_order(sym, "sell", qty, price=price, strategy=PARK, reason="pengar till aktieköp")
        actions.append({"symbol": sym, "action": "SÄLJ (parkering)", "detail": f"{qty} st för att betala köp"})


def _park_idle_cash(broker, settings, prices, actions):
    """Köper SPY för ledig kassa (utom en liten buffert)."""
    sym = settings.park_symbol
    price = prices.get(sym)
    if not price:
        actions.append({"symbol": sym, "action": "ingen kurs", "detail": "Kunde inte parkera kassan."})
        return
    held = broker.positions.get(sym)
    if held and held.get("strategy") != PARK:
        return  # roboten äger redan SPY som vanlig affär
    equity = broker.get_account(prices)["equity"]
    spend = broker.cash - equity * PARK_BUFFER_PCT / 100
    if spend < 50:
        return
    qty = round(spend * 0.98 / price - 0.00005, 4)
    if qty <= 0:
        return
    if held:  # öka befintlig parkering: sälj inte, köp till som ny post
        old = broker.positions.pop(sym)
        broker.submit_order(sym, "buy", qty, price=price, strategy=PARK)
        new = broker.positions[sym]
        total = old["quantity"] + new["quantity"]
        new["avg_price"] = (old["quantity"] * old["avg_price"] + new["quantity"] * new["avg_price"]) / total
        new["entry_fee"] = old.get("entry_fee", 0) + new.get("entry_fee", 0)
        new["quantity"] = total
        new["opened"] = old["opened"]
        broker.save()
    else:
        broker.submit_order(sym, "buy", qty, price=price, strategy=PARK)
    actions.append({"symbol": sym, "action": "KÖP (parkering)", "detail": f"{qty} st ledig kassa i S&P 500"})


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


DAY_CLOSE_NY = time(15, 50)  # stäng dagpositioner 10 min före börsens stängning (21:50 svensk tid)


def close_all_day_positions(broker, prices, reason="stängd manuellt"):
    """Säljer alla daytrading-positioner till senaste kurs."""
    actions = []
    for sym, pos in broker.get_positions().items():
        if pos.get("strategy") != "day":
            continue
        price = prices.get(sym)
        if price is None:
            actions.append({"symbol": sym, "action": "ingen kurs", "detail": "Kunde inte stänga – kurs saknas."})
            continue
        order = broker.submit_order(sym, "sell", pos["quantity"], price=price, reason=reason, strategy="day")
        actions.append({"symbol": sym, "action": "SÄLJ", "detail": f"{reason}, resultat {order['pnl']:+.2f} USD"})
    return actions


def run_paper_cycle(broker, scan_rows, settings, prices, now=None, limits=None, ny_now=None):
    """Kör en handelsrunda. Returnerar en lista med vad som hände.

    limits: valfritt max antal positioner per strategi, t.ex. {"day": 10}.
    """
    now = now or datetime.now()
    ny_now = ny_now or datetime.now(ZoneInfo("America/New_York"))
    limits = limits or {}
    actions = []

    # 1) Sälj.
    for sym, pos in broker.get_positions().items():
        if pos.get("strategy") == PARK:
            continue
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
        elif pos.get("strategy") == "day" and ny_now.weekday() < 5 and ny_now.time() >= DAY_CLOSE_NY:
            reason = "dagens slut (börsen stänger snart)"
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
        if row.get("signal") != "CANDIDATE" or sym in held or sym == settings.park_symbol:
            continue
        if ((strat == "day" and ny_now.weekday() < 5 and ny_now.time() >= DAY_CLOSE_NY)):
            continue  # inga nya dagpositioner strax före stängning
        if sum(1 for p in held.values() if p.get("strategy") == strat) >= limits.get(strat, settings.max_positions_per_strategy):
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
            if settings.park_idle_in_index:
                _free_cash_from_park(broker, settings, prices, qty * price * 1.01 + 2, actions)
            broker.submit_order(sym, "buy", qty, price=price, stop=stop, target=target, strategy=strat,
                                trail_atr=row.get("trail_atr"), atr=row.get("atr"))
            held = broker.get_positions()
            actions.append({"symbol": sym, "action": "KÖP",
                            "detail": f"{qty} st à {price:.2f} USD ({strat}), stop {stop:.2f}"})
        except ValueError as e:
            actions.append({"symbol": sym, "action": "hoppar över", "detail": str(e)})
    if settings.park_idle_in_index:
        _park_idle_cash(broker, settings, prices, actions)
    if not actions:
        actions.append({"symbol": "", "action": "inget att göra", "detail": "Inga säljsignaler och inga nya köpkandidater."})
    return actions
