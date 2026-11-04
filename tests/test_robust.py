from trading_platform.config import Settings
from trading_platform.robust import robust_score, with_setup
from trading_platform.strategies.variants import swing_variants


def test_robust_score_runs_on_many_lists(bars):
    data = {f"S{i}": bars(600, seed=i) for i in range(30)}
    r = robust_score(data, swing_variants()["Swing – grund"], 10_000, with_setup(Settings(), 1.0, 35), lists=3)
    assert r["lists"] == 3 and r["worst_per_year_pct"] <= r["per_year_pct"] <= r["best_per_year_pct"]
    assert 0 <= r["plus_lists"] <= 3


def test_autopick_applies_max_position(tmp_path):
    import json
    from datetime import date
    from trading_platform import autopick
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"date": "2026-11-04", "swing_variant": "Swing – grund", "risk_per_trade_pct": 2.0,
                             "max_position_pct_of_strategy_capital": 35}))
    s, _ = autopick.apply(Settings(auto_pick_best=True), swing_variants(), path=f, today=date(2026, 11, 4))
    assert s.max_position_pct_of_strategy_capital == 35
