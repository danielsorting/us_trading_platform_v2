from .data import YahooResearchProvider
from .strategies import day, swing

def scan(symbols, include_day=True, include_swing=True, provider=None):
    provider=provider or YahooResearchProvider()
    results=[]
    for i,symbol in enumerate(symbols,1):
        if include_swing:
            try:
                bars=provider.history(symbol,"2y","1d")
                results.append(swing.evaluate(symbol,bars))
            except Exception as e:
                results.append({"symbol":symbol,"strategy":"swing","signal":"DATA_ERROR","reason":str(e)[:160]})
        if include_day:
            try:
                bars=provider.history(symbol,"5d","5m")
                results.append(day.evaluate(symbol,bars))
            except Exception as e:
                results.append({"symbol":symbol,"strategy":"day","signal":"DATA_ERROR","reason":str(e)[:160]})
    return results
