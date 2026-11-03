"""Nyhetsfilter: läser de senaste nyheterna om bolagen och om världsläget.

Källa: Yahoo Finance-nyheter (gratis, via yfinance). Rubrikerna är på engelska och
bedöms med en ordlista över ord som brukar flytta aktiekurser (t.ex. "downgrade",
"lawsuit", "beats estimates", "raises guidance").

Hur det används:
1. Bolagsnyheter: innan roboten köper en aktie läses nyheterna från de senaste
   48 timmarna. Finns en tydligt dålig nyhet (t.ex. nyemission, stämning,
   sänkt prognos, utredning) köps aktien INTE – i loggen står rubriken.
2. Världsläget: nyheter om börsen, olja, Trump/tullar och räntor vägs ihop till
   ett "marknadshumör". Är det tydligt negativt halveras risken per ny affär.

Ärligt: det går inte att backtesta nyheter bakåt i tiden, så filtret är ett
skydd mot uppenbart dåliga köp – inte en bevisad vinstmaskin.
"""
from datetime import datetime, timedelta, timezone

# Ord/fraser och vikt. Negativt = dåligt för kursen.
SEVERE = {  # så allvarligt att roboten aldrig köper (inom 48 h)
    "bankruptcy": -5, "chapter 11": -5, "fraud": -4, "sec investigation": -4, "sec probe": -4,
    "investigation": -3, "probe": -3, "subpoena": -3, "class action": -3, "lawsuit": -2,
    "public offering": -3, "stock offering": -3, "share offering": -3, "secondary offering": -3,
    "dilution": -3, "delisting": -4, "delisted": -4, "trading halt": -3, "halted": -3,
    "going concern": -5, "restatement": -4, "accounting irregularities": -5, "short seller": -3,
    "short report": -3, "guidance cut": -3, "cuts guidance": -3, "lowers guidance": -3,
    "slashes": -3, "profit warning": -4, "recall": -2, "ceo resigns": -3, "cfo resigns": -3,
}
NEGATIVE = {
    "downgrade": -2, "downgraded": -2, "misses": -2, "missed estimates": -2, "below estimates": -2,
    "plunge": -2, "plunges": -2, "tumble": -2, "tumbles": -2, "sinks": -2, "slump": -2, "slumps": -2,
    "falls": -1, "drops": -1, "decline": -1, "weak": -1, "layoffs": -1, "cuts jobs": -1, "loss": -1,
    "warning": -1, "sell-off": -2, "selloff": -2, "crash": -2, "fears": -1,
    "tariff": -1, "tariffs": -1, "sanctions": -1, "recession": -2, "war": -2, "escalat": -1,
    "price target cut": -2, "lowers price target": -2, "bearish": -1, "underperform": -2,
}
POSITIVE = {
    "upgrade": 2, "upgraded": 2, "beats": 2, "beat estimates": 2, "tops estimates": 2,
    "above estimates": 2, "raises guidance": 3, "raises outlook": 3, "record revenue": 2, "record": 1,
    "buyback": 2, "share repurchase": 2, "approval": 2, "approved": 2, "fda approval": 3,
    "partnership": 1, "contract": 1, "wins": 1, "surges": 2, "soars": 2, "jumps": 1, "rallies": 1,
    "rally": 1, "gains": 1, "rises": 1, "strong demand": 2, "outperform": 2, "bullish": 1,
    "raises price target": 2, "price target raised": 2, "rate cut": 1, "ceasefire": 1, "deal": 1,
}
MARKET_QUERIES = ("stock market today", "oil prices", "Trump tariffs", "Federal Reserve interest rates",
                  "Wall Street stocks")
BLOCK_SCORE = -3        # så här dåligt (summa) räcker för att inte köpa
MARKET_RISK_OFF = -4    # marknadshumör under detta → halverad risk per ny affär
MAX_AGE_HOURS = 48


def score_text(text):
    """Poäng för en rubrik. Returnerar (poäng, allvarlig?)."""
    t = f" {(text or '').lower()} "
    score, severe = 0, False
    for words, is_severe in ((SEVERE, True), (NEGATIVE, False), (POSITIVE, False)):
        for w, v in words.items():
            if w in t:
                score += v
                severe = severe or is_severe
    return score, severe


def _parse_item(item):
    """Yahoo har två format för nyheter (äldre och nyare yfinance). → dict eller None."""
    if not isinstance(item, dict):
        return None
    c = item.get("content") if isinstance(item.get("content"), dict) else item
    title = c.get("title") or ""
    if not title:
        return None
    when = None
    if c.get("pubDate") or c.get("displayTime"):
        try:
            when = datetime.fromisoformat(str(c.get("pubDate") or c.get("displayTime")).replace("Z", "+00:00"))
        except ValueError:
            when = None
    elif c.get("providerPublishTime"):
        try:
            when = datetime.fromtimestamp(int(c["providerPublishTime"]), timezone.utc)
        except (TypeError, ValueError):
            when = None
    prov = c.get("provider")
    publisher = (prov.get("displayName") if isinstance(prov, dict) else None) or c.get("publisher") or ""
    link = ""
    for k in ("canonicalUrl", "clickThroughUrl"):
        if isinstance(c.get(k), dict) and c[k].get("url"):
            link = c[k]["url"]
            break
    link = link or c.get("link") or ""
    summary = c.get("summary") or c.get("description") or ""
    return {"title": title, "time": when, "publisher": publisher, "link": link, "summary": summary}


