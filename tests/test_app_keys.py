"""Snabbt test (körs överallt): inga två reglage på webbsidan får ha samma interna namn (key).
Samma namn två gånger kraschar sidan (StreamlitDuplicateElementKey)."""
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL_FIELDS = re.findall(r'k\("([a-z_]+)"\)', (ROOT / "app.py").read_text())
PANEL_PREFIXES = re.findall(r'(?:tab_settings|settings_panel)\([^)]*?"(rb|scan|bt|sim)"',
                            (ROOT / "app.py").read_text())


def test_no_duplicate_widget_keys():
    for page in [ROOT / "app.py", *sorted((ROOT / "pages").glob("*.py"))]:
        src = page.read_text()
        keys = re.findall(r'key="([^"]+)"', src)
        if page.name == "app.py":
            keys += [f"{p}_{f}" for p in set(PANEL_PREFIXES) for f in set(PANEL_FIELDS)]
        dup = [k for k, n in Counter(keys).items() if n > 1]
        assert not dup, f"{page.name}: samma key används flera gånger: {dup}"
