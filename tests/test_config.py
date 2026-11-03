import json

import pytest

from trading_platform.config import Settings, load_settings
from trading_platform.risk import commission, position_size


def test_project_config_loads():
    s = load_settings()
    assert s.day_capital + s.swing_capital == s.total_capital


def test_live_mode_is_blocked(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"mode": "live"}))
    with pytest.raises(ValueError):
        load_settings(p)


def test_unknown_config_keys_are_ignored(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"total_capital": 20000, "something_new": 1}))
    assert load_settings(p).total_capital == 20000


def test_fractional_position_size():
    qty = position_size(500, 100, 95, 0.5, 25, fractional=True)
    assert qty == 0.5  # 2,50 USD risk / 5 USD per aktie
    assert position_size(500, 100, 95, 0.5, 25) == 0  # utan delaktier blir det 0


def test_commission_minimum_and_cap():
    assert commission(10, price=100) == 1.0  # minimum 1 USD
    assert commission(0.5, price=100) == 0.5  # högst 1 % av affärsvärdet
    assert commission(0, price=100) == 0.0
    assert Settings().min_commission_usd == 1.0
