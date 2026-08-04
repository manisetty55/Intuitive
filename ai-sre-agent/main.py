"""AI SRE Agent - Main entry point.

Runs the anomaly detection polling loop, querying Prometheus every configured
interval for error rate spikes and latency deviations from baseline.
"""

import signal
import sys
import time

import structlog

from config import POLLING_INTERVAL_SECONDS, PROMETHEUS_URL
from detector import detect_anomalies

# Configure structlog for JSON output
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger("ai_sre_agent")

# Graceful shutdown flag
_shutdown_requested = False


def _handle_signal(signum: int, frame) -> None:
    """Handle shutdown signals gracefully."""
    global _shutdown_requested
    logger.info("shutdown_requested", signal=signum)
    _shutdown_requested = True


def run_detection_loop() -> None:
    """Run the anomaly detection polling loop.

    Polls Prometheus every POLLING_INTERVAL_SECONDS for error rate spikes
    and latency deviations from baseline. Logs detection events when
    anomalies are found.
    """
    logger.info(
        "detection_loop_started",
        polling_interval_seconds=POLLING_INTERVAL_SECONDS,
        prometheus_url=PROMETHEUS_URL,
    )

    while not _shutdown_requested:
        try:
            anomalies = detect_anomalies()

            if anomalies:
                for anomaly in anomalies:
                    logger.warning(
                        "anomaly_detection_event",
                        metric_name=anomaly.metric_name,
                        current_value=anomaly.current_value,
                        threshold=anomaly.threshold,
                    )
            else:
                logger.debug("detection_cycle_complete", anomalies_found=0)

        except Exception as exc:
            logger.error(
                "detection_cycle_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )

        # Sleep for the configured polling interval
        # Use small increments to allow faster shutdown response
        elapsed = 0.0
        while elapsed < POLLING_INTERVAL_SECONDS and not _shutdown_requested:
            time.sleep(min(1.0, POLLING_INTERVAL_SECONDS - elapsed))
            elapsed += 1.0


def main() -> None:
    """Entry point for the AI SRE Agent."""
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("ai_sre_agent_starting", version="0.1.0")

    try:
        run_detection_loop()
    except KeyboardInterrupt:
        logger.info("interrupted_by_user")
    finally:
        logger.info("ai_sre_agent_stopped")

    sys.exit(0)


if __name__ == "__main__":
    main()
