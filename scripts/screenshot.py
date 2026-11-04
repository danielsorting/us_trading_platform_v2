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
    for key, press, n in (("bt_maxpct", "ArrowLeft", 3), ("bt_maxpos", "ArrowRight", 1), ("sb_risk", "ArrowLeft", 2)):
        try:
            inp = page.locator(f".st-key-{key} input[type='range']").first
            inp.focus(timeout=5000)
            for _ in range(n):
                page.keyboard.press(press)
            time.sleep(3)
        except Exception as e:
            print("kunde inte flytta", key, type(e).__name__, str(e)[:200])
    st = page.evaluate("""() => { const g = document.querySelector(".st-key-bt_maxpct [role='group'] > div");
        if (!g) return 'ingen bana';
        const kids = Array.from(g.children).map(c => { const s = getComputedStyle(c);
            return c.className + ' h=' + s.height + ' top=' + s.top + ' bg=' + s.backgroundImage.slice(0,80) + ' ' + s.backgroundColor; });
        return 'bana h=' + getComputedStyle(g).height + ' | ' + kids.join(' || '); }""")
    print(st)
    time.sleep(3)
    page.screenshot(path=str(OUT / "2_andrat.png"), full_page=False)
    vals = lambda: page.evaluate("""() => Array.from(document.querySelectorAll("[class*='st-key-bt_'] input[type='range'], [class*='st-key-sb_risk'] input[type='range']")).map(i => i.getAttribute('aria-label').slice(0,25) + '=' + i.value)""")
    print("före återställning:", vals())
    try:
        page.get_by_role("button", name="Tillbaka till förval").first.click(timeout=5000)
        time.sleep(4)
        print("efter återställning:", vals())
    except Exception as e:
        print("hittade inte knappen", type(e).__name__)
    page.screenshot(path=str(OUT / "3_aterstallt.png"), full_page=False)
    b.close()
print("klart", list(OUT.iterdir()))
