from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

from .state import Position


ET = ZoneInfo("America/New_York")
NYSE = mcal.get_calendar("NYSE")

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Signal:
    side: Side
    symbol: str
    qty: int
    reason: str


# --- calendar helpers -------------------------------------------------------

def _session_for_day(d: date) -> tuple[datetime, datetime] | None:
    """Return (open, close) datetimes in ET for d, or None if market is closed."""
    sched = NYSE.schedule(start_date=d.isoformat(), end_date=d.isoformat())
    if sched.empty:
        return None
    row = sched.iloc[0]
    # pandas_market_calendars returns UTC timestamps; convert to ET.
    open_et = row["market_open"].tz_convert(ET).to_pydatetime()
    close_et = row["market_close"].tz_convert(ET).to_pydatetime()
    return open_et, close_et


def _week_anchor_day(reference: date, target_weekday: int) -> date:
    """Return the date in `reference`'s week (Mon-based) matching `target_weekday` (0=Mon…4=Fri)."""
    # weekday(): Mon=0 ... Sun=6
    monday = reference - timedelta(days=reference.weekday())
    return monday + timedelta(days=target_weekday)


def _shift_to_open(anchor: date, direction: Literal["forward", "backward"]) -> date | None:
    """Walk forward (Mon-side) or backward (Fri-side) to find the nearest open NYSE day.

    Stays within the same ISO week to avoid jumping into the next/previous week.
    """
    d = anchor
    week_monday = anchor - timedelta(days=anchor.weekday())
    week_friday = week_monday + timedelta(days=4)
    step = 1 if direction == "forward" else -1
    while week_monday <= d <= week_friday:
        if _session_for_day(d) is not None:
            return d
        d += timedelta(days=step)
    return None


def entry_fire_time(ref: date) -> datetime | None:
    """The ET datetime at which entry should fire in ref's week, or None if no day is open."""
    day = _shift_to_open(_week_anchor_day(ref, 0), "forward")
    if day is None:
        return None
    session = _session_for_day(day)
    assert session is not None
    _, close_et = session
    return close_et - timedelta(minutes=5)


def exit_fire_time(ref: date) -> datetime | None:
    """The ET datetime at which exit should fire in ref's week, or None if no day is open."""
    day = _shift_to_open(_week_anchor_day(ref, 4), "backward")
    if day is None:
        return None
    session = _session_for_day(day)
    assert session is not None
    _, close_et = session
    return close_et - timedelta(minutes=5)


# --- strategy ---------------------------------------------------------------

class SpyWeeklyStrategy:
    """Buy 1 SPY at entry-fire, sell it at exit-fire.

    Entry anchor: Monday minus 5 minutes to close. If Monday is a holiday, shift forward
    (Tue, Wed, ...) within the same week. On NYSE early-close days the close is 13:00 ET,
    so the fire time adapts automatically (12:55 ET).

    Exit anchor: Friday minus 5 minutes to close. If Friday is a holiday, shift backward
    within the same week.

    The strategy is side-effect free. It compares the current ET time against the fire
    time and returns a Signal if the current minute matches (within a tolerance window).
    """

    TOLERANCE = timedelta(minutes=1)  # scheduler ticks every 30s, 1 min window is safe

    def __init__(self, symbol: str = "SPY", qty: int = 1) -> None:
        self.symbol = symbol
        self.qty = qty

    def _within(self, now_et: datetime, target: datetime) -> bool:
        return target <= now_et < target + self.TOLERANCE

    def should_enter(
        self, now: datetime, current_position: Position | None
    ) -> Signal | None:
        if current_position is not None and current_position.qty != 0:
            return None
        now_et = now.astimezone(ET)
        target = entry_fire_time(now_et.date())
        if target is None:
            return None
        if self._within(now_et, target):
            return Signal(
                side="BUY",
                symbol=self.symbol,
                qty=self.qty,
                reason=f"weekly entry at {target.isoformat()}",
            )
        return None

    def should_exit(
        self, now: datetime, current_position: Position
    ) -> Signal | None:
        if current_position.qty <= 0:
            return None
        now_et = now.astimezone(ET)
        target = exit_fire_time(now_et.date())
        if target is None:
            return None
        if self._within(now_et, target):
            return Signal(
                side="SELL",
                symbol=self.symbol,
                qty=int(current_position.qty),
                reason=f"weekly exit at {target.isoformat()}",
            )
        return None
