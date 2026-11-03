from datetime import datetime, timedelta, timezone

from trading_platform import news

NOW = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)


def item_new(title, hours_ago=1):
    t = (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {"id": "x", "content": {"title": title, "pubDate": t, "provider": {"displayName": "Reuters"},
                                   "canonicalUrl": {"url": "https://example.com"}}}


def item_old_format(title, hours_ago=1):
    return {"title": title, "publisher": "Yahoo", "link": "https://example.com",
            "providerPublishTime": int((NOW - timedelta(hours=hours_ago)).timestamp())}


def test_scores_and_both_yahoo_formats():
    assert news.score_text("Company beats estimates and raises guidance")[0] > 0
    s, severe = news.score_text("XYZ announces $500 million public offering")
    assert s < 0 and severe
    j = news.judge([item_new("ACME beats estimates, raises guidance"), item_old_format("ACME upgraded at Goldman")],
                   NOW)
    assert j["count"] == 2 and not j["block"] and j["best"]


def test_bad_news_blocks_buy_but_old_news_does_not():
    rows = [{"symbol": "BAD", "signal": "CANDIDATE"}, {"symbol": "OLD", "signal": "CANDIDATE"},
            {"symbol": "OK", "signal": "CANDIDATE"}, {"symbol": "W", "signal": "WATCH"}]
    feed = {"BAD": [item_new("BAD Corp faces SEC investigation over accounting")],
            "OLD": [item_new("OLD Inc files for bankruptcy", hours_ago=24 * 10)],  # för gammal
            "OK": [item_new("OK Corp shares rise after contract win")]}
    state = {}
    keep, acts = news.filter_candidates(rows, state, now=NOW, fetch=lambda s: feed.get(s, []))
    assert [r["symbol"] for r in keep] == ["OLD", "OK", "W"]
    assert acts[0]["symbol"] == "BAD" and "SEC investigation" in acts[0]["detail"]
    # cachat: andra gången hämtas inget nytt
    calls = []
    news.filter_candidates(rows, state, now=NOW + timedelta(minutes=5), fetch=lambda s: calls.append(s) or [])
    assert calls == []


def test_market_mood_halves_risk_when_world_news_is_bad():
    from trading_platform.autotrader import news_mood
    from trading_platform.config import Settings
    bad = [news._parse_item(item_new(t)) for t in (
        "Stocks plunge as Trump tariffs spark recession fears", "Oil prices crash amid war escalation",
        "Wall Street sell-off deepens", "Tech stocks tumble on downgrade", "Recession fears hit banks")]
    m = news.market_mood({}, now=NOW, fetch=lambda: bad)
    assert m["risk_off"] and m["mood"] == "negativt"
    state, events = {}, []
    real_now = datetime.now(timezone.utc)
    news.market_mood(state, now=real_now, fetch=lambda: [{**b, "time": real_now} for b in bad])
    out = news_mood(Settings(risk_per_trade_pct=2.0, news_market_risk_cut=True), state,
                    lambda kind, acts: events.extend(acts))
    assert out.risk_per_trade_pct == 1.0  # halverad risk
    calm = news_mood(Settings(risk_per_trade_pct=2.0), {"market_news": {**state["market_news"], "risk_off": False}},
                     lambda kind, acts: None)
    assert calm.risk_per_trade_pct == 2.0


def test_mixed_world_news_is_not_risk_off():
    mixed = [news._parse_item(item_new(t)) for t in (
        "S&P 500, Nasdaq open higher as oil prices extend declines", "Stocks wobble ahead of election",
        "Oil Prices Plunge as talks take new turn", "Chip stocks rally", "Dow dips slightly")]
    m = news.market_mood({}, now=NOW, fetch=lambda: mixed)
    assert not m["risk_off"]
