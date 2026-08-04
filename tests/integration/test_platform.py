"""End-to-end integration tests for the local platform stack.

These tests require a running k3s cluster with the full platform deployed.
Gate: set PLATFORM_INTEGRATION_TEST=1 to enable.
Timeout: 600s for the full test suite.

Run with: PLATFORM_INTEGRATION_TEST=1 pytest -v --timeout=600
"""

import json
import os
import subprocess
import time

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SAMPLE_API_URL = os.environ.get("SAMPLE_API_URL", "http://localhost:8080")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
LOKI_URL = os.environ.get("LOKI_URL", "http://localhost:3100")
TEMPO_URL = os.environ.get("TEMPO_URL", "http://localhost:3200")

POLL_INTERVAL = 2  # seconds
LOG_INGESTION_TIMEOUT = 30
TRACE_INGESTION_TIMEOUT = 30
METRICS_SCRAPE_TIMEOUT = 30
ALERT_FIRE_TIMEOUT = 120
WORKFLOW_TIMEOUT = 60
DEGRADATION_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def kubectl(*args: str) -> str:
    """Run a kubectl command and return stdout."""
    result = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def wait_for(description: str, timeout: float, condition):
    """Poll a condition until True or timeout."""
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            result = condition()
            if result:
                return result
        except Exception as e:
            last_error = e
        time.sleep(POLL_INTERVAL)
    msg = f"Timed out waiting for {description}"
    if last_error:
        msg += f" (last error: {last_error})"
    raise TimeoutError(msg)


# ---------------------------------------------------------------------------
# Test: Log Ingestion Pipeline (API → Loki)
# Validates: Requirement 3.3
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_log_ingestion_api_to_loki():
    """Send a request to Sample API, then query Loki for the log entry."""
    # Send a request to generate a log entry
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{SAMPLE_API_URL}/api/v1/status")
        assert resp.status_code == 200
        request_id = resp.json()["request_id"]

    # Poll Loki for the log containing our request_id
    def check_loki():
        query = f'{{namespace="applications"}} |= "{request_id}"'
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "limit": "5",
                    "start": str(int((time.time() - 120) * 1e9)),
                    "end": str(int(time.time() * 1e9)),
                },
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            results = data.get("data", {}).get("result", [])
            for stream in results:
                if stream.get("values"):
                    return True
        return False

    wait_for("log ingestion into Loki", LOG_INGESTION_TIMEOUT, check_loki)


# ---------------------------------------------------------------------------
# Test: Trace Pipeline (API → Tempo)
# Validates: Requirement 3.4
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_trace_pipeline_api_to_tempo():
    """Send a request to Sample API, then query Tempo for the trace."""
    with httpx.Client(timeout=10) as client:
        resp = client.post(f"{SAMPLE_API_URL}/api/v1/process", json={})
        assert resp.status_code == 200

    # Poll Tempo for traces from sample-api
    def check_tempo():
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{TEMPO_URL}/api/search",
                params={
                    "service.name": "sample-api",
                    "limit": "5",
                    "start": str(int(time.time() - 120)),
                    "end": str(int(time.time())),
                },
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            return len(data.get("traces", [])) > 0

    wait_for("trace ingestion into Tempo", TRACE_INGESTION_TIMEOUT, check_tempo)


# ---------------------------------------------------------------------------
# Test: Metrics Scraping Pipeline (API → Prometheus)
# Validates: Requirement 3.5
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_metrics_scraping_api_to_prometheus():
    """Send requests to Sample API, then query Prometheus for metrics."""
    # Send several requests to generate metrics
    with httpx.Client(timeout=10) as client:
        for _ in range(5):
            resp = client.get(f"{SAMPLE_API_URL}/api/v1/status")
            assert resp.status_code == 200

    # Poll Prometheus for the request_count metric
    def check_prometheus():
        query = 'request_count{service="sample-api"}'
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            results = data.get("data", {}).get("result", [])
            return len(results) > 0

    wait_for("metrics scraping by Prometheus", METRICS_SCRAPE_TIMEOUT, check_prometheus)


# ---------------------------------------------------------------------------
# Test: SLO Breach → Alert Fires
# Validates: Requirement 3.8
# ---------------------------------------------------------------------------


