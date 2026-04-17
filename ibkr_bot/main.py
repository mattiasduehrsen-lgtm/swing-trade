from __future__ import annotations

import asyncio
import signal

import structlog

from .config import load_settings
from .connection import IBConnection
from .executor import Executor
from .logging_config import configure_logging
from .scheduler import Scheduler
from .state import StateStore
from .strategy import SpyWeeklyStrategy


async def _run() -> None:
    settings = load_settings()
    log = configure_logging(settings.log_level, settings.log_dir)
    log.info(
        "boot",
        host=settings.ibkr_host,
        port=settings.ibkr_port,
        client_id=settings.ibkr_client_id,
        dry_run=settings.dry_run,
        symbol=settings.strategy_symbol,
        qty=settings.strategy_qty,
    )

    state = StateStore(settings.db_path)
    conn = IBConnection(
        host=settings.ibkr_host,
        port=settings.ibkr_port,
        client_id=settings.ibkr_client_id,
        state=state,
    )
    await conn.connect()

    strategy = SpyWeeklyStrategy(settings.strategy_symbol, settings.strategy_qty)
    executor = Executor(conn.ib, state, dry_run=settings.dry_run)
    scheduler = Scheduler(strategy, executor, state)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("signal.received")
        stop_event.set()
        scheduler.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    scheduler_task = asyncio.create_task(scheduler.run())
    try:
        await stop_event.wait()
    finally:
        scheduler.stop()
        await scheduler_task
        await conn.disconnect()
        state.close()
        log.info("shutdown.done")


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        structlog.get_logger(__name__).info("keyboard.interrupt")


if __name__ == "__main__":
    main()
