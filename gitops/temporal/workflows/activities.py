"""Activities for the HealthCheckWorkflow."""
import httpx
from temporalio import activity


@activity.defn
async def query_api_health(target_service: str) -> dict:
    """Query the target service's /health endpoint."""
    url = f"http://{target_service}.applications.svc.cluster.local:8080/health"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url)
            return {
                "status_code": resp.status_code,
                "healthy": resp.status_code == 200,
                "response_body": resp.text[:500],
            }
        except Exception as e:
            return {"healthy": False, "error": str(e)}


@activity.defn
async def check_prometheus_errors(target_service: str) -> dict:
    """Query Prometheus for recent errors related to the target service."""
    prom_url = "http://prometheus-server.observability.svc.cluster.local:9090/api/v1/query"
    query = f'sum(increase(error_count{{service="{target_service}"}}[5m]))'

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(prom_url, params={"query": query})
            data = resp.json()
            error_count = 0
            if data.get("status") == "success":
                results = data.get("data", {}).get("result", [])
                for r in results:
                    if len(r.get("value", [])) >= 2:
                        error_count += int(float(r["value"][1]))
            return {"error_count": error_count, "query": query}
        except Exception as e:
            return {"error_count": -1, "error": str(e)}


@activity.defn
async def report_status(result) -> dict:
    """Log and report the health check result."""
    message = (
        f"[HealthCheck] Service: {result.service} | "
        f"Status: {result.status} | ErrorCount: {result.error_count}"
    )
    print(message)
    return {"reported": True, "message": message}
