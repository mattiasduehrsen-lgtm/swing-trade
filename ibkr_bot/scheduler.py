from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pandas_market_calendars as mcal
import structlog

from .executor import Executor
from .state import StateStore
from .strategy import ET, SpyWeeklyStrategy


log = structlog.get_logger(__name__)
NYSE = mcal.get_calendar("NYSE")

TICK_SEC = 30


class Scheduler:
    def __init__(
        self,
        strategy: SpyWeeklyStrategy,
        executor: Executor,
        state: StateStore,
    ) -> None:
        self.strategy = strategy
        self.executor = executor
        self.state = state
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            now = datetime.now(tz=ET)
            session = self._current_session(now)
            if session is None:
                await self._sleep_until_next_open(now)
                continue

            open_et, close_et = session
            if now < open_et:
                await self._sleep_until(open_et)
                continue
            if now >= close_et:
                # Market closed for the day; jump to next session.
                await self._sleep_until_next_open(now + timedelta(minutes=1))
                continue

            await self._tick(now)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SEC)
                return
            except asyncio.TimeoutError:
                continue

    async def _tick(self, now: datetime) -> None:
        try:
            pos = self.state.get_position(self.strategy.symbol)
            if pos is None:
                signal = self.strategy.should_enter(now, None)
            else:
                signal = self.strategy.should_exit(now, pos)
            if signal is not None:
                log.info(
                    "scheduler.signal",
                    side=signal.side, symbol=signal.symbol, qty=signal.qty,
                    reason=signal.reason,
                )
                await self.executor.execute(signal)
        except Exception as e:  # noqa: BLE001 — don't let one tick kill the loop
            log.exception("scheduler.tick.error", error=str(e))

    def _current_session(self, now: datetime) -> tuple[datetime, datetime] | None:
        d = now.date()
        sched = NYSE.schedule(start_date=d.isoformat(), end_date=d.isoformat())
        if sched.empty:
            return None
        row = sched.iloc[0]
        return (
            row["market_open"].tz_convert(ET).to_pydatetime(),
            row["market_close"].tz_convert(ET).to_pydatetime(),
        )

    async def _sleep_until(self, target: datetime) -> None:
        now = datetime.now(tz=ET)
        delta = (target - now).total_seconds()
        if delta <= 0:
            return
        log.info("scheduler.sleep", until=target.isoformat(), seconds=int(delta))
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delta)
        except asyncio.TimeoutError:
            pass

    async def _sleep_until_next_open(self, after: datetime) -> None:
        # Look up to 14 days ahead for the next open.
        start = after.date()
        sched = NYSE.schedule(
            start_date=start.isoformat(),
            end_date=(start + timedelta(days=14)).isoformat(),
        )
        for _, row in sched.iterrows():
            open_et = row["market_open"].tz_convert(ET).to_pydatetime()
            if open_et > after:
                await self._sleep_until(open_et)
                return
        log.warning("scheduler.no_open_found", start=start.isoformat())
        await self._sleep_until(after + timedelta(hours=1))