def recent(items, now=None, max_age_hours=MAX_AGE_HOURS):
    """Bara nyheter från de senaste max_age_hours timmarna (utan datum räknas de som nya)."""
    now = now or datetime.now(timezone.utc)
    out = []
    for it in items:
        p = _parse_item(it) if "title" not in it or "time" not in it else it
        if not p:
            continue
        t = p.get("time")
        if t is not None and t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t is None or now - t <= timedelta(hours=max_age_hours):
            out.append(p)
    return out


def judge(items, now=None):
    """Samlad bedömning av nyheter: dict med poäng, om köp ska stoppas och värsta/bästa rubrik."""
    news = recent(items, now)
    total, severe_hit, worst, best = 0, False, None, None
    for n in news:
        s, sev = score_text(n["title"] + " " + (n.get("summary") or "")[:200])
        n["score"] = s
        total += s
        severe_hit = severe_hit or (sev and s < 0)
        if worst is None or s < worst["score"]:
            worst = n
        if best is None or s > best["score"]:
            best = n
    block = severe_hit or total <= BLOCK_SCORE
    return {"count": len(news), "score": total, "block": block,
            "worst": worst["title"] if worst is not None and worst["score"] < 0 else None,
            "best": best["title"] if best is not None and best["score"] > 0 else None,
            "headlines": [{"title": n["title"], "score": n["score"], "publisher": n.get("publisher", ""),
                           "time": n["time"].isoformat() if n.get("time") else None, "link": n.get("link", "")}
                          for n in sorted(news, key=lambda n: n["score"])[:6]]}


# ---------------------------------------------------------------- hämtning (Yahoo)
def fetch_symbol_news(symbol, count=10):
    """Senaste nyheterna om ett bolag från Yahoo Finance. Tom lista vid fel."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        try:
            items = t.get_news(count=count)
        except Exception:
            items = t.news
        return list(items or [])[:count]
    except Exception:
        return []


def fetch_market_news(queries=MARKET_QUERIES, per_query=8):
    """Nyheter om världsläget: börsen, olja, Trump/tullar, räntor."""
    items = []
    try:
        import yfinance as yf
        for q in queries:
            try:
                items += list(yf.Search(q, news_count=per_query).news or [])
            except Exception:
                continue
    except Exception:
        pass
    seen, out = set(), []
    for it in items:
        p = _parse_item(it)
        if p and p["title"] not in seen:
            seen.add(p["title"])
            out.append(p)
    return out


# ---------------------------------------------------------------- används av roboten
def market_mood(state, now=None, fetch=None, refresh_min=30):
    """Marknadshumör (cachat i state i refresh_min minuter)."""
    now = now or datetime.now(timezone.utc)
    cached = state.get("market_news") or {}
    ts = cached.get("ts")
    if ts and (now - datetime.fromisoformat(ts)).total_seconds() < refresh_min * 60:
        return cached
    j = judge((fetch or fetch_market_news)(), now)
    mood = "negativt" if j["score"] <= MARKET_RISK_OFF else "positivt" if j["score"] >= 4 else "neutralt"
    out = {"ts": now.isoformat(timespec="seconds"), "score": j["score"], "mood": mood, "count": j["count"],
           "risk_off": j["score"] <= MARKET_RISK_OFF, "headlines": j["headlines"]}
    state["market_news"] = out
    return out


def filter_candidates(rows, state, now=None, fetch=None, refresh_min=30):
    """Tar bort köpkandidater med dåliga nyheter. Returnerar (kvar, händelser).
    Nyheter per aktie cachas i state["news"] i refresh_min minuter."""
    now = now or datetime.now(timezone.utc)
    cache = state.setdefault("news", {})
    keep, acts = [], []
    for r in rows:
        if r.get("signal") != "CANDIDATE":
            keep.append(r)
            continue
        sym = r.get("symbol")
        c = cache.get(sym)
        if not c or (now - datetime.fromisoformat(c["ts"])).total_seconds() >= refresh_min * 60:
            j = judge((fetch or fetch_symbol_news)(sym), now)
            c = {"ts": now.isoformat(timespec="seconds"), **j}
            cache[sym] = c
        r = {**r, "news_score": c["score"], "news_count": c["count"]}
        if c["block"]:
            acts.append({"symbol": sym, "action": "hoppar över (nyheter)",
                         "detail": f"Dålig nyhet senaste 48 h: \"{c['worst']}\" (nyhetspoäng {c['score']})."})
            continue
        keep.append(r)
    # Håll cachen liten: bara de senaste 200 aktierna.
    if len(cache) > 200:
        for k in sorted(cache, key=lambda k: cache[k]["ts"])[:-200]:
            del cache[k]
    return keep, acts
