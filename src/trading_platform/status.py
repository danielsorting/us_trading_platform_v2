"""Statuslampor: fungerar datainhämtningen och bakgrundsroboten just nu?

Varje kontroll ger (färg, rubrik, förklaring) där färg är "grön", "gul" eller "röd".
"""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .daytop import market_status

NY = ZoneInfo("America/New_York")
SE = ZoneInfo("Europe/Stockholm")
STATE_URL = "https://raw.githubusercontent.com/danielsorting/us_trading_platform_v2/main/paper/auto_state.json"


def data_status(provider, now=None, symbol="SPY"):
    """Hämtar senaste 5-minuterskursen för SPY och kontrollerar hur färsk den är."""
    now = (now or datetime.now(NY)).astimezone(NY)
    is_open, _ = market_status(now)
    try:
        df = provider.history(symbol, "5d", "5m")
    except Exception as e:  # nätverk, Yahoo spärrar m.m.
        return "röd", "Kursdata: fel", f"Kunde inte hämta kurser från Yahoo ({type(e).__name__})."
    if df is None or not len(df):
        return "röd", "Kursdata: fel", "Yahoo svarade utan kurser."
    last = df.index[-1]
    last = last.tz_localize(NY) if getattr(last, "tzinfo", None) is None else last.tz_convert(NY)
    age_min = (now - last.to_pydatetime()).total_seconds() / 60
    price = float(df.Close.iloc[-1])
    when = last.astimezone(SE).strftime("%H:%M")
    if not is_open:
        return "grön", "Kursdata: fungerar", f"Yahoo svarar. Börsen är stängd – senaste kurs {when} (SPY {price:.2f})."
    if age_min <= 15:
        return "grön", "Kursdata: fungerar", f"Yahoo svarar. Senaste kurs {when} svensk tid (SPY {price:.2f})."
    return "gul", "Kursdata: fördröjd", f"Senaste kurs är {age_min:.0f} min gammal ({when}). Kan vara tillfälligt."


def robot_status(state, now=None):
    """Kontrollerar när bakgrundsroboten senast sparade en runda."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    is_open, _ = market_status(now)
    last = (state or {}).get("last_run")
    if not last:
        return ("gul" if is_open else "grön"), "Roboten: ingen runda än", "Roboten har inte sparat någon runda."
    t = datetime.fromisoformat(last)
    t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
    age_min = (now - t).total_seconds() / 60
    when = t.astimezone(SE).strftime("%Y-%m-%d %H:%M") if age_min > 12 * 60 else t.astimezone(SE).strftime("%H:%M")
    if not is_open:
        return "grön", "Roboten: vilar", f"Börsen är stängd. Senaste runda {when}."
    if age_min <= 12:
        return "grön", "Roboten: kör", f"Senaste runda {when} ({age_min:.0f} min sedan). Körs var 5:e minut."
    if age_min <= 30:
        return "gul", "Roboten: sen", f"Senaste runda {when} ({age_min:.0f} min sedan) – väntar på nästa."
    return "röd", "Roboten: står still", f"Ingen runda sedan {when} ({age_min:.0f} min). Säg till Claude."


def load_robot_state(local_path=None, timeout=5):
    """Robotens senaste status: från GitHub (alltid färskast), annars lokal fil."""
    best = None
    try:
        import requests
        r = requests.get(STATE_URL, timeout=timeout)
        if r.ok:
            best = r.json()
    except Exception:
        pass
    try:
        if local_path and local_path.exists():
            local = json.loads(local_path.read_text())
            if not best or str(local.get("last_run", "")) > str(best.get("last_run", "")):
                best = local
    except Exception:
        pass
    return best or {}


LAMP = {"grön": ("🟢", "success"), "gul": ("🟡", "warning"), "röd": ("🔴", "error")}


def render_status_box(provider, local_state_path=None):
    """Visar statuslamporna i sidopanelen. Uppdateras av sig själv varje minut."""
    import streamlit as st

    @st.cache_data(ttl=60, show_spinner=False)
    def _data(_p, minute):
        return data_status(_p)

    @st.cache_data(ttl=60, show_spinner=False)
    def _robot(path, minute):
        return robot_status(load_robot_state(path))

    @st.fragment(run_every=60)
    def _box():
        minute = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        for color, title, text in (_data(provider, minute), _robot(local_state_path, minute)):
            icon, kind = LAMP[color]
            getattr(st, kind)(f"{icon} **{title}**  \n{text}")

    with st.sidebar:
        st.markdown("**Status**")
        _box()