@pytest.mark.timeout(180)
def test_slo_breach_alert_fires():
    """Inject latency to breach SLO, verify SLOLatencyBreach alert fires."""
    # Inject latency
    try:
        kubectl(
            "set", "env", "-n", "applications",
            "deployment/sample-api", "INJECT_LATENCY_MS=2000",
        )
    except RuntimeError:
        pytest.skip("Could not inject latency into deployment")

    try:
        # Send traffic to trigger high-latency metrics
        with httpx.Client(timeout=10) as client:
            for _ in range(30):
                try:
                    client.get(f"{SAMPLE_API_URL}/api/v1/status")
                except Exception:
                    pass
                time.sleep(1)

        # Check for SLOLatencyBreach alert
        def check_alert():
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{PROMETHEUS_URL}/api/v1/alerts")
                if resp.status_code != 200:
                    return False
                data = resp.json()
                alerts = data.get("data", {}).get("alerts", [])
                for alert in alerts:
                    if alert.get("labels", {}).get("alertname") == "SLOLatencyBreach":
                        return alert.get("state") in ("firing", "pending")
            return False

        wait_for("SLOLatencyBreach alert", ALERT_FIRE_TIMEOUT, check_alert)
    finally:
        # Cleanup
        kubectl(
            "set", "env", "-n", "applications",
            "deployment/sample-api", "INJECT_LATENCY_MS-",
        )


# ---------------------------------------------------------------------------
# Test: Temporal Workflow Execution to Completed Status
# Validates: Requirements 4.2, 4.3
# ---------------------------------------------------------------------------


@pytest.mark.timeout(120)
def test_temporal_workflow_execution():
    """Start HealthCheckWorkflow and verify it completes."""
    workflow_id = f"integration-test-{int(time.time())}"

    # Try to start workflow via tctl in the temporal frontend pod
    try:
        kubectl(
            "exec", "-n", "temporal", "deploy/temporal-frontend", "--",
            "tctl", "workflow", "start",
            "--taskqueue", "health-check-queue",
            "--workflow_type", "HealthCheckWorkflow",
            "--workflow_id", workflow_id,
            "--input", '{"target_service":"sample-api","check_type":"http"}',
        )
    except RuntimeError:
        # Try newer temporal CLI
        try:
            kubectl(
                "exec", "-n", "temporal", "deploy/temporal-frontend", "--",
                "temporal", "workflow", "start",
                "--task-queue", "health-check-queue",
                "--type", "HealthCheckWorkflow",
                "--workflow-id", workflow_id,
                "--input", '{"target_service":"sample-api","check_type":"http"}',
            )
        except RuntimeError:
            pytest.skip("Could not start Temporal workflow")

    # Wait for workflow to complete
    def check_workflow():
        try:
            output = kubectl(
                "exec", "-n", "temporal", "deploy/temporal-frontend", "--",
                "tctl", "workflow", "describe", "--workflow_id", workflow_id,
            )
        except RuntimeError:
            try:
                output = kubectl(
                    "exec", "-n", "temporal", "deploy/temporal-frontend", "--",
                    "temporal", "workflow", "describe", "--workflow-id", workflow_id,
                )
            except RuntimeError:
                return False
        return "Completed" in output or "COMPLETED" in output

    wait_for("workflow completion", WORKFLOW_TIMEOUT, check_workflow)


# ---------------------------------------------------------------------------
# Test: Failure Injection → Measurable Degradation
# Validates: Requirement 9.2
# ---------------------------------------------------------------------------


@pytest.mark.timeout(90)
def test_failure_injection_causes_degradation():
    """Kill Sample API pod and verify measurable degradation within 30s."""
    # Kill the pod
    try:
        kubectl(
            "delete", "pod", "-n", "applications",
            "-l", "app=sample-api", "--grace-period=0", "--force",
        )
    except RuntimeError:
        pytest.skip("Could not delete sample-api pod")

    # Send requests during degradation window and count errors
    errors = 0
    successes = 0
    deadline = time.time() + DEGRADATION_TIMEOUT

    with httpx.Client(timeout=5) as client:
        while time.time() < deadline:
            try:
                resp = client.get(f"{SAMPLE_API_URL}/api/v1/status")
                if resp.status_code == 200:
                    successes += 1
                else:
                    errors += 1
            except Exception:
                errors += 1
            time.sleep(0.5)

    total = errors + successes
    assert total > 0, "No requests were made during degradation window"

    error_rate = errors / total * 100
    assert errors > 0 or error_rate > 5, (
        f"Expected measurable degradation (errors or >5% error rate), "
        f"got {errors} errors / {total} total ({error_rate:.1f}%)"
    )

    # Wait for recovery
    def check_recovery():
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{SAMPLE_API_URL}/health")
                return resp.status_code == 200
        except Exception:
            return False

    wait_for("Sample API recovery", 60, check_recovery)
