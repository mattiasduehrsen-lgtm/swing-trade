from __future__ import annotations

from pathlib import Path

import pytest

from ibkr_bot.state import Position, StateStore


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


def test_position_round_trip(store: StateStore) -> None:
    assert store.get_position("SPY") is None
    store.upsert_position(Position("SPY", 1.0, 500.12))
    got = store.get_position("SPY")
    assert got == Position("SPY", 1.0, 500.12)

    # Update
    store.upsert_position(Position("SPY", 2.0, 501.00))
    assert store.get_position("SPY") == Position("SPY", 2.0, 501.00)

    # Zero-qty positions are treated as absent.
    store.upsert_position(Position("SPY", 0.0, 0.0))
    assert store.get_position("SPY") is None


def test_order_record_and_update(store: StateStore) -> None:
    store.record_order(1, "SPY", "BUY", 1, "submitted")
    opens = store.open_orders()
    assert len(opens) == 1 and opens[0].order_id == 1

    store.record_order(1, "SPY", "BUY", 1, "Filled", filled_qty=1, avg_fill_price=500.0)
    assert store.open_orders() == []


def test_reconcile_no_diff(store: StateStore) -> None:
    store.upsert_position(Position("SPY", 1.0, 500.0))
    diffs = store.reconcile_positions([Position("SPY", 1.0, 500.0)])
    assert diffs == {}


def test_reconcile_broker_has_extra(store: StateStore) -> None:
    # Local is empty, broker shows a mystery position (e.g. manually opened in TWS).
    diffs = store.reconcile_positions([Position("AAPL", 10.0, 190.0)])
    assert "AAPL" in diffs
    # After reconcile, local matches broker.
    assert store.get_position("AAPL") == Position("AAPL", 10.0, 190.0)


def test_reconcile_local_has_stale(store: StateStore) -> None:
    # Local thinks we own SPY, broker says we don't — broker wins.
    store.upsert_position(Position("SPY", 1.0, 500.0))
    diffs = store.reconcile_positions([])
    assert "SPY" in diffs
    assert store.get_position("SPY") is None


def test_reconcile_quantity_mismatch(store: StateStore) -> None:
    store.upsert_position(Position("SPY", 1.0, 500.0))
    diffs = store.reconcile_positions([Position("SPY", 3.0, 505.0)])
    assert "SPY" in diffs
    assert store.get_position("SPY") == Position("SPY", 3.0, 505.0)


def test_trade_log_and_run_log(store: StateStore) -> None:
    store.log_trade(42, "SPY", "BUY", 1, 500.0, "filled")
    store.log_run_event("startup", "test")
    # Just assert no raise and rows exist.
    rows = store._conn.execute("SELECT COUNT(*) FROM trade_log").fetchone()[0]
    assert rows == 1
    rows = store._conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
    assert rows == 1
