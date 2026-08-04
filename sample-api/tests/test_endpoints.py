"""Unit tests for Sample API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_health_returns_correct_fields(client):
    """GET /health returns status, timestamp, and request_id."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data
    assert "request_id" in data
    assert len(data["request_id"]) > 0


@pytest.mark.anyio
async def test_status_returns_uptime(client):
    """GET /api/v1/status returns uptime_seconds."""
    response = await client.get("/api/v1/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert isinstance(data["uptime_seconds"], int)
    assert data["uptime_seconds"] >= 0


@pytest.mark.anyio
async def test_process_returns_duration(client):
    """POST /api/v1/process returns duration_ms."""
    response = await client.post("/api/v1/process")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processed"
    assert "duration_ms" in data
    assert data["duration_ms"] >= 50  # at least 50ms simulated delay
    assert "timestamp" in data
    assert "request_id" in data


@pytest.mark.anyio
async def test_metrics_returns_prometheus_format(client):
    """GET /metrics returns Prometheus exposition format."""
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    # Should contain our custom metrics
    assert "request_count" in body or "request_duration_seconds" in body


@pytest.mark.anyio
async def test_request_id_generation_unique(client):
    """Each request gets a unique request_id."""
    response1 = await client.get("/health")
    response2 = await client.get("/health")
    id1 = response1.json()["request_id"]
    id2 = response2.json()["request_id"]
    assert id1 != id2


@pytest.mark.anyio
async def test_request_id_header_passthrough(client):
    """X-Request-ID header is used when provided."""
    custom_id = "test-request-id-12345"
    response = await client.get(
        "/health", headers={"X-Request-ID": custom_id}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == custom_id
    # Also returned in response header
    assert response.headers["x-request-id"] == custom_id
