from trading_platform.risk import position_size

def test_position_size_respects_risk_and_notional_caps():
    qty=position_size(5000,100,98,0.5,25)
    assert qty==12
    assert qty*2 <= 25
