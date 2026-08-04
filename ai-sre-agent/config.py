"""Configuration for the AI SRE Agent."""

import os


# Prometheus endpoint
PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://prometheus-server.observability.svc.cluster.local:9090",
)

# Loki endpoint
LOKI_URL = os.environ.get(
    "LOKI_URL",
    "http://loki.observability.svc.cluster.local:3100",
)

# Tempo endpoint
TEMPO_URL = os.environ.get(
    "TEMPO_URL",
    "http://tempo.observability.svc.cluster.local:3200",
)

# LLM configuration
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "2"))

# Polling interval in seconds (configurable via env var)
POLLING_INTERVAL_SECONDS = int(os.environ.get("POLLING_INTERVAL_SECONDS", "30"))

# Anomaly detection thresholds
ERROR_RATE_THRESHOLD = float(os.environ.get("ERROR_RATE_THRESHOLD", "0.05"))
LATENCY_P99_THRESHOLD_SECONDS = float(
    os.environ.get("LATENCY_P99_THRESHOLD_SECONDS", "0.5")
)

# PromQL queries for key metrics
ERROR_RATE_QUERY = os.environ.get(
    "ERROR_RATE_QUERY",
    'sum(rate(error_count{service="sample-api"}[5m]))',
)
LATENCY_P99_QUERY = os.environ.get(
    "LATENCY_P99_QUERY",
    'histogram_quantile(0.99, sum(rate(request_duration_seconds_bucket{service="sample-api"}[5m])) by (le))',
)

# HTTP timeout for Prometheus queries
PROMETHEUS_QUERY_TIMEOUT_SECONDS = int(
    os.environ.get("PROMETHEUS_QUERY_TIMEOUT_SECONDS", "10")
)

# Reports directory (PVC-mounted path for RCA report persistence)
REPORTS_DIR = os.environ.get("REPORTS_DIR", "/reports")

# Analysis window (seconds before and after detection)
ANALYSIS_WINDOW_SECONDS = int(os.environ.get("ANALYSIS_WINDOW_SECONDS", "300"))
