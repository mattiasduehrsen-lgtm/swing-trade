from __future__ import annotations

import asyncio

import structlog
from ib_async import IB, MarketOrder, Stock, Trade

from .state import Position, StateStore
from .strategy import Signal


log = structlog.get_logger(__name__)

FILL_TIMEOUT_SEC = 30.0


class Executor:
    def __init__(self, ib: IB, state: StateStore, dry_run: bool) -> None:
        self.ib = ib
        self.state = state
        self.dry_run = dry_run

    async def execute(self, signal: Signal) -> None:
        log_ctx = log.bind(
            symbol=signal.symbol, side=signal.side, qty=signal.qty, reason=signal.reason
        )

        if self.dry_run:
            log_ctx.info("executor.dry_run.signal")
            self.state.log_trade(
                None, signal.symbol, signal.side, signal.qty, None,
                event="dry_run_signal", details=signal.reason,
            )
            return

        contract = Stock(signal.symbol, "SMART", "USD")
        await self.ib.qualifyContractsAsync(contract)
        order = MarketOrder(signal.side, signal.qty)

        trade: Trade = self.ib.placeOrder(contract, order)
        order_id = trade.order.orderId
        log_ctx = log_ctx.bind(order_id=order_id)
        log_ctx.info("executor.order.submitted")

        self.state.record_order(
            order_id, signal.symbol, signal.side, signal.qty, status="submitted"
        )
        self.state.log_trade(
            order_id, signal.symbol, signal.side, signal.qty, None,
            event="submitted", details=signal.reason,
        )

        try:
            await asyncio.wait_for(self._await_terminal(trade), timeout=FILL_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            log_ctx.error("executor.order.timeout", timeout_sec=FILL_TIMEOUT_SEC)
            self.state.record_order(
                order_id, signal.symbol, signal.side, signal.qty,
                status=trade.orderStatus.status or "timeout",
                filled_qty=float(trade.orderStatus.filled or 0),
                avg_fill_price=float(trade.orderStatus.avgFillPrice) or None,
            )
            self.state.log_trade(
                order_id, signal.symbol, signal.side, signal.qty, None,
                event="timeout",
                details=f"status={trade.orderStatus.status}",
            )
            return

        status = trade.orderStatus.status
        filled = float(trade.orderStatus.filled or 0)
        avg_px = float(trade.orderStatus.avgFillPrice) or None

        self.state.record_order(
            order_id, signal.symbol, signal.side, signal.qty,
            status=status, filled_qty=filled, avg_fill_price=avg_px,
        )

        if status == "Filled":
            log_ctx.info("executor.order.filled", filled=filled, avg_px=avg_px)
            self.state.log_trade(
                order_id, signal.symbol, signal.side, filled, avg_px,
                event="filled",
            )
            self._sync_position(signal.symbol)
        elif status in ("Cancelled", "ApiCancelled"):
            log_ctx.warning("executor.order.cancelled", status=status, filled=filled)
            self.state.log_trade(
                order_id, signal.symbol, signal.side, signal.qty, avg_px,
                event="cancelled", details=status,
            )
        else:  # PartiallyFilled, Inactive, Rejected, …
            log_ctx.error(
                "executor.order.abnormal", status=status, filled=filled, avg_px=avg_px
            )
            self.state.log_trade(
                order_id, signal.symbol, signal.side, signal.qty, avg_px,
                event="abnormal", details=status,
            )

    def _sync_position(self, symbol: str) -> None:
        """After a fill, mirror IBKR's reported position for `symbol` into local state."""
        matched = False
        for p in self.ib.positions():
            if p.contract.symbol != symbol:
                continue
            matched = True
            qty = float(p.position)
            if qty == 0:
                self.state.delete_position(symbol)
            else:
                self.state.upsert_position(
                    Position(symbol=symbol, qty=qty, avg_cost=float(p.avgCost))
                )
        if not matched:
            # IBKR no longer reports a position — we just sold it all.
            self.state.delete_position(symbol)

    async def _await_terminal(self, trade: Trade) -> None:
        """Resolve when trade reaches a terminal state (Filled/Cancelled/Inactive)."""
        done = asyncio.Event()

        def on_status(t: Trade) -> None:
            if t.isDone():
                done.set()

        trade.statusEvent += on_status
        try:
            if trade.isDone():
                return
            await done.wait()
        finally:
            trade.statusEvent -= on_status
