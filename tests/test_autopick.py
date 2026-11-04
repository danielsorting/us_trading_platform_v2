import json
from datetime import date

from trading_platform import autopick
from trading_platform.config import Settings
from trading_platform.strategies.variants import swing_variants


def test_robot_uses_nightly_best_choice(tmp_path):
    f = tmp_path / "best_choice.json"
    f.write_text(json.dumps({"date": "2026-11-04", "swing_variant": "Utbrott – låt vinnare löpa",
                             "risk_per_trade_pct": 1.0, "swing_liquid_n": 100}))
    base = Settings(auto_pick_best=True, risk_per_trade_pct=2.0, swing_variant="Swing – låt vinnare löpa")
    s, c = autopick.apply(base, swing_variants(), path=f, today=date(2026, 11, 5))
    assert s.swing_variant == "Utbrott – låt vinnare löpa" and s.risk_per_trade_pct == 1.0 and s.swing_liquid_n == 100
    # gammalt val, avstängt eller okänd strategi → oförändrat
    assert autopick.apply(base, swing_variants(), path=f, today=date(2026, 12, 1))[1] is None
    assert autopick.apply(Settings(auto_pick_best=False), swing_variants(), path=f)[1] is None
    f.write_text(json.dumps({"date": "2026-11-04", "swing_variant": "Finns inte", "risk_per_trade_pct": 1.0}))
    assert autopick.apply(base, swing_variants(), path=f, today=date(2026, 11, 5))[1] is None


def test_risk_never_above_two_percent(tmp_path):
    f = tmp_path / "best_choice.json"
    f.write_text(json.dumps({"date": "2026-11-04", "swing_variant": "Swing – grund", "risk_per_trade_pct": 5.0}))
    s, _ = autopick.apply(Settings(auto_pick_best=True), swing_variants(), path=f, today=date(2026, 11, 4))
    assert s.risk_per_trade_pct == 2.0
