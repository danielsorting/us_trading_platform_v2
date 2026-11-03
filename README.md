# US Trading Platform v0.3

En modulär plattform för att **skanna, backtesta och simulera handel** med amerikanska aktier.
Skriven i Python med ett dashboard i Streamlit.

> ⚠️ **Inga riktiga order skickas.** Riktig handel är avstängd i koden (`mode` får bara vara
> `scan_only` eller `paper`). Det här är ett verktyg för forskning och övning – ingen
> garanti för vinst och ingen finansiell rådgivning.

## Vad som finns

| Del | Vad den gör |
|---|---|
| **Scanner** | Bedömer aktier med strategierna och visar köpkandidater med stop loss, kursmål, antal aktier och risk i USD och SEK. Filtrerar bort aktier med för låg omsättning. |
| **Swingtrading** | Dagsdata. Köper rekyler i en upptrend (SMA50/SMA200) först när vändningen är **bekräftad**: RSI stiger två dagar i rad, MACD-linjen och histogrammet stiger, inga nya bottnar och stängning över gårdagens högsta kurs. Köper aldrig när RSI eller MACD är på väg ner. Stop = pris − 2×ATR, mål = 2R, max 20 dagar. |
| **Daytrading** | 5-minutersdata. Momentum (EMA9/EMA20, stigande MACD, stigande RSI 50–75, volym). Stop = pris − 1,5×ATR, mål = 2R, stängs senast vid dagens slut. |
| **Riskhantering** | Positionsstorlek så att du förlorar högst vald risk om stop loss slår till. Stöd för delaktier. Växlar mellan SEK och USD. |
| **Jämför strategier** | Fem swing-varianter (grund, marknadsfilter, relativ styrka, mål 3R, fast +2 %/−2 %) testas på samma aktier. Perioden delas i testdel (70 %) och kontrolldel (30 %) för att avslöja tur. |
| **Backtest** | Historiskt test utan framtidsinformation: köp på stapeln efter signalen, gap-hantering, courtage och slippage, gemensam kassa och max antal positioner. Jämför med "köp och behåll". |
| **Simulerad handel** | Papper-konto med låtsaspengar. En *handelsrunda* säljer positioner som nått stop/mål/tidsgräns och köper nya kandidater. Kontot sparas i `paper_account.json`. |
| **Dashboard** | Fyra flikar: Scanner, Backtest, Simulerad handel och Om. |

## Starta dashboarden på nätet (rekommenderas för gamla Mac-datorer)

Streamlit Community Cloud kör appen gratis direkt från GitHub. Inget behöver installeras på din dator.

1. Gå till **share.streamlit.io** och logga in med ditt GitHub-konto.
2. Klicka **Create app** → **Deploy a public app from GitHub** (privata arkiv fungerar också).
3. Välj arkivet `danielsorting/us_trading_platform_v2`, grenen `main` och filen `app.py`.
4. Klicka **Deploy**. Efter några minuter öppnas appen i webbläsaren.

Varje gång ny kod sparas på GitHub uppdateras appen automatiskt.

**Bra att veta om webbversionen:** det simulerade kontot kan nollställas när appen startas om.
Använd *Ladda ner kontot* under fliken Simulerad handel ibland, så kan du ladda upp det igen.

## Köra på egen dator

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Köra testerna

```bash
pip install pytest
python -m pytest
```

## Inställningar (`config.json`)

| Inställning | Betydelse |
|---|---|
| `total_capital` | Totalt kapital i SEK |
| `day_allocation_pct` / `swing_allocation_pct` | Fördelning mellan strategierna (summa 100) |
| `risk_per_trade_pct` | Max förlust per affär i % av strategins kapital |
| `max_positions_per_strategy` | Max antal samtidiga positioner per strategi |
| `max_position_pct_of_strategy_capital` | Max storlek på en position |
| `allow_fractional_shares` | Tillåt delaktier (t.ex. 0,35 aktie) |
| `minimum_average_daily_dollar_volume` | Minsta genomsnittliga omsättning per dag (USD) |
| `usd_sek_fallback` | Växelkurs som används om aktuell kurs inte kan hämtas |
| `commission_per_share_usd`, `min_commission_usd` | Courtage (liknar Interactive Brokers "Fixed") |
| `slippage_pct` | Antagen prisförsämring per köp/sälj |
| `watchlist` | Din bevakningslista |
| `mode` | `scan_only` eller `paper`. Riktig handel är avstängd. |

## Projektets delar

- `app.py` – dashboard
- `src/trading_platform/config.py` – inställningar
- `src/trading_platform/data.py` – kursdata (Yahoo Finance, utbytbar)
- `src/trading_platform/indicators.py` – SMA, EMA, RSI, MACD, ATR
- `src/trading_platform/strategies/` – `swing.py`, `day.py` (samma regler i scanner och backtest)
- `src/trading_platform/risk.py` – positionsstorlek, courtage, valuta
- `src/trading_platform/scanner.py` – skanning
- `src/trading_platform/backtest.py` – backtesting
- `src/trading_platform/paper_trader.py` – handelsrunda för simulerad handel
- `src/trading_platform/brokers/` – mäklargränssnitt och papper-mäklare
- `src/trading_platform/universe.py` – lista över USA-aktier (Nasdaq Trader)
- `tests/` – automatiska tester

## Viktiga begränsningar

- **Kursdata:** Yahoo Finance är gratis men kan vara fördröjd, ofullständig och begränsa många anrop.
  5-minutersdata finns bara cirka 60 dagar bakåt. Inte lämpligt för riktig handel.
- **Litet kapital:** courtaget (ca 1 USD per köp och sälj, högst 1 % av affären) kan äta en stor
  del av varje affär när risken per affär bara är några dollar. Backtestet visar hur mycket.
- **Backtest ≠ framtid:** testa många aktier och perioder och jämför med "köp och behåll".
- **Överlevnadsbias:** dagens aktielistor saknar bolag som försvunnit, vilket kan försköna historiska tester.

## Nästa steg

1. Simulerad handel under en längre period (veckor–månader) och utvärdering.
2. Förbättra strategierna utifrån backtester.
3. Bättre datakälla med realtidskurser.
4. Interactive Brokers-koppling – först mot IBKR:s pappershandelskonto, med dubbelorder-skydd,
   nödstopp, loggning och marknadskalender. Riktiga pengar sist av allt.
