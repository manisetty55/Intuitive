"""HealthCheckWorkflow — Temporal workflow for platform health checking."""
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from activities import query_api_health, check_prometheus_errors, report_status


TASK_QUEUE = "health-check-queue"


@dataclass
class HealthCheckInput:
    target_service: str
    check_type: str  # "http", "metrics", "full"


@dataclass
class HealthCheckResult:
    service: str
    status: str  # "healthy", "degraded", "unhealthy"
    timestamp: str = ""
    details: dict = field(default_factory=dict)
    error_count: int = 0


@workflow.defn
class HealthCheckWorkflow:
    @workflow.run
    async def run(self, input: HealthCheckInput) -> HealthCheckResult:
        result = HealthCheckResult(service=input.target_service, status="healthy")

        # Retry policy: max 3 attempts, 5s initial interval, 2.0 backoff
        retry_policy = workflow.RetryPolicy(
            initial_interval=timedelta(seconds=5),
            backoff_coefficient=2.0,
            maximum_attempts=3,
        )

        # Step 1: Query API Health
        if input.check_type in ("http", "full"):
            try:
                api_result = await workflow.execute_activity(
                    query_api_health,
                    input.target_service,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=retry_policy,
                )
                result.details["api_health"] = api_result
            except Exception as e:
                result.status = "unhealthy"
                result.error_count += 1
                result.details["api_health_error"] = str(e)

        # Step 2: Check Prometheus Errors
        if input.check_type in ("metrics", "full"):
            try:
                metrics_result = await workflow.execute_activity(
                    check_prometheus_errors,
                    input.target_service,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=retry_policy,
                )
                result.details["prometheus_check"] = metrics_result
                result.error_count += metrics_result.get("error_count", 0)
            except Exception as e:
                result.error_count += 1
                result.details["prometheus_check_error"] = str(e)

        # Determine overall status
        if result.error_count == 0:
            result.status = "healthy"
        elif result.error_count <= 2:
            result.status = "degraded"
        else:
            result.status = "unhealthy"

        # Step 3: Report Status
        try:
            await workflow.execute_activity(
                report_status,
                result,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_policy,
            )
        except Exception as e:
            result.details["report_error"] = str(e)

        return result
