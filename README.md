# trading_bot_vsa

## Reports and run history

Every backtest is saved to MongoDB (`backtest_runs` collection) with its symbol, timeframe,
dates, the full strategy parameter set, overall metrics, the session/day breakdown, trades and
equity curve. The left sidebar on the dashboard lists saved runs; click one to reload its
parameters and report. Runs can be renamed or deleted from the sidebar.

Reports are segregated by trading session, weekday and calendar day, and can be filtered by
entry date range, time-of-day range (UTC, wraps past midnight), sessions and weekdays.

Sessions (UTC, set in `config.py` → `TRADING_SESSIONS`):

| Session  | Hours (UTC)   |
|----------|---------------|
| Asian    | 00:00 – 09:00 |
| London   | 07:00 – 16:00 |
| New York | 12:00 – 21:00 |

Trades are bucketed by entry time. Sessions overlap, so a trade in an overlap counts toward
both sessions; trades between 21:00 and 24:00 UTC show up as "Off hours".

### Configuration (`.env` next to `main.py`, optional)

```
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB=vsa_wyckoff_bot
BROKER_UTC_OFFSET_HOURS=0   # MT5 server time minus UTC, e.g. 2 for a UTC+2 broker
```
