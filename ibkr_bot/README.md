# ibkr_bot — Phase 0 paper-trading skeleton

A thin, operationally solid skeleton for running a paper-trading bot against
Interactive Brokers via the socket API (`ib_async`).

> **The strategy here is intentionally trivial.** It buys 1 share of SPY at
> Monday 15:55 ET and sells it at Friday 15:55 ET. The point of Phase 0 is to
> shake out connection, reconciliation, persistence, scheduling, and logging
> bugs on a toy strategy. A real strategy replaces `strategy.py` in Phase 1.

## What's here

| File | Responsibility |
|---|---|
| `config.py` | Pydantic settings loaded from `.env`. |
| `logging_config.py` | structlog → rotating JSON file + human stdout. |
| `state.py` | SQLite-backed position/order/trade/run log with reconciliation. |
| `connection.py` | `ib_async` connection manager, exponential backoff, reconcile on reconnect. |
| `strategy.py` | `SpyWeeklyStrategy` with NYSE-calendar-aware entry/exit. |
| `executor.py` | Signal → `MarketOrder`, fills recorded to SQLite, 30s timeout. |
| `scheduler.py` | 30s tick during market hours, sleeps through closed sessions. |
| `main.py` | Entry point; wires everything; SIGINT/SIGTERM graceful shutdown. |

## Key design decisions (locked in before build)

- **Library:** `ib_async` (the maintained community fork of `ib_insync`).
- **Half-days:** fire 5 min before the actual NYSE close, so early-close days
  (e.g., day after Thanksgiving, close 13:00 ET) fire at 12:55 ET automatically.
- **Holidays:** if Monday is a holiday, entry shifts forward to the next open
  day within the same ISO week. If Friday is a holiday, exit shifts backward.
  If the whole week has no open day (unlikely), no trade fires.
- **Reconciliation:** IBKR is the source of truth. On every connect/reconnect
  the local SQLite is overwritten to match broker positions; any diff is logged
  at WARNING so it's visible. Open orders reported by the broker are also logged.
- **DRY_RUN:** when true, strategy runs and signals are logged to the trade log,
  but the executor does not submit orders.

## Prerequisites

1. **IB Gateway** (paper mode) or TWS running locally, API enabled, socket on
   `127.0.0.1:4002` (the IB Gateway paper default). Make sure "Read-Only API"
   is **off** for the paper session if you intend to place orders.
2. **Python 3.11+** (3.9 / 3.10 will not work — the code uses PEP 604 union
   syntax and `zoneinfo`).
3. **uv** (recommended) or plain `pip`.

## Setup

```bash
cd ibkr_bot
cp .env.example .env  # then edit if needed

# Option A: uv (recommended)
uv venv -p 3.11
source .venv/bin/activate
uv pip install -e '.[dev]'

# Option B: plain pip
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Running

From the **parent** directory (so `ibkr_bot` is importable as a package):

```bash
cd ..            # should now be at the directory that contains ibkr_bot/
python -m ibkr_bot.main
```

Stop with Ctrl-C — the bot disconnects cleanly, logs current open positions,
and flushes SQLite.

### DRY_RUN first

Keep `DRY_RUN=true` in your `.env` until you've watched at least one full
Mon→Fri cycle's log output. The executor will log signals without placing
orders.

## Tests

From the `ibkr_bot/` package directory:

```bash
pytest
```

The tests do **not** connect to IBKR. They cover:

- SQLite round-trip + reconciliation diff logic (`test_state.py`).
- Calendar-aware entry/exit with DST, MLK Day, Good Friday, and
  post-Thanksgiving half-day (`test_strategy.py`).
- Executor happy-path fill, cancel, and 30s timeout against a mock IB
  (`test_executor_mock.py`).

## Inspecting state

```bash
sqlite3 data/state.db
sqlite> .tables
sqlite> SELECT * FROM positions;
sqlite> SELECT recorded_at, event, symbol, side, qty, price FROM trade_log ORDER BY id DESC LIMIT 20;
sqlite> SELECT recorded_at, event, message FROM run_log ORDER BY id DESC LIMIT 20;
```

Logs are in `./logs/ibkr_bot.jsonl` (rotated 10MB × 10).

## What this does NOT do (by design)

- No backtesting. That's a later phase.
- No historical-data ingestion (yfinance, Polygon, etc.).
- No dashboard, Telegram, email, or alerting. Just logs + SQLite.
- No REST / Client Portal API — socket API via `ib_async` only.
- No limit orders. The Phase 1 strategy will add those.
