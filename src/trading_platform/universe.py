import io
import requests
import pandas as pd

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

def _read_pipe(url):
    response = requests.get(url, timeout=30, headers={"User-Agent":"USTradingPlatform/0.2"})
    response.raise_for_status()
    frame = pd.read_csv(io.StringIO(response.text), sep="|")
    return frame

def load_us_equity_symbols(include_etfs=False):
    """Loads current Nasdaq Trader symbol-directory entries; not a guarantee of tradability."""
    a = _read_pipe(NASDAQ_LISTED)
    b = _read_pipe(OTHER_LISTED)
    a = a[a["Symbol"].notna() & ~a["Symbol"].astype(str).str.contains("File Creation", na=False)]
    b = b[b["ACT Symbol"].notna() & ~b["ACT Symbol"].astype(str).str.contains("File Creation", na=False)]
    rows = pd.concat([
        pd.DataFrame({"symbol":a["Symbol"], "name":a.get("Security Name",""), "etf":a.get("ETF","N")}),
        pd.DataFrame({"symbol":b["ACT Symbol"], "name":b.get("Security Name",""), "etf":b.get("ETF","N")})
    ], ignore_index=True)
    rows["symbol"] = rows["symbol"].astype(str).str.strip()
    rows = rows[rows["symbol"].str.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", na=False)]
    rows = rows.drop_duplicates("symbol")
    if not include_etfs:
        rows = rows[rows["etf"].fillna("N").astype(str).str.upper().ne("Y")]
    return rows.sort_values("symbol").reset_index(drop=True)

def get_symbols(mode, watchlist, include_etfs=False):
    if mode == "watchlist":
        return pd.DataFrame({"symbol": sorted(set(s.upper().strip() for s in watchlist if s.strip()))})
    if mode == "us_market":
        return load_us_equity_symbols(include_etfs)
    raise ValueError("scan mode must be watchlist or us_market")
