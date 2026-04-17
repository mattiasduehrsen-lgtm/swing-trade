from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    avg_cost: float


@dataclass(frozen=True)
class OrderRecord:
    order_id: int
    symbol: str
    side: str  # "BUY" or "SELL"
    qty: float
    status: str  # submitted/filled/cancelled/rejected/partial
    filled_qty: float
    avg_fill_price: float | None
    created_at: str
    updated_at: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    symbol    TEXT PRIMARY KEY,
    qty       REAL NOT NULL,
    avg_cost  REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id       INTEGER PRIMARY KEY,
    symbol         TEXT NOT NULL,
    side           TEXT NOT NULL,
    qty            REAL NOT NULL,
    status         TEXT NOT NULL,
    filled_qty     REAL NOT NULL DEFAULT 0,
    avg_fill_price REAL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER,
    symbol     TEXT NOT NULL,
    side       TEXT NOT NULL,
    qty        REAL NOT NULL,
    price      REAL,
    event      TEXT NOT NULL,
    details    TEXT,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event      TEXT NOT NULL,
    message    TEXT,
    recorded_at TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    """Thin wrapper over SQLite. Not thread-safe; call from the asyncio loop only."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # ---------- positions ----------
    def upsert_position(self, pos: Position) -> None:
        with self._tx() as c:
            c.execute(
                """INSERT INTO positions(symbol, qty, avg_cost, updated_at)
                   VALUES(?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     qty=excluded.qty,
                     avg_cost=excluded.avg_cost,
                     updated_at=excluded.updated_at""",
                (pos.symbol, pos.qty, pos.avg_cost, _utcnow()),
            )

    def delete_position(self, symbol: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM positions WHERE symbol=?", (symbol,))

    def get_position(self, symbol: str) -> Position | None:
        row = self._conn.execute(
            "SELECT symbol, qty, avg_cost FROM positions WHERE symbol=?", (symbol,)
        ).fetchone()
        if row is None or row["qty"] == 0:
            return None
        return Position(symbol=row["symbol"], qty=row["qty"], avg_cost=row["avg_cost"])

    def all_positions(self) -> list[Position]:
        rows = self._conn.execute(
            "SELECT symbol, qty, avg_cost FROM positions WHERE qty != 0"
        ).fetchall()
        return [Position(r["symbol"], r["qty"], r["avg_cost"]) for r in rows]

    # ---------- orders ----------
    def record_order(
        self,
        order_id: int,
        symbol: str,
        side: str,
        qty: float,
        status: str,
        filled_qty: float = 0.0,
        avg_fill_price: float | None = None,
    ) -> None:
        now = _utcnow()
        with self._tx() as c:
            c.execute(
                """INSERT INTO orders(order_id, symbol, side, qty, status,
                                       filled_qty, avg_fill_price, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(order_id) DO UPDATE SET
                     status=excluded.status,
                     filled_qty=excluded.filled_qty,
                     avg_fill_price=excluded.avg_fill_price,
                     updated_at=excluded.updated_at""",
                (order_id, symbol, side, qty, status, filled_qty, avg_fill_price, now, now),
            )

    def open_orders(self) -> list[OrderRecord]:
        rows = self._conn.execute(
            """SELECT * FROM orders
               WHERE status IN ('submitted','presubmitted','partial')"""
        ).fetchall()
        return [OrderRecord(**dict(r)) for r in rows]

    def log_trade(
        self,
        order_id: int | None,
        symbol: str,
        side: str,
        qty: float,
        price: float | None,
        event: str,
        details: str | None = None,
    ) -> None:
        with self._tx() as c:
            c.execute(
                """INSERT INTO trade_log(order_id, symbol, side, qty, price, event, details, recorded_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                (order_id, symbol, side, qty, price, event, details, _utcnow()),
            )

    def log_run_event(self, event: str, message: str | None = None) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO run_log(event, message, recorded_at) VALUES(?, ?, ?)",
                (event, message, _utcnow()),
            )

    # ---------- reconciliation ----------
    def reconcile_positions(
        self, broker_positions: list[Position]
    ) -> dict[str, tuple[Position | None, Position | None]]:
        """Overwrite local state with `broker_positions`. IBKR is source of truth.

        Returns a diff dict {symbol: (local_before, broker_after)} for any symbol where
        local and broker disagreed. Callers should log this loudly.
        """
        local = {p.symbol: p for p in self.all_positions()}
        broker = {p.symbol: p for p in broker_positions}
        diffs: dict[str, tuple[Position | None, Position | None]] = {}

        for sym in set(local) | set(broker):
            l, b = local.get(sym), broker.get(sym)
            if l != b:
                diffs[sym] = (l, b)

        # Overwrite: delete locals not in broker, upsert all broker positions.
        for sym in local:
            if sym not in broker:
                self.delete_position(sym)
        for pos in broker.values():
            self.upsert_position(pos)

        return diffs
