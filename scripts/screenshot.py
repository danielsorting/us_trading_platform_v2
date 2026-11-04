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
    (OUT / "page.html").write_text(page.content())
    # Klasser runt reglagen (för att kunna styla dem)
    info = page.evaluate("""() => Array.from(document.querySelectorAll("[role='slider']")).map(s => {
        let chain = []; let e = s;
        for (let i = 0; i < 9 && e; i++) { chain.push(e.tagName + '.' + (e.className || '').toString().slice(0, 120)
            + ' ' + (e.getAttribute('data-baseweb') || '') + ' ' + (e.getAttribute('data-testid') || ''));
            e = e.parentElement; }
        return chain.join('  <  ');
    })""")
    (OUT / "sliders.txt").write_text("\n\n".join(info))
    # Flytta "Max antal positioner" tre steg åt vänster och "Max storlek per swingaffär" fem steg åt höger
    print("\n".join(info)[:6000])
    thumbs = page.locator("[role='slider']")
    print("antal reglage:", thumbs.count())
    for idx, key, n in ((4, "ArrowLeft", 3), (5, "ArrowRight", 5)):
        try:
            t = thumbs.nth(idx)
            t.scroll_into_view_if_needed(timeout=5000)
            t.click(timeout=5000)
            for _ in range(n):
                page.keyboard.press(key)
            time.sleep(2)
        except Exception as e:
            print("kunde inte flytta reglage", idx, type(e).__name__)
    time.sleep(3)
    page.screenshot(path=str(OUT / "2_andrat.png"), full_page=False)
    b.close()
print("klart", list(OUT.iterdir()))
