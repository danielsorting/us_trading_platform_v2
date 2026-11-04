# Koppla roboten till Interactive Brokers (IBKR)

Koppla roboten till IBKR först när den har slagit S&P 500 på låtsaspengar i några veckor.

## Säkerhet: det här är inbyggt

- **Bara låtsaskonto.** Koden handlar bara på konton vars nummer börjar med `DU`, vilket är IBKR:s låtsaskonton. Ansluter roboten till ett konto med riktiga pengar kopplar den ned direkt och vägrar handla.
- **Nödstopp.** Skapa en tom fil som heter `STOPP` i projektmappen. Då gör roboten inga nya köp, men sälj fungerar fortfarande.
- **Nödknapp.** Kommandot `python scripts/ibkr_trade.py --close-all` säljer allt som roboten äger.
- **Gränser.** Det finns ett maxbelopp per köp (2 500 USD), högst 20 köp per dag och en maxförlust per dag (250 USD). Gränserna ändras i `config.json`.
- **Limitorder.** Roboten betalar aldrig mer än 0,3 % över senaste kurs. Fylls ordern inte inom 30 sekunder makuleras den.
- **Kontroll mot IBKR.** Stämmer robotens positioner inte med IBKR:s gör den inga nya köp. Aktier du själv äger i kontot rör roboten inte.
- **Bara hela aktier.** IBKR tillåter inte delaktier via API.

## Steg för steg

1. **Öppna konto hos IBKR** (interactivebrokers.com). Du kan välja ISK. Vänta tills kontot är godkänt.
2. **Hämta ditt låtsaskonto.** Logga in på IBKR:s webbsida och gå till *Settings → Paper Trading Account*. Där får du ett användarnamn för låtsaskontot.
3. **Köp realtidskurser för USA.** Gå till *Settings → Market Data Subscriptions* och välj *US Securities Snapshot and Futures Value Bundle* (ungefär 10 USD i månaden).
4. **Installera IB Gateway** på datorn eller servern som ska köra roboten. Logga in med **låtsaskontots** användarnamn och välj *Paper Trading*.
5. **Slå på API:et i IB Gateway.** Gå till *Configure → Settings → API → Settings*:
   - Kryssa i *Enable ActiveX and Socket Clients*.
   - Kontrollera att porten är **4002**.
   - Kryssa ur *Read-Only API*.
   - Lägg till `127.0.0.1` under *Trusted IPs*.
6. **Installera programmen** på samma dator:
   ```
   git clone https://github.com/danielsorting/us_trading_platform_v2.git
   cd us_trading_platform_v2
   pip install -r requirements.txt -r requirements-ibkr.txt
   ```
7. **Testa anslutningen:**
   ```
   python scripts/ibkr_trade.py --check
   ```
   Det ska stå `✅ Ansluten till IBKR-låtsaskonto DU...`.
8. **Slå på IBKR.** Lägg till raden `"ibkr_enabled": true,` i `config.json`.
9. **Kör en runda:** `python scripts/ibkr_trade.py --once`
10. **Kör hela dagen:** `python scripts/ibkr_trade.py`. Roboten gör en runda var 5:e minut när börsen är öppen.

Robotens bokföring sparas i `paper/ibkr_account.json`, och loggen sparas i `paper/ibkr_log.csv`.

## Bra att veta

- IBKR kräver att du **loggar in i IB Gateway på nytt ungefär en gång i veckan** och godkänner inloggningen i mobilappen IBKR Mobile.
- Roboten hämtar fortfarande kurser från Yahoo för att hitta köpkandidater. Själva köpet och säljet sker hos IBKR till IBKR:s egna priser.
