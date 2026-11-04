"""Automatiskt test av HELA webbsidan (körs på GitHub, kräver streamlit).

Klickar igenom sidan som en användare: varje reglage till lägsta och högsta värde,
varje val i varje lista, varje kryssruta och varje knapp – och kontrollerar att
sidan aldrig visar ett fel. Använder påhittade kurser (TP_FAKE_DATA=1), så inget
internet behövs och testet går att upprepa.
"""
import os
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 600
# Knappar som raderar eller laddar upp – hoppas över (de testas inte här).
SKIP_BUTTONS = ("Börja om", "Återställ från filen")


@pytest.fixture(autouse=True)
def fake_data(monkeypatch, tmp_path):
    monkeypatch.setenv("TP_FAKE_DATA", "1")
    monkeypatch.chdir(ROOT)


def _new(page="app.py"):
    at = AppTest.from_file(str(ROOT / page), default_timeout=TIMEOUT)
    at.run()
    _ok(at, "start")
    return at


def _ok(at, what):
    errs = [e.value for e in at.exception]
    errs += [e.value for e in at.error if "Traceback" in str(e.value) or "Error" in str(e.value)]
    assert not errs, f"Fel efter: {what}\n" + "\n".join(map(str, errs))[:3000]


def _ident(w):
    return (type(w).__name__, getattr(w, "key", None), getattr(w, "label", None))


def _find(at, ident):
    kind, key, label = ident
    group = {"Slider": at.slider, "SelectSlider": at.select_slider, "NumberInput": at.number_input,
             "Selectbox": at.selectbox, "Radio": at.radio, "Checkbox": at.checkbox, "Toggle": at.toggle,
             "Button": at.button}[kind]
    for w in group:
        if (key and getattr(w, "key", None) == key) or (not key and w.label == label):
            return w
    return None


def _widgets(at):
    out = []
    for group in (at.slider, at.select_slider, at.number_input, at.selectbox, at.radio, at.checkbox):
        for w in group:
            if not getattr(w, "disabled", False):
                out.append(_ident(w))
    return list(dict.fromkeys(out))


def _values(w):
    kind = type(w).__name__
    if kind == "Slider":
        return [w.min, w.max, w.value]
    if kind == "NumberInput":
        lo = w.min if w.min is not None else w.value
        return [lo, (w.value or 0) * 10, w.value]
    if kind in ("SelectSlider", "Selectbox", "Radio"):
        # Index i listan (visade texter kan vara omformaterade, t.ex. "5:e minut").
        n = len(list(w.options))
        cur = getattr(w, "index", None)
        return [("idx", i) for i in range(n)] + ([("idx", cur)] if cur is not None else [])
    if kind == "Checkbox":
        return [not w.value, w.value]
    return []


def _set(w, v):
    kind = type(w).__name__
    if kind == "Checkbox":
        w.check() if v else w.uncheck()
    elif isinstance(v, tuple) and v and v[0] == "idx":
        i = v[1]
        if kind == "Selectbox":
            w.select_index(i)
        else:  # Radio / SelectSlider: välj värdet på plats i
            opts = list(w.options)
            vals = getattr(w, "_original_options", None) or opts
            w.set_value(vals[i] if kind == "Radio" else _raw_option(w, i))
    else:
        w.set_value(v)


def _raw_option(w, i):
    """SelectSlider: underliggande värde på plats i (t.ex. 0.5 för risk)."""
    opts = list(w.options)
    try:
        return type(w.value)(opts[i])
    except Exception:
        return opts[i]


def test_every_setting_on_every_tab():
    """Varje reglage, lista och kryssruta på huvudsidan: alla värden, ett i taget."""
    at = _new()
    for ident in _widgets(at):
        w = _find(at, ident)
        if w is None:
            continue
        for v in _values(w):
            w = _find(at, ident)
            if w is None or getattr(w, "disabled", False):
                break
            try:
                _set(w, v)
            except Exception:
                continue  # t.ex. värde utanför tillåtet – Streamlit tillåter det inte heller i webbläsaren
            at.run()
            _ok(at, f"{ident} = {v!r}")


def test_all_settings_at_extremes_together():
    """Alla reglage på lägsta värde samtidigt, sedan alla på högsta."""
    for pick in (min, max):
        at = _new()
        for ident in _widgets(at):
            w = _find(at, ident)
            if w is None or type(w).__name__ not in ("Slider", "SelectSlider"):
                continue
            vals = [v for v in _values(w) if v is not None]
            try:
                if type(w).__name__ == "Slider":
                    _set(w, pick([w.min, w.max]))
                else:
                    _set(w, ("idx", 0 if pick is min else len(list(w.options)) - 1))
            except Exception:
                continue
        at.run()
        _ok(at, f"alla reglage på {'lägsta' if pick is min else 'högsta'}")


def test_every_button():
    """Varje knapp på huvudsidan, med standardinställningar och med extrema inställningar."""
    for extreme in (False, True):
        at = _new()
        if extreme:  # 100 % daytrading och 0 % – fallen som har kraschat förut
            for s in at.slider:
                if s.label.startswith("Andel till daytrading") and not s.disabled:
                    s.set_value(100 if s.key and s.key.startswith("bt") else 0)
            at.run()
            _ok(at, "extrema andelar")
        labels = list(dict.fromkeys(b.label for b in at.button if not b.disabled))
        for label in labels:
            if any(x in label for x in SKIP_BUTTONS):
                continue
            b = next((b for b in at.button if b.label == label and not b.disabled), None)
            if b is None:
                continue
            b.click()
            at.run()
            _ok(at, f"knappen '{label}' (extrema={extreme})")


def test_diagram_page_all_choices():
    at = _new("pages/1_📊_Diagram.py")
    for ident in _widgets(at):
        w = _find(at, ident)
        for v in (_values(w) if w is not None else []):
            w = _find(at, ident)
            if w is None:
                break
            _set(w, v)
            at.run()
            _ok(at, f"Diagram: {ident} = {v!r}")
