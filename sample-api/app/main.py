"""Sample API - FastAPI application with observability instrumentation."""

import asyncio
import random
import time
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app.tracing import init_tracing
from app.logging_config import init_logging

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

# Initialize structured logging
init_logging()

# Initialize OpenTelemetry tracing (non-blocking: logs warning if backend unreachable)
init_tracing()

from opentelemetry import trace
from opentelemetry.propagate import inject

# Prometheus metrics
REQUEST_COUNT = Counter(
    "request_count",
    "Total number of requests",
    ["method", "path", "status_code"],
)
REQUEST_DURATION = Histogram(
    "request_duration_seconds",
    "Request duration in seconds",
    ["method", "path"],
)
ERROR_COUNT = Counter(
    "error_count",
    "Total number of error responses",
    ["method", "path", "status_code"],
)

# Track startup time for uptime calculation
START_TIME = time.time()

# Create FastAPI app
app = FastAPI(title="Sample API", version="1.0.0")

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def observability_middleware(request: Request, call_next) -> Response:
    """Middleware that handles request ID, metrics, tracing context, and logging."""
    start = time.time()

    # Request ID: use incoming X-Request-ID header or generate a new UUID
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())

    # Get current trace/span context
    span = trace.get_current_span()
    span_context = span.get_span_context()
    trace_id = format(span_context.trace_id, "032x") if span_context.trace_id else ""
    span_id = format(span_context.span_id, "016x") if span_context.span_id else ""

    # Store request_id on request state for handlers to access
    request.state.request_id = request_id

    # Bind structured logging context
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        trace_id=trace_id,
        span_id=span_id,
        method=request.method,
        path=request.url.path,
    )

    # Process request
    response = await call_next(request)

    # Calculate duration
    duration_s = time.time() - start
    duration_ms = round(duration_s * 1000, 2)

    # Record metrics
    status_code = str(response.status_code)
    REQUEST_COUNT.labels(
        method=request.method,
        path=request.url.path,
        status_code=status_code,
    ).inc()
    REQUEST_DURATION.labels(
        method=request.method,
        path=request.url.path,
    ).observe(duration_s)

    if response.status_code >= 400:
        ERROR_COUNT.labels(
            method=request.method,
            path=request.url.path,
            status_code=status_code,
        ).inc()

    # Add request ID to response headers
    response.headers["X-Request-ID"] = request_id

    # Log the completed request
    structlog.contextvars.bind_contextvars(
        duration_ms=duration_ms,
        status_code=response.status_code,
    )
    await logger.ainfo(
        "request completed",
    )

    return response


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health(request: Request):
    """Health check endpoint."""
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request.state.request_id,
    }


@app.get("/api/v1/status")
async def status(request: Request):
    """Status endpoint with uptime information."""
    uptime_seconds = int(time.time() - START_TIME)
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request.state.request_id,
        "uptime_seconds": uptime_seconds,
    }


@app.post("/api/v1/process")
async def process(request: Request):
    """Process endpoint that simulates a downstream call with trace propagation."""
    start = time.time()

    tracer = trace.get_tracer("sample-api")
    with tracer.start_as_current_span("downstream-call") as span:
        # Inject W3C traceparent into a fake outgoing request headers
        carrier: dict[str, str] = {}
        inject(carrier)

        span.set_attribute("downstream.url", "http://downstream-service/api/data")
        span.set_attribute(
            "downstream.traceparent", carrier.get("traceparent", "")
        )

        # Simulate downstream processing (50-200ms)
        delay = random.uniform(0.05, 0.2)
        await asyncio.sleep(delay)

        span.set_attribute("downstream.duration_ms", round(delay * 1000, 2))

    duration_ms = round((time.time() - start) * 1000, 2)

    return {
        "status": "processed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request.state.request_id,
        "duration_ms": duration_ms,
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
