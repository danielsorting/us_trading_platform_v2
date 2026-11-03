"""Skriver ut börsens läge just nu: weekend / before / pre / open / post / after.
pre  = Avanzas förhandel (11:00–15:30 svensk tid), post = efterhandel (22:00–22:30).
Används av arbetsflödet för att veta när roboten ska handla och när dagens loop ska sluta."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

now = datetime.now(ZoneInfo("America/New_York"))
if now.weekday() >= 5:
    print("weekend")
elif now.time() < time(5, 0):
    print("before")
elif now.time() < time(9, 30):
    print("pre")
elif now.time() < time(16, 0):
    print("open")
elif now.time() < time(16, 30):
    print("post")
else:
    print("after")
