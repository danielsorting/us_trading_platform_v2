import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))
import streamlit as st
import pandas as pd
from trading_platform.config import load_settings, Settings
from trading_platform.universe import get_symbols
from trading_platform.scanner import scan

st.set_page_config(page_title="US Trading Platform", layout="wide")
st.title("US Trading Platform")
st.caption("Broker-independent scanner · Day trading + swing trading · Scan-only build")

defaults=load_settings()
with st.sidebar:
    st.header("Capital & risk")
    capital=st.number_input("Total capital (SEK)", min_value=1000, value=int(defaults.total_capital), step=1000)
    day_pct=st.slider("Day trading allocation (%)",0,100,int(defaults.day_allocation_pct),5)
    swing_pct=100-day_pct
    st.metric("Swing allocation", f"{swing_pct}%")
    risk=st.select_slider("Risk per trade (%)", options=[0.25,0.5,0.75,1.0], value=float(defaults.risk_per_trade_pct))
    st.divider()
    mode=st.radio("Universe",["Watchlist","Whole US symbol directory"])
    include_etfs=st.checkbox("Include ETFs",value=defaults.include_etfs)
    strategies=st.multiselect("Strategies",["Day trading","Swing trading"],default=["Day trading","Swing trading"])

st.subheader("Capital allocation")
c1,c2=st.columns(2)
c1.metric("Day trading budget",f"{capital*day_pct/100:,.0f} SEK")
c2.metric("Swing trading budget",f"{capital*swing_pct/100:,.0f} SEK")
st.info("Orders are disabled. This version only scans. Day-trading data availability may be delayed or incomplete; validate the feed before relying on intraday signals.")

if mode=="Watchlist":
    symbols_text=st.text_area("Symbols (comma-separated)",",".join(defaults.watchlist),height=80)
    symbols=[x.strip().upper() for x in symbols_text.split(",") if x.strip()]
else:
    st.warning("The directory is a broad symbol universe, not a guarantee that every security is liquid, supported by the data provider, or tradable in your account.")
    if st.button("Load US symbol directory"):
        try:
            uni=get_symbols("us_market",[],include_etfs)
            st.session_state["us_symbols"]=uni["symbol"].tolist()
            st.success(f"Loaded {len(uni):,} symbols from Nasdaq Trader directories.")
        except Exception as e: st.error(f"Could not load directory: {e}")
    symbols=st.session_state.get("us_symbols",[])
    st.write(f"Loaded symbols: {len(symbols):,}")

if st.button("Run scan",type="primary",disabled=not symbols or not strategies):
    with st.spinner(f"Scanning {len(symbols)} symbols. This can take time, especially for the whole market..."):
        res=scan(symbols, "Day trading" in strategies, "Swing trading" in strategies)
    df=pd.DataFrame(res)
    st.session_state["scan_results"]=df

if "scan_results" in st.session_state:
    df=st.session_state["scan_results"]
    st.subheader("Scanner results")
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button("Download CSV",df.to_csv(index=False),"scan_results.csv","text/csv")
