"""OpenTelemetry tracing configuration for the Sample API."""

import os
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger(__name__)


def init_tracing() -> None:
    """Initialize OpenTelemetry tracing with OTLP gRPC exporter.

    Configuration via environment variables:
    - OTEL_EXPORTER_OTLP_ENDPOINT: gRPC endpoint (default: tempo.observability.svc.cluster.local:4317)
    - OTEL_SERVICE_NAME: Service name (default: sample-api)

    Resilience: If the OTLP backend is unreachable, a warning is logged and the
    application continues serving with no-op tracing.
    """
    service_name = os.environ.get("OTEL_SERVICE_NAME", "sample-api")
    endpoint = os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "tempo.observability.svc.cluster.local:4317",
    )

    resource = Resource.create({"service.name": service_name})

    provider = TracerProvider(resource=resource)

    try:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        processor = BatchSpanProcessor(exporter, max_queue_size=1000)
        provider.add_span_processor(processor)
    except Exception as exc:
        logger.warning(
            "Failed to initialize OTLP exporter at %s: %s. Tracing will be no-op.",
            endpoint,
            exc,
        )

    trace.set_tracer_provider(provider)

    # Set W3C TraceContext propagator
    propagator = CompositePropagator([TraceContextTextMapPropagator()])
    set_global_textmap(propagator)
