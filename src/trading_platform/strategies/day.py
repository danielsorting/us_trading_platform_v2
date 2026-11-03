from ..indicators import enrich

def evaluate(symbol, bars):
    # Intended for 5-minute candles during regular US market hours.
    if bars is None or len(bars)<60: return {"symbol":symbol,"strategy":"day","signal":"INSUFFICIENT_DATA"}
    x=enrich(bars.dropna())
    a,b=x.iloc[-1],x.iloc[-2]
    checks={
      "intraday trend": bool(a.Close>a.EMA20 and a.EMA9>a.EMA20),
      "momentum": bool(a.MACD_HIST>b.MACD_HIST),
      "RSI strength": bool(50<a.RSI14<75),
      "relative volume": bool(a.Volume>a.VOL20),
    }
    return {"symbol":symbol,"strategy":"day","signal":"CANDIDATE" if all(checks.values()) else "WATCH",
      "price":round(float(a.Close),2),"rsi":round(float(a.RSI14),1),
      "atr":round(float(a.ATR14),3),"stop_reference":round(float(a.Close-1.5*a.ATR14),2),
      "checks":checks}
