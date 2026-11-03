"""Skriver ut börsens läge just nu: before / open / after / weekend.
Används av arbetsflödet för att veta när dagens loop ska sluta."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

now = datetime.now(ZoneInfo("America/New_York"))
if now.weekday() >= 5:
    print("weekend")
elif now.time() < time(9, 30):
    print("before")
elif now.time() < time(16, 0):
    print("open")
else:
    print("after")
