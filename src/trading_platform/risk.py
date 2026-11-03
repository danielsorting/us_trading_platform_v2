"""Riskhantering: positionsstorlek och handelskostnader."""
import math


def position_size(strategy_capital, price, stop_price, risk_pct=0.5, max_position_pct=25, fractional=False):
    """Share quantity from risk budget and notional cap.

    Kapital, pris och stop måste vara i samma valuta (USD för USA-aktier).
    - Risk: om stop loss slår till förloras högst risk_pct % av kapitalet.
    - Tak: positionen får högst vara max_position_pct % av kapitalet.
    - fractional=True tillåter delaktier (avrundat nedåt till 4 decimaler).
    """
    if price <= 0 or stop_price <= 0 or stop_price >= price:
        return 0
    risk_cash = strategy_capital * risk_pct / 100
    per_share_risk = price - stop_price
    max_notional = strategy_capital * max_position_pct / 100
    if fractional:
        qty = min(risk_cash / per_share_risk, max_notional / price)
        return max(0.0, math.floor(qty * 10_000) / 10_000)
    by_risk = int(risk_cash // per_share_risk)
    by_notional = int(max_notional // price)
    return max(0, min(by_risk, by_notional))


def commission(quantity, per_share=0.005, minimum=1.0, price=None, pct=0.0, fx_pct=0.0):
    """Courtage i USD för en order.

    Två modeller som kan kombineras:
    - Per aktie med minimum (IBKR Fixed: 0,005 USD/aktie, minst 1 USD, högst 1 % av beloppet).
    - Procent av beloppet (t.ex. Avanza: pct=0,04 betyder 0,04 %).
    fx_pct = valutaväxlingsavgift i % av beloppet (0 om du har dollarkonto).
    """
    if quantity <= 0:
        return 0.0
    value = quantity * price if price else 0.0
    fee = quantity * per_share + value * (pct + fx_pct) / 100
    fee = max(minimum, fee)
    if price and per_share > 0 and pct == 0:
        fee = min(fee, 0.01 * value)
    return round(fee, 4)


def fee_kwargs(settings):
    """Courtage-inställningarna från Settings som argument till commission()."""
    return {"per_share": settings.commission_per_share_usd, "minimum": settings.min_commission_usd,
            "pct": getattr(settings, "commission_pct", 0.0), "fx_pct": getattr(settings, "fx_fee_pct", 0.0)}


def fees_too_high(qty, price, stop, target, per_share=0.005, minimum=1.0, slippage_pct=0.05, max_share_pct=20.0,
                  pct=0.0, fx_pct=0.0):
    """Kostnadsfilter: True om courtage + slippage tur och retur är större än
    max_share_pct % av den förväntade vinsten.

    Förväntad vinst = (kursmål − pris) × antal. Saknas kursmål (följande stop)
    används 2 × risken som uppskattning."""
    if qty <= 0 or price <= 0:
        return True
    gain_per_share = (target - price) if target and target > price else 2 * max(price - stop, 0)
    expected = qty * gain_per_share
    costs = 2 * commission(qty, per_share, minimum, price, pct, fx_pct) + 2 * qty * price * slippage_pct / 100
    return expected <= 0 or costs > expected * max_share_pct / 100


def sek_to_usd(amount_sek, usd_sek_rate):
    return amount_sek / usd_sek_rate


def usd_to_sek(amount_usd, usd_sek_rate):
    return amount_usd * usd_sek_rate
