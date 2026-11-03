from trading_platform.config import Settings
from trading_platform.scanner import scan
from trading_platform.strategies import day, entry_signals, swing


def test_swing_finds_candidates_over_time(bars):
    """Gamla swing-villkoren gav nästan aldrig någon signal."""
    total = sum(int(entry_signals(swing, bars(1500, seed=s))[1].sum()) for s in range(5))
    assert total > 20


def test_evaluate_with_too_little_data(bars):
    assert swing.evaluate("X", bars(100))["signal"] == "INSUFFICIENT_DATA"
    assert day.evaluate("X", None)["signal"] == "INSUFFICIENT_DATA"


def test_scanner_adds_position_size_in_usd_and_sek(provider):
    rows = scan(["aapl", "MSFT", "AAPL"], provider=provider(rate=10.0), settings=Settings())
    assert {r["symbol"] for r in rows} == {"AAPL", "MSFT"}  # dubbletter tas bort
    for r in rows:
        assert r["strategy"] in ("day", "swing")
        assert r["shares"] > 0  # delaktier gör att även litet kapital räcker
        assert abs(r["position_sek"] - r["position_usd"] * 10) <= 1
        # risken får aldrig överstiga vald risk (0,5 % av 5 000 kr = 25 kr)
        assert r["risk_sek"] <= 25 + 0.5


def test_scanner_low_liquidity_filter(provider, bars):
    thin = bars(600)
    thin["Volume"] = 100  # nästan ingen handel
    rows = scan(["THIN"], provider=provider({"THIN": thin}), settings=Settings())
    assert {r["signal"] for r in rows} == {"LOW_LIQUIDITY"}


def test_scanner_reports_data_errors(provider):
    class Broken(provider):
        def history(self, *a):
            raise RuntimeError("nätverksfel")

    rows = scan(["AAPL"], provider=Broken(), settings=Settings())
    assert {r["signal"] for r in rows} == {"DATA_ERROR"}


def test_swing_never_buys_when_rsi_or_macd_falling(bars):
    """Användarens regel: köp aldrig när RSI eller MACD är på väg ner,
    och bara efter bekräftad botten (kursen har vänt upp)."""
    for seed in range(5):
        x, sig = entry_signals(swing, bars(1500, seed=seed))
        b = x[sig]
        prev = x.shift(1)[sig]
        prev2 = x.shift(2)[sig]
        assert (b.RSI14 > prev.RSI14).all() and (prev.RSI14 > prev2.RSI14).all()
        assert (b.MACD > prev.MACD).all() and (b.MACD_HIST > prev.MACD_HIST).all()
        assert (b.Close > prev.High).all()
