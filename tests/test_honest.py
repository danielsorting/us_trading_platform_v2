import numpy as np
import pandas as pd

from trading_platform import autopick
from trading_platform.config import Settings
from trading_platform.honest import choose, prepare
from trading_platform.strategies.variants import daily_variants


def fake_raw(k=40, n=700, seed=3):
    rng = np.random.default_rng(seed)
    out = {}
    idx = pd.bdate_range("2021-01-01", periods=n)
    for i in range(k):
        c = 30 * np.exp(np.cumsum(rng.normal(0.0008, 0.02, n)))
        v = rng.uniform(1e5, 1e7) * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
        out[f"S{i}"] = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": v}, index=idx)
    return out


def test_honest_choice_includes_yours_and_picks_stable():
    raw = fake_raw()
    prep = prepare(raw, n_values=(20,))
    V = dict(list(daily_variants().items())[:2])
    yours = {"swing_variant": list(V)[1], "risk_per_trade_pct": 2.0}
    s1, s2, best, mine = choose(prep, V, 10_000, Settings(), yours=yours, top=2, lists=3)
    assert len(s1) == 2 * 2 * 2  # strategier × risk × kassa i S&P
    assert mine is not None and mine["Strategi"] == list(V)[1]
    assert best is None or (best["plus_lists"] >= 8 or best["lists"] == 3)


def test_autopick_applies_park(tmp_path):
    import json
    from datetime import date
    f = tmp_path / "c.json"
    v = list(daily_variants())[0]
    f.write_text(json.dumps({"date": date.today().isoformat(), "swing_variant": v, "risk_per_trade_pct": 1.0,
                             "swing_liquid_n": 300, "park_idle_in_index": True}))
    s, c = autopick.apply(Settings(auto_pick_best=True), daily_variants(), path=f)
    assert s.park_idle_in_index is True and s.risk_per_trade_pct == 1.0 and s.swing_variant == v
