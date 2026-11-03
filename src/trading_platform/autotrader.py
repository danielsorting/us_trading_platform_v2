"""Automatisk handel med låtsaspengar – används både av webbsidan och av
bakgrundsroboten på GitHub (scripts/auto_trade.py).

En "tick" (körs t.ex. var 15:e minut):
1. Börsen stängd → stäng eventuella dagpositioner, annars inget.
2. Daytrading: ny sökning efter dagens bästa var 30:e minut, annars färska kurser
   för den sparade listan. Rangordna, förhandelskoll, köp de bästa, sälj vid
   stop/mål/dagens slut.
3. Swing: var 30:e minut – skanna bevakningslistan och köp kandidater.
   Stop/mål för alla positioner kontrolleras varje tick.
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


def day_strategy_for(settings):
    """Daytrading-strategin som valts i inställningarna (standard: nuvarande momentum)."""
    from .strategies.day_variants import day_variants
    return day_variants().get(getattr(settings, "day_variant", ""), None)


def day_round(provider, broker, settings, universe, top_n=10, day_share=20.0, max_move_pct=2.0,
              is_open=True, progress=None, ny_now=None):
    """Daytrading-runda. Returnerar (rangordning, händelser, priser)."""
    intraday = provider.history_many(list(universe), "5d", "5m", progress=progress)
    ranked = rank_day_candidates(intraday, day_strategy_for(settings))
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


def _records(df, cols, n=30):
    """Tabell → lista med enkla dictar (sparbar som JSON)."""
    if df is None or not len(df):
        return []
    cols = [c for c in cols if c in df]
    out = df[cols].head(n).copy()
    return [{k: (None if v != v else (v.item() if hasattr(v, "item") else v)) for k, v in r.items()}
            for r in out.to_dict("records")]


DAY_COLS = ["symbol", "signal", "score", "price", "ext_price", "ext_move_pct", "change_today_pct", "rel_volume",
            "rsi", "stop_reference", "target_reference", "conditions_met", "conditions"]
SWING_COLS = ["symbol", "signal", "price", "rsi", "stop_reference", "target_reference", "conditions_met",
              "conditions"]


def tick(provider, broker, settings, state, liquid_symbols, swing_strategy=None, market_bars=None,
         usd_sek=10.0, top_n=10, max_move_pct=2.0, now_ny=None, force=False, swing_symbols=None,
         search_every_min=SEARCH_EVERY_MIN, progress=None):
    """En automatisk runda för BÅDE daytrading och swing. `state` sparas mellan körningarna.

    - Daytrading: ny sökning bland `liquid_symbols` var `search_every_min` minut (eller om
      force=True), annars färska kurser för den sparade listan. Köp/sälj varje runda.
    - Swing: skanna `swing_symbols` (standard: bevakningslistan) var `search_every_min` minut
      (eller om force=True) och köp kandidater. Stop/mål för ALLA positioner kontrolleras varje runda.
    - Parkerade pengar i SPY säljs automatiskt när ett köp behöver dem.

    Returnerar en lista med händelser (med tid och typ). Rangordningarna sparas i
    state["day_top"] och state["swing_top"]."""
    now_ny = (now_ny or datetime.now(NY)).astimezone(NY)
    stamp = now_ny.astimezone(ZoneInfo("Europe/Stockholm")).strftime("%Y-%m-%d %H:%M")
    is_open, _ = market_status(now_ny)
    events = []
    swing_symbols = list(swing_symbols or settings.watchlist)

    def add(kind, acts):
        for a in acts:
            if a.get("action") != "inget att göra":
                events.append({"tid": stamp, "typ": kind, **a})

    if not is_open:
        day_pos = [s for s, p in broker.positions.items() if p.get("strategy") == "day"]
        if day_pos:
            add("day", close_all_day_positions(broker, latest_prices(provider, day_pos), reason="börsen stängd"))
        return events

    due = force or now_ny.timestamp() - state.get("last_search_ts", 0) >= search_every_min * 60

    # Swing först (få men större affärer), sedan daytrading.
    if settings.swing_allocation_pct > 0 and (due or not state.get("swing_top")):
        rows = scan(swing_symbols, False, True, provider=provider, settings=settings, usd_sek=usd_sek,
                    swing_strategy=swing_strategy, market_bars=market_bars)
        prices = latest_prices(provider, set(swing_symbols) | set(broker.positions)
                               | ({settings.park_symbol} if settings.park_idle_in_index else set()))
        add("swing", run_paper_cycle(broker, rows, settings, prices, ny_now=now_ny))
        import pandas as pd
        state["swing_top"] = _records(pd.DataFrame(rows), SWING_COLS, n=len(rows))
        state["last_swing_date"] = now_ny.strftime("%Y-%m-%d")

    if settings.day_allocation_pct > 0:
        saved = state.get("day_list") or []
        searched = due or not saved
        if searched:
            universe = list(liquid_symbols)
        else:
            universe = list(dict.fromkeys(saved + [s for s, p in broker.positions.items()
                                                   if p.get("strategy") == "day"]))
        ranked, acts, _ = day_round(provider, broker, settings, universe, top_n=top_n,
                                    day_share=settings.day_allocation_pct, max_move_pct=max_move_pct,
                                    is_open=True, progress=progress, ny_now=now_ny)
        if searched and len(ranked):
            state["day_list"] = ranked.symbol.head(30).tolist()
        state["day_top"] = _records(ranked, DAY_COLS)
        add("day", acts)
    else:
        # Ingen daytrading: kontrollera ändå stop/mål för alla positioner.
        prices = latest_prices(provider, set(broker.positions))
        add("swing", run_paper_cycle(broker, [], settings, prices, ny_now=now_ny))

    if due:
        state["last_search_ts"] = now_ny.timestamp()
        state["last_search_time"] = stamp
    state["last_tick_time"] = stamp
    return events
