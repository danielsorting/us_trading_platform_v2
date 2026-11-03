"""Scannern: går igenom aktier, bedömer dem med strategierna och räknar
ut positionsstorlek (antal aktier) utifrån kapital och risk."""
from concurrent.futures import ThreadPoolExecutor

from .config import Settings
from .data import YahooResearchProvider
from .risk import position_size, sek_to_usd, usd_to_sek
from .strategies import day, swing
from .strategies.variants import add_market

SIGNAL_ORDER = {"CANDIDATE": 0, "WATCH": 1, "LOW_LIQUIDITY": 2, "INSUFFICIENT_DATA": 3, "DATA_ERROR": 4}


def resolve_usd_sek(provider, settings):
    """Hämtar USD/SEK; faller tillbaka på värdet i inställningarna."""
    rate = provider.usd_sek() if provider else None
    return (rate, "live") if rate else (settings.usd_sek_fallback, "reserv (config.json)")


def add_sizing(row, settings, usd_sek):
    """Lägger till antal aktier, positionsvärde och risk på en resultatrad."""
    if row.get("price") is None or row.get("stop_reference") is None:
        return row
    cap_sek = settings.day_capital if row["strategy"] == "day" else settings.swing_capital
    cap_usd = sek_to_usd(cap_sek, usd_sek)
    qty = position_size(
        cap_usd,
        row["price"],
        row["stop_reference"],
        settings.risk_per_trade_pct,
        settings.max_position_pct_of_strategy_capital,
        settings.allow_fractional_shares,
    )
    row["shares"] = qty
    row["position_usd"] = round(qty * row["price"], 2)
    row["position_sek"] = round(usd_to_sek(qty * row["price"], usd_sek), 0)
    row["risk_usd"] = round(qty * (row["price"] - row["stop_reference"]), 2)
    row["risk_sek"] = round(usd_to_sek(row["risk_usd"], usd_sek), 0)
    if qty == 0 and row["signal"] == "CANDIDATE":
        row["note"] = "För lite kapital inom risk-/positionsgränsen."
    return row


def _scan_symbol(symbol, include_day, include_swing, provider, settings, swing_strategy=swing, market_bars=None):
    rows = []
    daily = None
    try:
        daily = provider.history(symbol, "2y", "1d")
    except Exception as e:
        if include_swing:
            rows.append({"symbol": symbol, "strategy": "swing", "signal": "DATA_ERROR", "reason": str(e)[:160]})

    # Likviditetsfilter: genomsnittlig daglig omsättning i dollar (20 dagar).
    min_dv = settings.minimum_average_daily_dollar_volume
    if daily is not None and len(daily) >= 20 and min_dv > 0:
        dv = float((daily.Close * daily.Volume).tail(20).mean())
        if dv < min_dv:
            for name, on in (("swing", include_swing), ("day", include_day)):
                if on:
                    rows.append({
                        "symbol": symbol, "strategy": name, "signal": "LOW_LIQUIDITY",
                        "reason": f"Omsättning {dv/1e6:.1f} MUSD/dag < {min_dv/1e6:.1f} MUSD",
                    })
            return rows

    if include_swing and not rows:
        try:
            want = getattr(swing_strategy, "DATA", None)
            bars = provider.history(symbol, *want) if want else daily
            rows.append(swing_strategy.evaluate(symbol, add_market(bars, market_bars)))
        except Exception as e:
            rows.append({"symbol": symbol, "strategy": "swing", "signal": "DATA_ERROR", "reason": str(e)[:160]})
    if include_day:
        try:
            rows.append(day.evaluate(symbol, provider.history(symbol, "5d", "5m")))
        except Exception as e:
            rows.append({"symbol": symbol, "strategy": "day", "signal": "DATA_ERROR", "reason": str(e)[:160]})
    return rows


def scan(symbols, include_day=True, include_swing=True, provider=None, settings=None,
         usd_sek=None, max_workers=8, progress=None, swing_strategy=None, market_bars=None):
    """Skannar aktier. Returnerar en lista med rader (en per aktie och strategi),
    sorterad med köpkandidater först.

    progress: valfri funktion progress(klara, totalt) för t.ex. en förloppsindikator.
    swing_strategy: vilken swing-variant som används (standard: grundstrategin).
    market_bars: kurser för SPY, behövs för varianter med marknadsfilter.
    """
    swing_strategy = swing_strategy or swing
    provider = provider or YahooResearchProvider()
    settings = settings or Settings()
    if usd_sek is None:
        usd_sek, _ = resolve_usd_sek(provider, settings)
    symbols = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_scan_symbol, s, include_day, include_swing, provider, settings,
                               swing_strategy, market_bars) for s in symbols]
        for i, f in enumerate(futures, 1):
            results.extend(f.result())
            if progress:
                progress(i, len(symbols))

    results = [add_sizing(r, settings, usd_sek) for r in results]
    results.sort(key=lambda r: (SIGNAL_ORDER.get(r.get("signal"), 9), r.get("strategy", ""), r.get("symbol", "")))
    return results
