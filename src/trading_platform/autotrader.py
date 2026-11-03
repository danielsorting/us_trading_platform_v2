"""Automatisk handel med låtsaspengar – används både av webbsidan och av
bakgrundsroboten på GitHub (scripts/auto_trade.py).

En "tick" (körs t.ex. var 15:e minut):
1. Börsen stängd → stäng eventuella dagpositioner, annars inget.
2. Daytrading: ny sökning efter dagens bästa var 30:e minut, annars färska kurser
   för den sparade listan. Rangordna, förhandelskoll, köp de bästa, sälj vid
   stop/mål/dagens slut.
3. Swing: en gång per dag nära stängning (då dagsstapeln nästan är klar) –
   skanna listan och köp kandidater. Stop/mål för swing kontrolleras varje tick.
4. Ledigt kapital parkeras i SPY (om påslaget).
"""
from dataclasses import replace
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .daytop import add_extended_check, market_status, rank_day_candidates
from .paper_trader import close_all_day_positions, latest_prices, run_paper_cycle
from .scanner import scan

NY = ZoneInfo("America/New_York")
SWING_TIME_NY = time(15, 30)   # 21:30 svensk tid
SEARCH_EVERY_MIN = 30


def day_round(provider, broker, settings, universe, top_n=10, day_share=20.0, max_move_pct=2.0,
              is_open=True, progress=None, ny_now=None):
    """Daytrading-runda. Returnerar (rangordning, händelser, priser)."""
    intraday = provider.history_many(list(universe), "5d", "5m", progress=progress)
    ranked = rank_day_candidates(intraday)
    ext = {}
    if len(ranked):
        try:
            ext = provider.latest_extended(ranked.symbol.head(50).tolist())
        except Exception:
            ext = {}
        ranked = add_extended_check(ranked, ext, max_move_pct)
    prices = {s: float(df.Close.iloc[-1]) for s, df in intraday.items() if df is not None and len(df)}
    prices.update({s: p for s, (p, _) in ext.items() if p})
    extra = (set(broker.positions) | ({settings.park_symbol} if settings.park_idle_in_index else set())) - set(prices)
    prices.update(latest_prices(provider, extra))
    if not is_open:
        return ranked, [{"symbol": "", "action": "väntar", "detail": "Börsen är stängd – inget köpt."}], prices
    top = ranked[ranked.is_candidate].head(top_n) if len(ranked) else ranked
    acts = []
    if len(top) and "move_ok" in top:
        for r in top[~top.move_ok].itertuples():
            acts.append({"symbol": r.symbol, "action": "hoppar över",
                         "detail": f"Har rört sig {r.ext_move_pct:+.1f} % sedan signalen (gräns {max_move_pct} %)."})
        top = top[top.move_ok]
    cands = top.to_dict("records") if len(top) else []
    s_day = replace(settings, day_allocation_pct=float(day_share), swing_allocation_pct=float(100 - day_share),
                    max_position_pct_of_strategy_capital=min(settings.max_position_pct_of_strategy_capital,
                                                             100.0 / max(top_n, 1)))
    acts += run_paper_cycle(broker, cands, s_day, prices, limits={"day": top_n}, ny_now=ny_now)
    if not cands:
        acts.insert(0, {"symbol": "", "action": "inga kandidater",
                        "detail": "Ingen aktie uppfyller alla daytrading-villkor just nu."})
    return ranked, acts, prices


def swing_round(provider, broker, settings, symbols, swing_strategy=None, market_bars=None, usd_sek=10.0,
                ny_now=None):
    """Swing-runda: skanna listan (bara swing) och handla."""
    rows = scan(symbols, False, True, provider=provider, settings=settings, usd_sek=usd_sek,
                swing_strategy=swing_strategy, market_bars=market_bars)
    prices = latest_prices(provider, set(symbols) | set(broker.positions)
                           | ({settings.park_symbol} if settings.park_idle_in_index else set()))
    return run_paper_cycle(broker, rows, settings, prices, ny_now=ny_now), prices


def tick(provider, broker, settings, state, liquid_symbols, swing_strategy=None, market_bars=None,
         usd_sek=10.0, top_n=10, max_move_pct=2.0, now_ny=None):
    """En automatisk runda. `state` är en dict som sparas mellan körningarna.

    Returnerar en lista med händelser (med tid och typ)."""
    now_ny = (now_ny or datetime.now(NY)).astimezone(NY)
    stamp = now_ny.astimezone(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%d %H:%M")
    is_open, _ = market_status(now_ny)
    events = []

    def add(kind, acts):
        for a in acts:
            events.append({"tid": stamp, "typ": kind, **a})

    if not is_open:
        day_pos = [s for s, p in broker.positions.items() if p.get("strategy") == "day"]
        if day_pos:
            add("day", close_all_day_positions(broker, latest_prices(provider, day_pos), reason="börsen stängd"))
        return events

    # Daytrading
    if settings.day_allocation_pct > 0:
        last_search = state.get("last_search_ts", 0)
        saved = state.get("day_list") or []
        if not saved or now_ny.timestamp() - last_search >= SEARCH_EVERY_MIN * 60:
            universe = list(liquid_symbols)
            state["last_search_ts"] = now_ny.timestamp()
        else:
            universe = list(dict.fromkeys(saved + [s for s, p in broker.positions.items()
                                                   if p.get("strategy") == "day"]))
        ranked, acts, _ = day_round(provider, broker, settings, universe, top_n=top_n,
                                    day_share=settings.day_allocation_pct, max_move_pct=max_move_pct,
                                    is_open=True, ny_now=now_ny)
        if universe == list(liquid_symbols) and len(ranked):
            state["day_list"] = ranked.symbol.head(30).tolist()
        add("day", [a for a in acts if a["action"] not in ("inget att göra",)])

    # Swing: en gång per dag nära stängning; stop/mål kontrolleras ändå varje tick ovan.
    today = now_ny.strftime("%Y-%m-%d")
    if (settings.swing_allocation_pct > 0 and now_ny.time() >= SWING_TIME_NY
            and state.get("last_swing_date") != today):
        acts, _ = swing_round(provider, broker, settings, list(settings.watchlist), swing_strategy,
                              market_bars, usd_sek, ny_now=now_ny)
        state["last_swing_date"] = today
        add("swing", [a for a in acts if a["action"] not in ("inget att göra",)])
    elif settings.day_allocation_pct <= 0:
        # Ingen daytrading: kontrollera ändå swing-positionernas stop/mål.
        prices = latest_prices(provider, set(broker.positions))
        add("swing", [a for a in run_paper_cycle(broker, [], settings, prices, ny_now=now_ny)
                      if a["action"] != "inget att göra"])
    return events
