from trading_platform import notify


def test_only_swing_buys_and_sells_give_notifications(monkeypatch):
    ev = [{"typ": "swing", "action": "KÖP", "symbol": "AAPL", "detail": "3 st à 200 USD"},
          {"typ": "swing", "action": "SÄLJ", "symbol": "MSFT", "detail": "kursmål"},
          {"typ": "day", "action": "KÖP", "symbol": "TSLA"},
          {"typ": "swing", "action": "swing-koll", "symbol": ""},
          {"typ": "park", "action": "KÖP (parkering)", "symbol": "SPY"}]
    msgs = notify.messages_for(ev)
    assert [m[0] for m in msgs] == ["🟢 Swing-köp: AAPL", "🔴 Swing-sälj: MSFT"]
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert notify.send("x", "y") is False  # utan kanal skickas inget (och inget fel)
