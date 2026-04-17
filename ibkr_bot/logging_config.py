from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog


def configure_logging(log_level: str, log_dir: Path) -> structlog.stdlib.BoundLogger:
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    # Rotating file handler — JSON lines, 10MB x 10.
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "ibkr_bot.jsonl",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    file_handler.setLevel(level)

    # Stdout — human-readable, INFO+ only for interactive runs.
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("%(message)s"))
    stdout_handler.setLevel(max(level, logging.INFO))

    root.addHandler(file_handler)
    root.addHandler(stdout_handler)

    # ib_async is chatty at DEBUG; keep it at WARNING unless explicitly asked.
    logging.getLogger("ib_async").setLevel(
        logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    )

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route stdout through a human renderer, file through JSON.
    stdout_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processor=structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty()),
        )
    )
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processor=structlog.processors.JSONRenderer(),
        )
    )

    return structlog.get_logger("ibkr_bot")
