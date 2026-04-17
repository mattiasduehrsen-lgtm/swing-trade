from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ibkr_bot.state import Position
from ibkr_bot.strategy import (
    ET,
    SpyWeeklyStrategy,
    entry_fire_time,
    exit_fire_time,
)


UTC = ZoneInfo("UTC")


# --- calendar math ---------------------------------------------------------

def test_entry_regular_monday() -> None:
    # 2025-01-13 is a regular open Monday.
    t = entry_fire_time(datetime(2025, 1, 13).date())
    assert t is not None
    assert t.date().isoformat() == "2025-01-13"
    assert (t.hour, t.minute) == (15, 55)


def test_exit_regular_friday() -> None:
    t = exit_fire_time(datetime(2025, 1, 17).date())
    assert t is not None
    assert t.date().isoformat() == "2025-01-17"
    assert (t.hour, t.minute) == (15, 55)


def test_mlk_monday_shifts_to_tuesday() -> None:
    # 2025-01-20 MLK Day (Mon holiday). Entry should shift to Tue 2025-01-21.
    t = entry_fire_time(datetime(2025, 1, 20).date())
    assert t is not None
    assert t.date().isoformat() == "2025-01-21"
    assert (t.hour, t.minute) == (15, 55)


def test_good_friday_exit_shifts_to_thursday() -> None:
    # 2025-04-18 is Good Friday (closed). Exit shifts back to Thu 2025-04-17.
    t = exit_fire_time(datetime(2025, 4, 18).date())
    assert t is not None
    assert t.date().isoformat() == "2025-04-17"
    assert (t.hour, t.minute) == (15, 55)


def test_half_day_fires_five_min_before_early_close() -> None:
    # 2024-11-29 (day after Thanksgiving) — early close at 13:00 ET.
    t = exit_fire_time(datetime(2024, 11, 29).date())
    assert t is not None
    assert t.date().isoformat() == "2024-11-29"
    assert (t.hour, t.minute) == (12, 55)


def test_dst_transition_monday_has_correct_offset() -> None:
    # Monday 2025-03-10 — day after DST starts (2025-03-09). ET = UTC-4.
    t = entry_fire_time(datetime(2025, 3, 10).date())
    assert t is not None
    assert t.utcoffset() == timedelta(hours=-4)

    # Monday 2024-11-04 — day after DST ends (2024-11-03). ET = UTC-5.
    t2 = entry_fire_time(datetime(2024, 11, 4).date())
    assert t2 is not None
    assert t2.utcoffset() == timedelta(hours=-5)


# --- signal behaviour ------------------------------------------------------

@pytest.fixture()
def strat() -> SpyWeeklyStrategy:
    return SpyWeeklyStrategy("SPY", 1)


def test_should_enter_fires_at_target(strat: SpyWeeklyStrategy) -> None:
    now = datetime(2025, 1, 13, 15, 55, 30, tzinfo=ET)
    sig = strat.should_enter(now, None)
    assert sig is not None
    assert sig.side == "BUY" and sig.symbol == "SPY" and sig.qty == 1


def test_should_enter_no_signal_before_target(strat: SpyWeeklyStrategy) -> None:
    now = datetime(2025, 1, 13, 15, 54, 0, tzinfo=ET)
    assert strat.should_enter(now, None) is None


def test_should_enter_no_signal_if_already_long(strat: SpyWeeklyStrategy) -> None:
    now = datetime(2025, 1, 13, 15, 55, 10, tzinfo=ET)
    pos = Position("SPY", 1.0, 500.0)
    assert strat.should_enter(now, pos) is None


def test_should_exit_fires_at_friday_target(strat: SpyWeeklyStrategy) -> None:
    now = datetime(2025, 1, 17, 15, 55, 20, tzinfo=ET)
    sig = strat.should_exit(now, Position("SPY", 1.0, 500.0))
    assert sig is not None
    assert sig.side == "SELL" and sig.qty == 1


def test_should_exit_shifts_to_thursday_on_good_friday(strat: SpyWeeklyStrategy) -> None:
    now = datetime(2025, 4, 17, 15, 55, 5, tzinfo=ET)
    sig = strat.should_exit(now, Position("SPY", 1.0, 500.0))
    assert sig is not None
    assert sig.side == "SELL"


def test_should_exit_no_signal_if_flat(strat: SpyWeeklyStrategy) -> None:
    now = datetime(2025, 1, 17, 15, 55, 0, tzinfo=ET)
    assert strat.should_exit(now, Position("SPY", 0.0, 0.0)) is None


def test_utc_input_converts_to_et(strat: SpyWeeklyStrategy) -> None:
    # 2025-01-13 20:55:30 UTC == 15:55:30 ET (EST, UTC-5).
    now_utc = datetime(2025, 1, 13, 20, 55, 30, tzinfo=UTC)
    sig = strat.should_enter(now_utc, None)
    assert sig is not None
