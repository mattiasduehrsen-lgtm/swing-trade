# Phase 0 — Observation Period

**Status:** Deployed and running. Now we wait and watch.
**Duration:** ~2 weeks from deployment date.
**Goal:** Prove operational reliability before trusting the system with a real strategy or real capital.

---

## Why we're doing nothing

The temptation is to start improving the strategy, adding features, or building the backtester. **Resist this.**

Phase 0's entire purpose is to catch operational failures — the things that kill bots in production but never show up in unit tests:

- Gateway disconnects and reconnects
- Laptop reboots and auto-recovery
- State reconciliation after a crash mid-position
- Daily IBKR auto-logout handling
- Scheduler correctness across weekends and holidays

None of these are interesting. All of them are fatal if they fail silently when real money is on the line. The boring SPY weekly strategy exists so we can focus 100% on the plumbing without wondering if a weird result is a strategy bug or an infrastructure bug.

---

## The 2-week plan

### Week 1: Dry-run observation

Config state: `DRY_RUN=true` in `.env`

**What should happen:**
- **Monday ~15:55 ET** — Bot wakes, logs `strategy.should_enter` decision, logs a *would-be* buy order (but does NOT submit it). Scheduler goes back to sleep.
- **Tuesday–Thursday** — Bot sleeps. Occasional log entries for scheduler wake/check cycles.
- **Friday ~15:55 ET** — Bot wakes, checks position (none, since dry-run), logs nothing significant, goes back to sleep.
- **Weekend** — Bot is idle but process should still be alive.

**Daily check:**
```bash
ssh matti@192.168.2.212 "Get-Content C:\Users\matti\Desktop\swing-trade\ibkr_bot\logs\*.log -Tail 30"
```

Look for: no ERROR-level entries, clean scheduler wake/sleep cycles, any reconnects logged cleanly.

### Week 2: Live paper trading

Config change (end of Week 1):
1. SSH in: `ssh matti@192.168.2.212`
2. Edit `.env`: change `DRY_RUN=true` to `DRY_RUN=false`
3. Restart the bot (kill the process; the watchdog will bring it back, or use the restart script)

**What should happen:**
- **Monday ~15:55 ET** — Bot submits a REAL buy order for 1 share of SPY to the paper account. Confirm in IBKR Gateway or Client Portal that the fill happened.
- **Tuesday–Thursday** — Bot holds the position, logs heartbeats, does nothing strategic.
- **Friday ~15:55 ET** — Bot submits a REAL sell order. Confirm fill.
- **Verify in SQLite:**
  ```bash
  ssh matti@192.168.2.212 "sqlite3 C:\Users\matti\Desktop\swing-trade\ibkr_bot\data\state.db \"SELECT * FROM trade_log ORDER BY timestamp DESC LIMIT 10;\""
  ```
  Both the buy and sell should appear with fill prices and timestamps.

---

## Failure modes to actively test

Don't just passively watch — deliberately stress-test these:

### 1. Gateway daily auto-logout
IB Gateway logs out automatically around midnight ET every day. The bot's reconnection logic needs to handle this transparently.

**Test:** Just let it happen naturally. Check logs the next morning for `ib.connect.ok` re-entries. If the bot is in a "disconnected" state for hours without reconnecting, that's a bug.

### 2. Connection drops
Wifi hiccups, laptop sleeps, network glitches.

**Test:** Temporarily disconnect the laptop's wifi for 30 seconds, reconnect. Check logs for graceful reconnect within ~60 seconds.

### 3. Laptop reboot recovery
The Startup folder watchdog should bring Gateway + bot back up automatically.

**Test:** Force a reboot (`Restart-Computer`). After boot, within ~5 minutes, verify:
- IB Gateway is running (Task Manager or `Get-Process`)
- Bot process is running
- Logs show fresh `boot` and `ib.connect.ok` entries

### 4. Weekend idle survival
Bot sleeps from Friday ~16:00 ET to Monday ~09:30 ET (~65 hours).

**Test:** Monday morning, verify the process is still alive and picks up the Monday schedule correctly.

### 5. State reconciliation after crash
**Only test this in Week 2, when real paper positions exist.**

**Test:** On a Wednesday (mid-position):
1. Kill the bot process hard: `Stop-Process -Name python -Force`
2. Wait 30 seconds
3. Restart bot
4. Check logs: bot should detect the existing SPY position from IBKR, reconcile with SQLite state, and NOT try to buy again. It should wait for Friday to sell.

