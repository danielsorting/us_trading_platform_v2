# US Trading Platform v2.0

A modular, broker-independent US stock trading research platform for macOS/Linux/Windows.
It supports:
- Whole-market symbol-universe discovery (Nasdaq Trader symbol directory files).
- User watchlists.
- Separate day-trading and swing-trading strategy modules.
- Independent capital allocations and risk budgets.
- Broker adapter interface (paper broker included; live adapters intentionally not enabled).
- Streamlit dashboard for configuration and scanning.

## Important limitations
This is a working research/scanning starter, **not a live-money auto-trader**.
- Whole-market discovery does not mean every stock can be analyzed intraday for free. A market-data provider, entitlements, rate limits, symbol mapping, and costs must be chosen.
- Yahoo Finance via `yfinance` is for research/prototyping; it is not an execution-grade real-time feed.
- No real orders are sent. The included broker is a local paper/mock adapter.
- Before live trading, implement and test a broker-specific adapter using that broker's official API, verify Swedish residency/account eligibility, instrument permissions, currency conversion, fees, and market-data rights.
- Do not place API secrets in source code. Use environment variables or a secrets manager.

## Install on Mac
1. Open Terminal.
2. Check Python: `python3 --version` (Python 3.10+ recommended).
3. Unzip this project and enter its folder:
   `cd ~/Downloads/us_trading_platform_v2`
4. Create and activate an environment:
   `python3 -m venv .venv`
   `source .venv/bin/activate`
5. Install:
   `pip install -r requirements.txt`
6. Launch dashboard:
   `streamlit run app.py`

## First run
- Set mode to scan-only.
- Set capital to SEK 10,000.
- Try a small watchlist first.
- Whole-market symbol discovery reads the public Nasdaq Trader symbol-directory files when you click "Load US universe". You can also import a CSV with a `symbol` column.
- Scanner uses daily candles for swing analysis and a separate intraday interval for day-trading candidates. Intraday coverage depends on the data source and may be delayed.

## Project layout
- `config.py`: capital, allocations, risk and scanner settings.
- `universe.py`: US symbol directory + custom watchlist.
- `data.py`: replaceable market-data provider abstraction.
- `indicators.py`: RSI, MACD, EMA, ATR.
- `strategies/day.py`: intraday momentum candidate logic.
- `strategies/swing.py`: daily trend/pullback candidate logic.
- `risk.py`: position sizing and limits.
- `brokers/base.py`: broker interface.
- `brokers/paper.py`: local paper broker; no real orders.
- `scanner.py`: orchestration.
- `app.py`: dashboard.

## Next development milestones
1. Choose a market-data provider with US-wide coverage and suitable real-time entitlements.
2. Validate symbol mapping, delisted securities, ETFs/ADR filters, and corporate actions.
3. Add robust event-driven backtesting with spreads, slippage, commissions, borrow/short constraints, and no look-ahead.
4. Add official broker adapters one at a time (e.g. Webull/IBKR where eligible).
5. Add order reconciliation, duplicate-order protection, market-hours calendar, disconnect handling, kill switch and audit log.
6. Paper trade for a meaningful period before considering live orders.
