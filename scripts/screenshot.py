"""Tar skärmbilder av webbsidan (startad lokalt med påhittade kurser) – för att kontrollera utseendet."""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screens")
OUT.mkdir(exist_ok=True)
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1400, "height": 1100})
    page.goto("http://localhost:8501", wait_until="networkidle")
    page.wait_for_selector("text=SORTrading Platform", timeout=120_000)
    time.sleep(3)
    page.get_by_role("tab", name="📈 Backtest").click()
    time.sleep(2)
    page.get_by_text("Inställningar för backtestet").first.click()
    time.sleep(2)
    page.screenshot(path=str(OUT / "1_forval.png"), full_page=False)
    # Flytta "Max storlek per swingaffär" fem steg åt höger och "Risk" ett steg åt vänster
    sliders = page.locator(".st-key-bt_maxpct [role='slider']")
    sliders.first.focus()
    for _ in range(5):
        page.keyboard.press("ArrowRight")
    time.sleep(2)
    risk = page.locator(".st-key-sb_risk [role='slider']")
    risk.first.focus()
    page.keyboard.press("ArrowLeft")
    time.sleep(3)
    page.screenshot(path=str(OUT / "2_andrat.png"), full_page=False)
    b.close()
print("klart", list(OUT.iterdir()))