If the bot tries to double-buy or fails to detect the existing position, that's a **critical bug** that must be fixed before Phase 1.

---

## Daily check-in command

Copy-paste this into your terminal each morning (takes 2 seconds):

```bash
ssh matti@192.168.2.212 "Get-Content C:\Users\matti\Desktop\swing-trade\ibkr_bot\logs\*.log -Tail 30"
```

**Things to look for:**
- ✅ `ib.connect.ok` entries (reconnects happening cleanly)
- ✅ `scheduler.sleep` with sensible `until=...` timestamps
- ✅ `reconcile.ok positions=N` after any restart
- ❌ `ERROR` level anything
- ❌ Repeated reconnect attempts without success
- ❌ Scheduler sleeping until dates in the past or far future

---

## Observation checklist

Print this and tick things off as you go. Or copy it into a GitHub issue and update daily.

### Week 1 (Dry-run)
- [ ] Bot process stays alive through the full week
- [ ] Monday 15:55 ET: `should_enter` signal logged with would-be buy order
- [ ] Friday 15:55 ET: no action taken (correct — we're flat)
- [ ] At least one clean Gateway auto-logout + reconnect observed in logs
- [ ] No ERROR-level log entries
- [ ] Laptop reboot test passed (bot auto-recovers)

### Week 2 (Paper live)
- [ ] DRY_RUN flipped to false successfully
- [ ] Monday 15:55 ET: real paper buy order submitted and filled
- [ ] Fill logged in `trade_log` table with correct price and quantity
- [ ] Wednesday mid-week: crash/restart test — bot correctly reconciles existing position
- [ ] Friday 15:55 ET: real paper sell order submitted and filled
- [ ] Position closes cleanly, SQLite reflects flat state
- [ ] P&L in IBKR matches what SQLite `trade_log` shows

### Final Phase 0 gate (must pass all before Phase 1)
- [ ] 2 full weekly cycles completed end-to-end
- [ ] Zero unresolved ERROR logs
- [ ] State reconciliation tested and working
- [ ] Auto-restart tested and working
- [ ] Confident you could leave the bot running for a month without touching it

---

## What NOT to do during Phase 0

- ❌ **Don't add new strategies.** The SPY weekly rule stays.
- ❌ **Don't touch the backtester.** It doesn't exist yet, and that's intentional.
- ❌ **Don't modify the executor, scheduler, or state code** unless fixing a bug found during observation.
- ❌ **Don't deploy real money.** Paper only until Phase 1 is complete.
- ❌ **Don't "optimize" the dry-run strategy.** It's supposed to be boring.
- ❌ **Don't add a web dashboard, Telegram alerts, fancy monitoring,** etc. Not yet.

If you find yourself wanting to improve things, write the ideas down in a `IDEAS.md` file for later. Don't implement them.

---

## When to move to Phase 1

Move to Phase 1 only when:

1. All items in the observation checklist above are ticked ✓
2. You've completed at least 2 successful Monday buy + Friday sell cycles in paper mode
3. You've actively stress-tested the failure modes (crash, reboot, disconnect)
4. You'd be comfortable not touching the bot for 4 weeks

When you're ready, come back with:
- The completed checklist (showing ✓ or ✗ with notes)
- Any log excerpts that looked weird
- Confirmation of the successful paper cycles

Then we'll build Phase 1, which will include:
- **Historical data ingestion** (yfinance → Polygon as needed)
- **VectorBT backtesting framework**
- **A real strategy** (RSI-2 mean reversion or similar, tested on S&P 500 universe)
- **Walk-forward testing** to avoid overfitting
- **Dropping in the new strategy class** via the existing strategy interface (no plumbing changes needed)

---

## Emergency procedures

### Bot is flooding logs with errors
1. SSH in, stop the bot: `Stop-Process -Name python`
2. Read the errors in the log file
3. Fix the issue, commit, push to GitHub
4. Pull on the laptop, restart

### Bot placed a bad order
1. Manually close the position in IBKR Client Portal (paper account, no real money at risk)
2. Stop the bot
3. Check `trade_log` in SQLite vs IBKR positions — reconcile manually if needed
4. Investigate what caused the bad order before restarting

### Gateway won't connect
1. Open Gateway UI on the laptop directly (via Remote Desktop or in person)
2. Log in manually — this often fixes stuck states
3. Verify API settings are still correct (port 4002, API enabled)
4. Restart the bot

---

*Last updated: Phase 0 deployment — let this sit for 2 weeks, do not touch.*
