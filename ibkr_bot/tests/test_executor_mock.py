from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from ibkr_bot.executor import Executor
from ibkr_bot.state import StateStore
from ibkr_bot.strategy import Signal


class FakeEvent:
    """Minimal stand-in for ib_async's Event with += / -= semantics."""

    def __init__(self) -> None:
        self._handlers: list = []

    def __iadd__(self, h):
        self._handlers.append(h)
        return self

    def __isub__(self, h):
        self._handlers.remove(h)
        return self

    def fire(self, *args, **kwargs) -> None:
        for h in list(self._handlers):
            h(*args, **kwargs)


class FakeTrade:
    def __init__(self, order_id: int) -> None:
        self.order = SimpleNamespace(orderId=order_id)
        self.orderStatus = SimpleNamespace(
            status="Submitted", filled=0, avgFillPrice=0.0
        )
        self.statusEvent = FakeEvent()
        self._done = False

    def isDone(self) -> bool:
        return self._done

    def fill(self, qty: float, price: float) -> None:
        self.orderStatus = SimpleNamespace(status="Filled", filled=qty, avgFillPrice=price)
        self._done = True
        self.statusEvent.fire(self)

    def cancel(self) -> None:
        self.orderStatus = SimpleNamespace(
            status="Cancelled", filled=0, avgFillPrice=0.0
        )
        self._done = True
        self.statusEvent.fire(self)


class FakePosition:
    def __init__(self, symbol: str, qty: float, avg_cost: float) -> None:
        self.contract = SimpleNamespace(symbol=symbol)
        self.position = qty
        self.avgCost = avg_cost


class FakeIB:
    def __init__(self) -> None:
        self.placed: list = []
        self._next_id = 100
        self.qualified: list = []
        self._positions: list[FakePosition] = []

    async def qualifyContractsAsync(self, *contracts):
        self.qualified.extend(contracts)
        return list(contracts)

    def placeOrder(self, contract, order) -> FakeTrade:
        self._next_id += 1
        t = FakeTrade(self._next_id)
        self.placed.append((contract, order, t))
        return t

    def positions(self) -> list[FakePosition]:
        return list(self._positions)

    def set_position(self, symbol: str, qty: float, avg_cost: float) -> None:
        self._positions = [p for p in self._positions if p.contract.symbol != symbol]
        if qty != 0:
            self._positions.append(FakePosition(symbol, qty, avg_cost))


@pytest.fixture()
def state(tmp_path: Path) -> StateStore:
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


@pytest.mark.asyncio
async def test_dry_run_logs_signal_no_order(state: StateStore) -> None:
    ib = FakeIB()
    ex = Executor(ib, state, dry_run=True)
    await ex.execute(Signal("BUY", "SPY", 1, "test"))
    assert ib.placed == []
    # trade_log entry recorded
    rows = state._conn.execute("SELECT event FROM trade_log").fetchall()
    assert rows and rows[0]["event"] == "dry_run_signal"


@pytest.mark.asyncio
async def test_live_order_fills(state: StateStore) -> None:
    ib = FakeIB()
    ex = Executor(ib, state, dry_run=False)

    task = asyncio.create_task(ex.execute(Signal("BUY", "SPY", 1, "entry")))
    # Give the executor a chance to place and register its status handler.
    for _ in range(20):
        await asyncio.sleep(0)
        if ib.placed:
            break
    assert len(ib.placed) == 1
    _, order, trade = ib.placed[0]
    assert order.action == "BUY" and order.totalQuantity == 1

    # Simulate IBKR updating its position after the fill.
    ib.set_position("SPY", 1.0, 500.25)
    trade.fill(qty=1, price=500.25)
    await task

    # local position table now mirrors IBKR
    from ibkr_bot.state import Position
    assert state.get_position("SPY") == Position("SPY", 1.0, 500.25)

    # order row updated to Filled, trade_log has filled event.
    row = state._conn.execute(
        "SELECT status, filled_qty, avg_fill_price FROM orders WHERE order_id=?",
        (trade.order.orderId,),
    ).fetchone()
    assert row["status"] == "Filled"
    assert row["filled_qty"] == 1.0
    assert row["avg_fill_price"] == pytest.approx(500.25)

    events = [
        r["event"]
        for r in state._conn.execute(
            "SELECT event FROM trade_log ORDER BY id"
        ).fetchall()
    ]
    assert "submitted" in events and "filled" in events


@pytest.mark.asyncio
async def test_timeout_records_timeout_event(monkeypatch, state: StateStore) -> None:
    import ibkr_bot.executor as exmod
    monkeypatch.setattr(exmod, "FILL_TIMEOUT_SEC", 0.05)

    ib = FakeIB()
    ex = Executor(ib, state, dry_run=False)
    await ex.execute(Signal("BUY", "SPY", 1, "entry"))

    events = [
        r["event"]
        for r in state._conn.execute(
            "SELECT event FROM trade_log ORDER BY id"
        ).fetchall()
    ]
    assert "timeout" in events


@pytest.mark.asyncio
async def test_cancelled_order_recorded(state: StateStore) -> None:
    ib = FakeIB()
    ex = Executor(ib, state, dry_run=False)
    task = asyncio.create_task(ex.execute(Signal("SELL", "SPY", 1, "exit")))
    for _ in range(20):
        await asyncio.sleep(0)
        if ib.placed:
            break
    _, _, trade = ib.placed[0]
    trade.cancel()
    await task

    row = state._conn.execute(
        "SELECT status FROM orders WHERE order_id=?", (trade.order.orderId,)
    ).fetchone()
    assert row["status"] == "Cancelled"
    events = [
        r["event"]
        for r in state._conn.execute("SELECT event FROM trade_log").fetchall()
    ]
    assert "cancelled" in events
