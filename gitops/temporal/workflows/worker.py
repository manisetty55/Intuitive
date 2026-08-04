"""Temporal worker for the HealthCheckWorkflow."""
import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from workflow import HealthCheckWorkflow
from activities import query_api_health, check_prometheus_errors, report_status

TASK_QUEUE = "health-check-queue"


async def main():
    temporal_address = os.environ.get(
        "TEMPORAL_ADDRESS",
        "temporal-frontend.temporal.svc.cluster.local:7233"
    )
    client = await Client.connect(temporal_address)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[HealthCheckWorkflow],
        activities=[query_api_health, check_prometheus_errors, report_status],
    )

    print(f"Starting worker on task queue: {TASK_QUEUE}")
    print(f"Temporal server: {temporal_address}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
