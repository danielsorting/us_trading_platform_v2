import numpy as np
import pandas as pd

def ema(s, span): return s.ewm(span=span, adjust=False).mean()

def rsi(close, period=14):
    d=close.diff(); gain=d.clip(lower=0); loss=-d.clip(upper=0)
    ag=gain.ewm(alpha=1/period,min_periods=period,adjust=False).mean()
    al=loss.ewm(alpha=1/period,min_periods=period,adjust=False).mean()
    rs=ag/al.replace(0,np.nan)
    return (100-100/(1+rs)).fillna(50)

def atr(df, period=14):
    pc=df.Close.shift(1)
    tr=pd.concat([df.High-df.Low,(df.High-pc).abs(),(df.Low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/period,min_periods=period,adjust=False).mean()

def enrich(df):
    x=df.copy()
    x["EMA9"]=ema(x.Close,9); x["EMA20"]=ema(x.Close,20)
    x["EMA50"]=ema(x.Close,50); x["EMA200"]=ema(x.Close,200)
    x["RSI14"]=rsi(x.Close)
    m=ema(x.Close,12)-ema(x.Close,26); sig=ema(m,9)
    x["MACD_HIST"]=m-sig; x["ATR14"]=atr(x)
    x["VOL20"]=x.Volume.rolling(20).mean()
    x["DOLLAR_VOL20"]=(x.Close*x.Volume).rolling(20).mean()
    return x
