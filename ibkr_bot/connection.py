from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import structlog
from ib_async import IB

from .state import Position, StateStore


log = structlog.get_logger(__name__)


ReconcileCb = Callable[[list[Position]], Awaitable[None]]


class IBConnection:
    """Wraps ib_async.IB with auto-reconnect and a reconciliation hook."""

    def __init__(
        self,
        host: str,
        port: int,
        client_id: int,
        state: StateStore,
        on_reconnect: ReconcileCb | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.state = state
        self.on_reconnect = on_reconnect

        self.ib = IB()
        self._stop = asyncio.Event()
        self._reconnect_task: asyncio.Task[None] | None = None
        self._backoff = 1.0
        self._backoff_max = 60.0

        self.ib.disconnectedEvent += self._handle_disconnect

    async def connect(self) -> None:
        log.info("ib.connect.start", host=self.host, port=self.port, client_id=self.client_id)
        await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
        self._backoff = 1.0
        self.state.log_run_event("connected", f"{self.host}:{self.port} cid={self.client_id}")
        log.info("ib.connect.ok")
        await self._reconcile()

    async def _reconcile(self) -> None:
        # Pull positions from IBKR and hand them to the callback.
        broker_positions: list[Position] = []
        for p in self.ib.positions():
            sym = p.contract.symbol
            broker_positions.append(
                Position(symbol=sym, qty=float(p.position), avg_cost=float(p.avgCost))
            )
        diffs = self.state.reconcile_positions(broker_positions)
        if diffs:
            log.warning("reconcile.mismatch", diffs={k: str(v) for k, v in diffs.items()})
            self.state.log_run_event("reconcile_mismatch", str(diffs))
        else:
            log.info("reconcile.ok", positions=len(broker_positions))

        # Also log open orders IBKR reports, so any drift is visible.
        open_trades = self.ib.openTrades()
        if open_trades:
            log.warning(
                "reconcile.open_orders_from_broker",
                count=len(open_trades),
                orders=[t.order.orderId for t in open_trades],
            )

        if self.on_reconnect is not None:
            await self.on_reconnect(broker_positions)

    def _handle_disconnect(self) -> None:
        if self._stop.is_set():
            return
        log.warning("ib.disconnected")
        self.state.log_run_event("disconnected")
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while not self._stop.is_set():
            delay = min(self._backoff, self._backoff_max)
            log.info("ib.reconnect.wait", seconds=delay)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                return  # stop requested during wait
            except asyncio.TimeoutError:
                pass
            try:
                await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
                self._backoff = 1.0
                log.info("ib.reconnect.ok")
                self.state.log_run_event("reconnected")
                await self._reconcile()
                return
            except Exception as e:  # noqa: BLE001 — transient, keep retrying
                log.warning("ib.reconnect.fail", error=str(e))
                self._backoff = min(self._backoff * 2, self._backoff_max)

    async def disconnect(self) -> None:
        self._stop.set()
        if self.ib.isConnected():
            # Log open positions for next startup context.
            for pos in self.state.all_positions():
                log.info("shutdown.position", symbol=pos.symbol, qty=pos.qty, avg_cost=pos.avg_cost)
            self.ib.disconnect()
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except (asyncio.CancelledError, Exception):
                pass
        self.state.log_run_event("shutdown")
        log.info("ib.shutdown.ok")
