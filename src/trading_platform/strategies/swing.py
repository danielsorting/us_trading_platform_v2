from ..indicators import enrich
import pandas as pd

def evaluate(symbol, bars):
    if bars is None or len(bars)<220: return {"symbol":symbol,"strategy":"swing","signal":"INSUFFICIENT_DATA"}
    x=enrich(bars.dropna())
    a,b=x.iloc[-1],x.iloc[-2]
    checks={
      "long trend": bool(a.EMA50>a.EMA200 and a.Close>a.EMA50),
      "RSI recovery": bool(b.RSI14<=40 and 40<a.RSI14<65),
      "MACD improving": bool(a.MACD_HIST>b.MACD_HIST),
      "volume confirmation": bool(a.Volume>=a.VOL20),
    }
    return {"symbol":symbol,"strategy":"swing","signal":"CANDIDATE" if all(checks.values()) else "WATCH",
      "price":round(float(a.Close),2),"rsi":round(float(a.RSI14),1),
      "atr":round(float(a.ATR14),3),"stop_reference":round(float(a.Close-2*a.ATR14),2),
      "checks":checks}
