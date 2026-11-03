def position_size(strategy_capital, price, stop_price, risk_pct=0.5, max_position_pct=25):
    """Share quantity from risk budget and notional cap. Assumes same currency; FX is not handled."""
    if price <= 0 or stop_price <= 0 or stop_price >= price: return 0
    risk_cash = strategy_capital * risk_pct / 100
    per_share_risk = price - stop_price
    by_risk = int(risk_cash // per_share_risk)
    max_notional = strategy_capital * max_position_pct / 100
    by_notional = int(max_notional // price)
    return max(0, min(by_risk, by_notional))
