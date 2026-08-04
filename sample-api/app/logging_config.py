"""Structured logging configuration using structlog."""

import logging
import sys

import structlog


def init_logging() -> None:
    """Configure structlog for JSON output to stdout.

    Log fields include:
    - timestamp (ISO8601)
    - level
    - message

    Per-request context (added via structlog.contextvars):
    - trace_id
    - span_id
    - request_id
    - method
    - path
    - duration_ms
    - status_code
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Also configure standard library logging to go through structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
