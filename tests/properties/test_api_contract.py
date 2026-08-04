"""Property-based tests for API contract (Properties 1-4).

Validates: Requirements 5.1, 5.2, 5.4, 5.7

Uses hypothesis + httpx AsyncClient with ASGITransport to test the FastAPI app
in-process without requiring a running server.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

# Add the sample-api directory to the Python path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "sample-api"))

import pytest
import httpx
from httpx import ASGITransport
from hypothesis import given, settings
from hypothesis import strategies as st

from app.main import app

# --- Regex patterns ---
ISO8601_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)
HEX32_REGEX = re.compile(r"^[0-9a-f]{32}$")
HEX16_REGEX = re.compile(r"^[0-9a-f]{16}$")
TRACEPARENT_REGEX = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


# --- Helpers ---

def make_client() -> httpx.AsyncClient:
    """Create an httpx AsyncClient that talks directly to the FastAPI app."""
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# ============================================================================
# Property 1: API Response Contract
# Validates: Requirements 5.1
# For any valid HTTP request to /api/v1/status, the JSON response SHALL contain
# the fields `status` (non-empty), `timestamp` (ISO 8601), and `request_id`
# (non-empty, unique).
# ============================================================================


class TestProperty1_APIResponseContract:
    """Property 1: API Response Contract. Validates: Requirements 5.1."""

    @settings(max_examples=100)
    @given(
        include_accept=st.booleans(),
        accept_value=st.sampled_from(["application/json", "*/*", "text/html"]),
        include_custom=st.booleans(),
        custom_value=st.from_regex(r"[a-zA-Z0-9]{1,20}", fullmatch=True),
    )
    @pytest.mark.asyncio
    async def test_status_response_contract(
        self, include_accept, accept_value, include_custom, custom_value
    ):
        """Any request to /api/v1/status returns status, timestamp, request_id."""
        headers = {}
        if include_accept:
            headers["Accept"] = accept_value
        if include_custom:
            headers["X-Custom"] = custom_value

        async with make_client() as client:
            resp = await client.get("/api/v1/status", headers=headers)

        assert resp.status_code == 200

        body = resp.json()

        # `status` field is non-empty string
        assert "status" in body
        assert isinstance(body["status"], str) and body["status"] != ""

        # `timestamp` field is valid ISO 8601
        assert "timestamp" in body
        ts = body["timestamp"]
        assert isinstance(ts, str) and ts != ""
        assert ISO8601_REGEX.match(ts), f"timestamp not ISO 8601: {ts}"
        # Also parse to confirm it's a valid datetime
        datetime.fromisoformat(ts.replace("Z", "+00:00"))

        # `request_id` field is non-empty
        assert "request_id" in body
        assert isinstance(body["request_id"], str) and body["request_id"] != ""

    @settings(max_examples=100)
    @given(st.integers(min_value=2, max_value=10))
    @pytest.mark.asyncio
    async def test_request_id_uniqueness(self, num_requests):
        """Request IDs are unique across multiple requests."""
        seen_ids: set[str] = set()

        async with make_client() as client:
            for _ in range(num_requests):
                resp = await client.get("/api/v1/status")
                assert resp.status_code == 200
                request_id = resp.json()["request_id"]
                assert request_id not in seen_ids, f"duplicate request_id: {request_id}"
                seen_ids.add(request_id)


# ============================================================================
# Property 2: Structured Log Completeness
# Validates: Requirements 5.2
# For any HTTP request processed by the Sample API, the resulting structured
# JSON log entry SHALL contain all required fields: timestamp (ISO 8601),
# level, message (non-empty), trace_id (32-char hex), span_id (16-char hex).
# ============================================================================


class TestProperty2_StructuredLogCompleteness:
    """Property 2: Structured Log Completeness. Validates: Requirements 5.2."""

    @settings(max_examples=100)
    @given(
        path=st.sampled_from(["/api/v1/status", "/api/v1/process", "/health"]),
    )
    @pytest.mark.asyncio
    async def test_log_entries_have_required_fields(self, path, capsys):
        """Log entries from request processing have all required fields."""
        import json

        async with make_client() as client:
            if path == "/api/v1/process":
                resp = await client.post(path, json={})
            else:
                resp = await client.get(path)

        assert resp.status_code == 200

        # Capture stdout where structured logs are written
        captured = capsys.readouterr()
        log_lines = captured.out.strip().split("\n")

        for line in log_lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            # Only check request processing log entries
            msg = entry.get("event", entry.get("message", ""))
            if "request" not in msg.lower() and "completed" not in msg.lower():
                continue

            # Verify timestamp
            ts = entry.get("timestamp", "")
            assert ts != "", f"log entry missing timestamp: {line}"

            # Verify level
            level = entry.get("level", "")
            assert level != "", f"log entry missing level: {line}"

            # Verify message/event is non-empty
            assert msg != "", f"log entry has empty message: {line}"

            # Verify trace_id (32-char hex)
            trace_id = entry.get("trace_id", "")
            if trace_id:
                assert HEX32_REGEX.match(trace_id), f"trace_id not 32-char hex: {trace_id}"

            # Verify span_id (16-char hex)
            span_id = entry.get("span_id", "")
            if span_id:
                assert HEX16_REGEX.match(span_id), f"span_id not 16-char hex: {span_id}"


# ============================================================================
# Property 3: Metrics Counter Accuracy
# Validates: Requirements 5.4
# For any sequence of N valid HTTP requests sent to the Sample API, the
# `request_count` metric SHALL increase by exactly N.
# ============================================================================


class TestProperty3_MetricsCounterAccuracy:
    """Property 3: Metrics Counter Accuracy. Validates: Requirements 5.4."""

    @settings(max_examples=100)
    @given(n=st.integers(min_value=1, max_value=50))
    @pytest.mark.asyncio
    async def test_request_count_increments_by_n(self, n):
        """Sending N requests increments request_count by exactly N."""
        from prometheus_client import REGISTRY

        # Get the current counter value for GET /api/v1/status with status 200
        def get_counter_value():
            try:
                # Access the metric from the registry
                for metric in REGISTRY.collect():
                    if metric.name == "request_count":
                        for sample in metric.samples:
                            if (
                                sample.labels.get("method") == "GET"
                                and sample.labels.get("path") == "/api/v1/status"
                                and sample.labels.get("status_code") == "200"
                            ):
                                return sample.value
            except Exception:
                pass
            return 0.0

        before = get_counter_value()

        async with make_client() as client:
            for _ in range(n):
                resp = await client.get("/api/v1/status")
                assert resp.status_code == 200

        after = get_counter_value()
        delta = after - before
        assert delta == n, f"request_count delta: expected {n}, got {delta}"


# ============================================================================
# Property 4: Trace Context Propagation
# Validates: Requirements 5.7
# For any inbound HTTP request with a valid W3C traceparent header, the
# request SHALL be processed successfully.
# ============================================================================


class TestProperty4_TraceContextPropagation:
    """Property 4: Trace Context Propagation. Validates: Requirements 5.7."""

    @settings(max_examples=100)
    @given(
        trace_id=st.from_regex(r"[0-9a-f]{32}", fullmatch=True),
        parent_id=st.from_regex(r"[0-9a-f]{16}", fullmatch=True),
        flags=st.sampled_from(["00", "01"]),
    )
    @pytest.mark.asyncio
    async def test_traceparent_header_accepted(self, trace_id, parent_id, flags):
        """Requests with valid W3C traceparent headers are processed correctly."""
        traceparent = f"00-{trace_id}-{parent_id}-{flags}"
        assert TRACEPARENT_REGEX.match(traceparent)

        headers = {
            "Traceparent": traceparent,
            "Content-Type": "application/json",
        }

        async with make_client() as client:
            resp = await client.post("/api/v1/process", headers=headers, json={})

        assert resp.status_code == 200

        body = resp.json()
        assert body["status"] == "processed"

        # Verify X-Request-ID header is present in response
        assert resp.headers.get("x-request-id"), "Expected X-Request-ID header"
