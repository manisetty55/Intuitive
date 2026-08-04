"""Analysis pipeline for the AI SRE Agent.

Queries Prometheus for metric deviations, Loki for error logs, and Tempo for
traces exceeding baseline p99 latency within the detection time window (±5 min).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests
import structlog

from config import (
    ANALYSIS_WINDOW_SECONDS,
    LATENCY_P99_THRESHOLD_SECONDS,
    LOKI_URL,
    PROMETHEUS_QUERY_TIMEOUT_SECONDS,
    PROMETHEUS_URL,
    TEMPO_URL,
)
from detector import AnomalyEvent

logger = structlog.get_logger(__name__)


@dataclass
class AnalysisEvidence:
    """Evidence gathered from the observability stack during analysis."""

    metrics: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    traces: list[str] = field(default_factory=list)
    timeline: list[dict[str, str]] = field(default_factory=list)
    affected_services: list[str] = field(default_factory=list)
    symptoms: list[str] = field(default_factory=list)


def _get_time_range() -> tuple[float, float]:
    """Return (start, end) timestamps for the ±5 min analysis window."""
    now = datetime.now(timezone.utc).timestamp()
    start = now - ANALYSIS_WINDOW_SECONDS
    end = now + ANALYSIS_WINDOW_SECONDS
    return start, end


def query_prometheus_deviations(anomalies: list[AnomalyEvent]) -> list[str]:
    """Query Prometheus for metric deviations in the ±5 min detection window.

    Executes range queries for each anomaly's PromQL expression to gather
    the metric history around the detection time.

    Returns a list of string descriptions of observed metric deviations.
    """
    deviations: list[str] = []
    start, end = _get_time_range()

    for anomaly in anomalies:
        url = f"{PROMETHEUS_URL}/api/v1/query_range"
        params = {
            "query": anomaly.query,
            "start": start,
            "end": end,
            "step": "15s",
        }

        try:
            response = requests.get(
                url, params=params, timeout=PROMETHEUS_QUERY_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "success":
                logger.warning(
                    "prometheus_range_query_failed",
                    query=anomaly.query,
                    status=data.get("status"),
                )
                deviations.append(
                    f"{anomaly.metric_name}: query failed (status={data.get('status')})"
                )
                continue

            results = data.get("data", {}).get("result", [])
            if results:
                # Summarise the deviation
                values = [
                    float(v[1]) for v in results[0].get("values", []) if len(v) >= 2
                ]
                if values:
                    max_val = max(values)
                    min_val = min(values)
                    deviation_desc = (
                        f"{anomaly.metric_name}: current={anomaly.current_value:.4f}, "
                        f"threshold={anomaly.threshold:.4f}, "
                        f"range=[{min_val:.4f}, {max_val:.4f}] over ±5min window"
                    )
                    deviations.append(deviation_desc)
                    logger.info(
                        "metric_deviation_found",
                        metric=anomaly.metric_name,
                        current=anomaly.current_value,
                        max_in_window=max_val,
                    )
            else:
                deviations.append(
                    f"{anomaly.metric_name}: no data in analysis window"
                )

        except requests.exceptions.Timeout:
            logger.error(
                "prometheus_range_query_timeout",
                query=anomaly.query,
            )
            deviations.append(f"{anomaly.metric_name}: query timed out")
        except requests.exceptions.ConnectionError:
            logger.error("prometheus_connection_error_analysis", url=PROMETHEUS_URL)
            deviations.append(f"{anomaly.metric_name}: prometheus unreachable")
        except (ValueError, KeyError, IndexError) as exc:
            logger.error(
                "prometheus_range_parse_error",
                query=anomaly.query,
                error=str(exc),
            )
            deviations.append(f"{anomaly.metric_name}: parse error ({exc})")

    return deviations


def query_loki_error_logs(
    affected_services: Optional[list[str]] = None,
) -> list[str]:
    """Query Loki for error logs matching affected services in the ±5 min window.

    Uses LogQL to filter for error-level log entries from the specified services
    within the analysis time window.

    Returns a list of log line summaries.
    """
    if affected_services is None:
        affected_services = ["sample-api"]

    log_entries: list[str] = []
    start_ns, end_ns = _get_time_range()
    # Loki expects nanosecond timestamps
    start_ns_int = int(start_ns * 1_000_000_000)
    end_ns_int = int(end_ns * 1_000_000_000)

    for service in affected_services:
        # LogQL query for error-level logs from the service
        logql_query = (
            f'{{namespace="applications", container="{service}"}} |= "error" or '
            f'{{namespace="applications", container="{service}"}} | json | level="ERROR"'
        )

        url = f"{LOKI_URL}/loki/api/v1/query_range"
        params = {
            "query": logql_query,
            "start": str(start_ns_int),
            "end": str(end_ns_int),
            "limit": 50,
        }

        try:
            response = requests.get(
                url, params=params, timeout=PROMETHEUS_QUERY_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "success":
                logger.warning(
                    "loki_query_failed",
                    service=service,
                    status=data.get("status"),
                )
                continue

            streams = data.get("data", {}).get("result", [])
            for stream in streams:
                for ts, line in stream.get("values", []):
                    # Truncate long log lines for the summary
                    summary = line[:300] if len(line) > 300 else line
                    log_entries.append(f"[{service}] {summary}")

            if streams:
                logger.info(
                    "loki_errors_found",
                    service=service,
                    count=sum(len(s.get("values", [])) for s in streams),
                )

        except requests.exceptions.Timeout:
            logger.error("loki_query_timeout", service=service)
            log_entries.append(f"[{service}] Loki query timed out")
        except requests.exceptions.ConnectionError:
            logger.error("loki_connection_error", url=LOKI_URL)
            log_entries.append(f"[{service}] Loki unreachable")
        except (ValueError, KeyError) as exc:
            logger.error("loki_parse_error", service=service, error=str(exc))
            log_entries.append(f"[{service}] Loki response parse error: {exc}")

    return log_entries


def query_tempo_slow_traces(
    affected_services: Optional[list[str]] = None,
) -> list[str]:
    """Query Tempo for traces exceeding baseline p99 latency in the ±5 min window.

    Searches for traces from affected services where duration exceeds
    the configured p99 latency threshold.

    Returns a list of trace summary strings.
    """
    if affected_services is None:
        affected_services = ["sample-api"]

    trace_summaries: list[str] = []
    start, end = _get_time_range()

    # Convert threshold to duration string for Tempo search (e.g., "500ms")
    min_duration_ms = int(LATENCY_P99_THRESHOLD_SECONDS * 1000)

    for service in affected_services:
        url = f"{TEMPO_URL}/api/search"
        params = {
            "q": f'{{ resource.service.name = "{service}" }}',
            "start": str(int(start)),
            "end": str(int(end)),
            "minDuration": f"{min_duration_ms}ms",
            "limit": 20,
        }

        try:
            response = requests.get(
                url, params=params, timeout=PROMETHEUS_QUERY_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            data = response.json()

            traces = data.get("traces", [])
            for trace in traces:
                trace_id = trace.get("traceID", "unknown")
                duration_ms = trace.get("durationMs", 0)
                root_service = trace.get("rootServiceName", service)
                root_name = trace.get("rootTraceName", "unknown")
                summary = (
                    f"traceID={trace_id} service={root_service} "
                    f"operation={root_name} duration={duration_ms}ms"
                )
                trace_summaries.append(summary)

            if traces:
                logger.info(
                    "tempo_slow_traces_found",
                    service=service,
                    count=len(traces),
                    min_duration_ms=min_duration_ms,
                )

        except requests.exceptions.Timeout:
            logger.error("tempo_query_timeout", service=service)
            trace_summaries.append(f"[{service}] Tempo query timed out")
        except requests.exceptions.ConnectionError:
            logger.error("tempo_connection_error", url=TEMPO_URL)
            trace_summaries.append(f"[{service}] Tempo unreachable")
        except (ValueError, KeyError) as exc:
            logger.error("tempo_parse_error", service=service, error=str(exc))
            trace_summaries.append(f"[{service}] Tempo response parse error: {exc}")

    return trace_summaries


def gather_evidence(anomalies: list[AnomalyEvent]) -> AnalysisEvidence:
    """Run the full analysis pipeline gathering evidence from all sources.

    Steps:
    1. Query Prometheus for metric deviations in detection window (±5 min)
    2. Query Loki for error logs matching affected services in same window
    3. Query Tempo for traces exceeding baseline p99 latency

    Args:
        anomalies: List of detected anomaly events to investigate.

    Returns:
        AnalysisEvidence containing metrics, logs, traces, timeline, and symptoms.
    """
    evidence = AnalysisEvidence()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Determine affected services from anomaly queries
    affected_services = ["sample-api"]
    evidence.affected_services = affected_services

    # Record detection in timeline
    for anomaly in anomalies:
        evidence.timeline.append(
            {
                "timestamp": now_iso,
                "event": (
                    f"Anomaly detected: {anomaly.metric_name} = "
                    f"{anomaly.current_value:.4f} (threshold: {anomaly.threshold:.4f})"
                ),
                "source": "prometheus",
            }
        )
        evidence.symptoms.append(
            f"{anomaly.metric_name} exceeded threshold "
            f"({anomaly.current_value:.4f} > {anomaly.threshold:.4f})"
        )

    # Step 1: Query Prometheus for metric deviations
    logger.info("analysis_step_prometheus", anomaly_count=len(anomalies))
    metrics = query_prometheus_deviations(anomalies)
    evidence.metrics = metrics

    if metrics:
        evidence.timeline.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": f"Prometheus analysis: {len(metrics)} metric deviation(s) found",
                "source": "prometheus",
            }
        )

    # Step 2: Query Loki for error logs
    logger.info("analysis_step_loki", services=affected_services)
    logs = query_loki_error_logs(affected_services)
    evidence.logs = logs

    if logs:
        evidence.timeline.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": f"Loki analysis: {len(logs)} error log(s) found",
                "source": "loki",
            }
        )

    # Step 3: Query Tempo for slow traces
    logger.info("analysis_step_tempo", services=affected_services)
    traces = query_tempo_slow_traces(affected_services)
    evidence.traces = traces

    if traces:
        evidence.timeline.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": f"Tempo analysis: {len(traces)} slow trace(s) found",
                "source": "tempo",
            }
        )

    logger.info(
        "analysis_complete",
        metrics_count=len(metrics),
        logs_count=len(logs),
        traces_count=len(traces),
    )

    return evidence
