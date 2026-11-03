"""Mobilnotiser via ntfy (gratis, ingen inloggning).

Du installerar appen "ntfy" i mobilen och prenumererar på ett hemligt
kanalnamn. Roboten skickar ett meddelande dit när något viktigt händer.
Kanalnamnet ligger som hemlighet NTFY_TOPIC på GitHub – aldrig i koden,
eftersom projektet är publikt.
"""
import os


def topic():
    return os.environ.get("NTFY_TOPIC", "").strip()


def send(title, message, tags="chart_with_upwards_trend", priority="high", timeout=10):
    """Skickar en notis. Returnerar True om den gick iväg. Kastar aldrig fel."""
    t = topic()
    if not t:
        return False
    try:
        import requests
        r = requests.post(f"https://ntfy.sh/{t}", data=message.encode("utf-8"), timeout=timeout,
                          headers={"Title": title.encode("utf-8"), "Tags": tags, "Priority": priority})
        return r.ok
    except Exception:
        return False


def messages_for(events, usd_sek=10.0):
    """Väljer ut händelser värda en notis: swing-köp och swing-sälj."""
    out = []
    for e in events:
        if e.get("typ") != "swing" or e.get("action") not in ("KÖP", "SÄLJ"):
            continue
        sym = e.get("symbol", "")
        if e["action"] == "KÖP":
            out.append((f"🟢 Swing-köp: {sym}", f"Roboten köpte {sym}: {e.get('detail', '')}. "
                                                 "Vill du göra samma affär hos Avanza?"))
        else:
            out.append((f"🔴 Swing-sälj: {sym}", f"Roboten sålde {sym}: {e.get('detail', '')}."))
    return out
