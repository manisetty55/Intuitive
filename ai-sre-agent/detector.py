"""Anomaly detection module for the AI SRE Agent.

Polls Prometheus every configured interval for error rate spikes and latency
deviations from baseline thresholds.
"""

from dataclasses import dataclass
from typing import Optional

import requests
import structlog

from config import (
    ERROR_RATE_QUERY,
    ERROR_RATE_THRESHOLD,
    LATENCY_P99_QUERY,
    LATENCY_P99_THRESHOLD_SECONDS,
    PROMETHEUS_QUERY_TIMEOUT_SECONDS,
    PROMETHEUS_URL,
)

logger = structlog.get_logger(__name__)


@dataclass
class AnomalyEvent:
    """Represents a detected anomaly."""

    metric_name: str
    current_value: float
    threshold: float
    query: str


def query_prometheus(query: str) -> Optional[float]:
    """Execute a PromQL instant query and return the scalar result.

    Returns None if the query fails or returns no data.
    """
    url = f"{PROMETHEUS_URL}/api/v1/query"
    params = {"query": query}

    try:
        response = requests.get(
            url, params=params, timeout=PROMETHEUS_QUERY_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        data = response.json()

        if data.get("status") != "success":
            logger.warning(
                "prometheus_query_failed",
                query=query,
                status=data.get("status"),
            )
            return None

        results = data.get("data", {}).get("result", [])
        if not results:
            logger.debug("prometheus_query_empty", query=query)
            return None

        # Extract scalar value from the first result
        value_pair = results[0].get("value", [])
        if len(value_pair) < 2:
            return None

        return float(value_pair[1])

    except requests.exceptions.Timeout:
        logger.error(
            "prometheus_query_timeout",
            query=query,
            timeout_seconds=PROMETHEUS_QUERY_TIMEOUT_SECONDS,
        )
        return None
    except requests.exceptions.ConnectionError:
        logger.error("prometheus_connection_error", url=PROMETHEUS_URL)
        return None
    except (ValueError, KeyError, IndexError) as exc:
        logger.error("prometheus_response_parse_error", query=query, error=str(exc))
        return None


def check_error_rate() -> Optional[AnomalyEvent]:
    """Check if the current error rate exceeds the threshold."""
    value = query_prometheus(ERROR_RATE_QUERY)
    if value is None:
        return None

    if value > ERROR_RATE_THRESHOLD:
        logger.info(
            "anomaly_detected",
            metric_name="error_rate",
            current_value=value,
            threshold=ERROR_RATE_THRESHOLD,
        )
        return AnomalyEvent(
            metric_name="error_rate",
            current_value=value,
            threshold=ERROR_RATE_THRESHOLD,
            query=ERROR_RATE_QUERY,
        )

    return None


def check_latency_p99() -> Optional[AnomalyEvent]:
    """Check if the p99 latency deviates from baseline threshold."""
    value = query_prometheus(LATENCY_P99_QUERY)
    if value is None:
        return None

    if value > LATENCY_P99_THRESHOLD_SECONDS:
        logger.info(
            "anomaly_detected",
            metric_name="latency_p99",
            current_value=value,
            threshold=LATENCY_P99_THRESHOLD_SECONDS,
        )
        return AnomalyEvent(
            metric_name="latency_p99",
            current_value=value,
            threshold=LATENCY_P99_THRESHOLD_SECONDS,
            query=LATENCY_P99_QUERY,
        )

    return None


def detect_anomalies() -> list[AnomalyEvent]:
    """Run all anomaly detection checks and return any detected anomalies."""
    anomalies: list[AnomalyEvent] = []

    error_rate_anomaly = check_error_rate()
    if error_rate_anomaly:
        anomalies.append(error_rate_anomaly)

    latency_anomaly = check_latency_p99()
    if latency_anomaly:
        anomalies.append(latency_anomaly)

    return anomalies
