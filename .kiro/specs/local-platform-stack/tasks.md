# Implementation Plan: Local Platform Stack

## Overview

This implementation plan builds a production-grade local platform stack incrementally. The sequence follows the dependency order: bootstrap infrastructure first, then GitOps layer, observability, orchestration, application workloads, AI agent, security hardening, failure injection, and finally tests. Each task builds on prior steps so there is no orphaned code.

## Tasks

- [x] 1. Set up repository structure and bootstrap script
  - [x] 1.1 Create directory structure and bootstrap script skeleton
    - Create the top-level directory layout: `gitops/apps/`, `gitops/observability/`, `gitops/temporal/`, `gitops/applications/`, `gitops/ai-sre/`, `gitops/security/`, `scripts/`, `tests/properties/`, `tests/integration/`, `tests/smoke/`
    - Create `bootstrap.sh` with prerequisite validation (Docker, k3s, helm on PATH), exit code handling (0-4), and existing-cluster detection via `k3s kubectl cluster-info`
    - Implement k3s installation with `--write-kubeconfig-mode 644 --disable traefik`, kubectl context configuration, and wait loop for node Ready + system pods Running (120s timeout)
    - Implement ArgoCD Helm installation into `argocd` namespace, pod readiness wait (120s), and root app-of-apps Application apply pointing to `gitops/apps/`
    - Implement sync wait loop for all child Applications to reach Synced+Healthy (300s timeout)
    - Output descriptive error messages per failure mode with correct exit codes
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 8.2, 8.3, 8.4, 8.7_

  - [x] 1.2 Create ArgoCD app-of-apps Application definitions
    - Create `gitops/apps/observability.yaml` — ArgoCD Application targeting `gitops/observability/` with automated sync, prune, selfHeal, and CreateNamespace
    - Create `gitops/apps/temporal.yaml` — ArgoCD Application targeting `gitops/temporal/`
    - Create `gitops/apps/applications.yaml` — ArgoCD Application targeting `gitops/applications/`
    - Create `gitops/apps/ai-sre.yaml` — ArgoCD Application targeting `gitops/ai-sre/`
    - Each Application must specify `destination.namespace` matching its target namespace
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 2. Implement LGTM observability stack manifests
  - [x] 2.1 Create Prometheus Helm chart values and alert rules
    - Create `gitops/observability/prometheus/Chart.yaml` and `values.yaml` with 15s scrape interval, `kubernetes-pods` service discovery filtering on `prometheus.io/scrape: "true"`, 24h retention
    - Create `gitops/observability/prometheus/rules/sli-slo.yaml` with SLI recording rule `sli:request_latency_p99` using `histogram_quantile(0.99, ...)` and SLO alert `SLOLatencyBreach` firing when p99 > 0.5 for 1m
    - Create `gitops/observability/prometheus/rules/alerts.yaml` with additional alerting rules
    - Ensure resource requests/limits are set on all Prometheus pods in values.yaml
    - _Requirements: 3.1, 3.5, 3.6, 3.7, 3.8, 3.9_

  - [x] 2.2 Create Loki Helm chart values with Promtail
    - Create `gitops/observability/loki/Chart.yaml` and `values.yaml` with local filesystem backend, 24h retention, JSON-structured log ingestion
    - Configure Promtail DaemonSet to scrape all pods across managed namespaces, indexing on `namespace`, `pod`, `container`, `level`
    - Set resource requests/limits on Loki and Promtail pods
    - _Requirements: 3.1, 3.3, 3.9_

  - [x] 2.3 Create Tempo Helm chart values
    - Create `gitops/observability/tempo/Chart.yaml` and `values.yaml` with OTLP gRPC (4317) and OTLP HTTP (4318) receivers, local filesystem backend, 24h retention, search enabled
    - Set resource requests/limits on Tempo pods
    - _Requirements: 3.1, 3.4, 3.9_

  - [x] 2.4 Create Grafana Helm chart values with provisioned dashboards and datasources
    - Create `gitops/observability/grafana/Chart.yaml` and `values.yaml` with anonymous access enabled
    - Create provisioned datasources ConfigMap for Prometheus, Loki, and Tempo
    - Create `gitops/observability/grafana/dashboards/sample-api-overview.json` with request rate, error rate, p50/p95/p99 latency panels
    - Set resource requests/limits on Grafana pods
    - _Requirements: 3.1, 3.2, 3.9_

- [x] 3. Implement Temporal workflow orchestration manifests and worker
  - [x] 3.1 Create Temporal Helm chart values with embedded PostgreSQL
    - Create `gitops/temporal/Chart.yaml` and `values.yaml` deploying Temporal server (frontend, history, matching, worker) with embedded PostgreSQL
    - Configure Temporal metrics endpoint at `:9090/metrics` with Prometheus scrape annotations
    - Set resource requests/limits on all Temporal component pods
    - _Requirements: 4.1, 4.4, 3.9_

  - [x] 3.2 Implement HealthCheckWorkflow in Go
    - Create `gitops/temporal/workflows/` directory with Go module
    - Implement `HealthCheckWorkflow` with activities: QueryAPIHealth, CheckPrometheusErrors, ReportStatus
    - Configure retry policy: max 3 attempts, 5s initial interval, 2.0 backoff coefficient
    - Include `tctl workflow start` trigger script or REST trigger
    - Define `HealthCheckInput` and `HealthCheckResult` structs per design data model
    - _Requirements: 4.2, 4.3, 4.5, 4.6_

- [x] 4. Checkpoint - Ensure infrastructure manifests are complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement Sample API application in Go
  - [x] 5.1 Create Go module with HTTP server and endpoints
    - Initialize Go module in `sample-api/` directory
    - Implement `/health` endpoint returning `{"status":"ok","timestamp":"...","request_id":"..."}`
    - Implement `/api/v1/status` endpoint returning status with `uptime_seconds`
    - Implement `/api/v1/process` endpoint simulating downstream call with trace propagation
    - Implement `/metrics` endpoint using `prometheus/client_golang` with `request_count` (counter), `request_duration_seconds` (histogram), `error_count` (counter)
    - Add middleware for request ID generation and metrics instrumentation
    - _Requirements: 5.1, 5.4_

  - [x] 5.2 Add OpenTelemetry tracing and structured logging
    - Integrate OpenTelemetry Go SDK with OTLP exporter targeting Tempo endpoint
    - Implement W3C Trace Context propagation — extract incoming `traceparent`, inject into downstream calls
    - Integrate `zerolog` for structured JSON logs to stdout with fields: `timestamp`, `level`, `message`, `trace_id`, `span_id`, `request_id`, `method`, `path`, `duration_ms`, `status_code`
    - Implement resilience: non-blocking telemetry export with bounded channel buffer (1000 spans), warning log on backend unreachability, continue serving requests
    - _Requirements: 5.2, 5.3, 5.7, 5.8_

  - [x] 5.3 Create Kubernetes deployment manifests for Sample API
    - Create `gitops/applications/sample-api/deployment.yaml` with resource requests (50m CPU, 64Mi mem) and limits (200m CPU, 128Mi mem)
    - Configure liveness and readiness probes: httpGet /health, initialDelaySeconds 5, periodSeconds 10, timeoutSeconds 3
    - Add `prometheus.io/scrape: "true"` annotation
    - Create `gitops/applications/sample-api/service.yaml` exposing port 8080
    - Create `gitops/applications/sample-api/servicemonitor.yaml` for Prometheus discovery
    - Create `gitops/applications/kustomization.yaml` aggregating resources
    - _Requirements: 5.5, 5.6_

  - [x] 5.4 Write property tests for Sample API (Properties 1-4)
    - **Property 1: API Response Contract** — Generate random valid HTTP requests to `/api/v1/status`, verify response contains `status` (non-empty), `timestamp` (ISO 8601), `request_id` (non-empty unique)
    - **Property 2: Structured Log Completeness** — Generate requests with various methods/paths, parse resulting log lines, verify all required fields present with correct formats
    - **Property 3: Metrics Counter Accuracy** — Generate random sequences of N requests (1-100), verify `request_count` increments by exactly N and histogram has N additional observations
    - **Property 4: Trace Context Propagation** — Generate random valid `traceparent` headers, verify downstream calls include same trace-id with updated parent-id
    - Use Go `rapid` library with minimum 100 iterations per property
    - Create in `tests/properties/api_contract_test.go`
    - **Validates: Requirements 5.1, 5.2, 5.4, 5.7**

- [x] 6. Implement AI SRE Agent in Python
  - [x] 6.1 Create Python project with anomaly detection loop
    - Initialize Python project in `ai-sre-agent/` with `requirements.txt` (LangChain, prometheus-api-client, requests, structlog)
    - Implement anomaly detection polling loop: query Prometheus every 30s for error rate spikes and latency deviations from baseline
    - Log detection events identifying deviating metric name on anomaly detection
    - _Requirements: 6.1_

  - [x] 6.2 Implement analysis pipeline and LLM integration
    - Implement analysis pipeline: query Prometheus metric deviations (±5 min window), query Loki error logs, query Tempo traces exceeding baseline p99
    - Integrate LLM via LangChain (configurable model via env var, default `gpt-4o-mini`)
    - Implement structured output parsing for RCA report format matching the JSON schema
    - Implement fallback: rule-based hypothesis if LLM unavailable
    - _Requirements: 6.2, 6.3_

  - [x] 6.3 Implement RCA report generation and output
    - Generate RCA report with all required fields: id, timestamp, status, timeline, affected_services, symptoms, root_cause (hypothesis + confidence + category), evidence (metrics/logs/traces), remediation
    - Write reports to PVC at `/reports/{timestamp}-{incident-id}.md`
    - Log report to stdout for Loki ingestion as fallback
    - Handle inconclusive case: produce report with status "inconclusive" containing all gathered evidence
    - _Requirements: 6.3, 6.4, 6.6_

  - [x] 6.4 Create Kubernetes deployment manifests for AI SRE Agent
    - Create `gitops/ai-sre/deployment.yaml` with resource requests/limits, configurable LLM env vars
    - Create `gitops/ai-sre/configmap.yaml` with Prometheus/Loki/Tempo endpoint configuration
    - Create `gitops/ai-sre/pvc.yaml` for report persistence
    - Create `gitops/ai-sre/kustomization.yaml` aggregating resources
    - _Requirements: 6.4, 3.9_

  - [x] 6.5 Write property test for RCA report structure (Property 5)
    - **Property 5: RCA Report Structural Completeness** — Generate reports from various failure scenarios (pod_termination, latency_injection, resource_pressure, unknown), validate all required fields present, arrays non-empty, enums valid
    - Use Python `hypothesis` library with minimum 100 iterations
    - Create in `tests/properties/rca_report_test.py`
    - **Validates: Requirements 6.3**

- [x] 7. Checkpoint - Ensure application workloads are complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement security hardening (network policies, RBAC, resource quotas)
  - [x] 8.1 Create default-deny network policies per namespace
    - Create `gitops/security/network-policies/observability-netpol.yaml` — default-deny ingress with empty podSelector for observability namespace
    - Create `gitops/security/network-policies/temporal-netpol.yaml` — default-deny ingress for temporal namespace
    - Create `gitops/security/network-policies/applications-netpol.yaml` — default-deny ingress for applications namespace
    - Create `gitops/security/network-policies/ai-sre-netpol.yaml` — default-deny ingress for ai-sre namespace
    - _Requirements: 7.1_

  - [x] 8.2 Create selective allow network policies for required cross-namespace paths
    - Add allow policies for Prometheus scraping into applications namespace (from observability)
    - Add allow policies for Tempo receiving traces from applications and ai-sre namespaces
    - Add allow policies for AI SRE Agent querying Prometheus, Loki, Tempo in observability namespace
    - Add allow policies for Temporal worker communication paths
    - _Requirements: 7.2_

  - [x] 8.3 Create RBAC resources (ServiceAccounts, Roles, RoleBindings)
    - Create `gitops/security/rbac/serviceaccounts.yaml` with per-namespace ServiceAccounts
    - Create `gitops/security/rbac/rolebindings.yaml` with namespace-scoped RoleBindings (no ClusterRoleBindings)
    - Ensure all Roles contain no wildcard verbs (`*`) and no wildcard resources (`*`)
    - Restrict Secret access to same-namespace ServiceAccounts only
    - _Requirements: 7.3, 7.5_

  - [x] 8.4 Write property tests for security posture (Properties 6-9)
    - **Property 6: Default-Deny Network Policy Coverage** — Iterate all managed namespaces, verify default-deny NetworkPolicy exists with empty podSelector and Ingress policyType
    - **Property 7: Secret Namespace Isolation** — Generate random namespace/secret/SA combinations, verify RBAC permits read access only to same-namespace SAs
    - **Property 8: Universal Resource Limits** — Enumerate all pods in managed namespaces, verify CPU and memory requests/limits with non-zero values
    - **Property 9: Scoped RBAC Without Wildcards** — Enumerate all SAs and bindings, verify RoleBindings (not ClusterRoleBindings) and no wildcards in verbs/resources
    - Use TypeScript `fast-check` library or Go `rapid` for Kubernetes manifest validation
    - Create in `tests/properties/security_test.go`
    - **Validates: Requirements 7.1, 7.3, 7.4, 7.5**

- [x] 9. Implement failure injection scripts
  - [x] 9.1 Create pod termination injection script
    - Create `scripts/inject-pod-kill.sh` that kills Sample API pod via `kubectl delete pod` with label selector
    - Output confirmation message with injection type and target resource
    - Include `--cleanup` flag that restarts the deployment to restore pod
    - Ensure idempotency (safe to run multiple times)
    - _Requirements: 9.1, 9.5, 9.6_

  - [x] 9.2 Create latency injection script
    - Create `scripts/inject-latency.sh` that patches Sample API deployment to add artificial delay (≥2s per request) via env var or sleep sidecar
    - Output confirmation message with injection type and target
    - Include `--cleanup` flag that reverts the deployment patch
    - Ensure idempotency
    - _Requirements: 9.1, 9.5, 9.6_

  - [x] 9.3 Create resource pressure injection script
    - Create `scripts/inject-resource-pressure.sh` that deploys a stress container consuming 80%+ of pod CPU/memory limits
    - Output confirmation message with injection type and target
    - Include `--cleanup` flag that removes the stress container
    - Ensure idempotency
    - _Requirements: 9.1, 9.5, 9.6_

  - [x] 9.4 Write property test for failure injection idempotency (Property 10)
    - **Property 10: Failure Injection Idempotency** — For each script, execute N times (N ∈ [1,3]), run cleanup, verify cluster returns to pre-injection state with all affected pods Ready within 30s
    - Use Go `rapid` or shell-based test harness
    - Create in `tests/properties/failure_injection_test.go`
    - **Validates: Requirements 9.5**

- [x] 10. Checkpoint - Ensure security and failure injection are complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Create documentation and integration tests
  - [x] 11.1 Create README with architecture overview and setup instructions
    - Create `README.md` with sections: setup instructions, architecture overview (with mermaid diagram), design decisions table, roadmap
    - Document expected observable symptoms for each failure scenario (metrics affected, direction of change, log patterns, trace anomalies)
    - Document the AI interaction log structure
    - _Requirements: 8.1, 8.5, 8.6, 9.3_

  - [x] 11.2 Write integration tests
    - Create integration tests in `tests/integration/` verifying end-to-end pipelines:
      - Log ingestion: API → Loki (query logs after request)
      - Trace pipeline: API → Tempo (query trace after request)
      - Metrics scraping: API → Prometheus (query metric after request)
      - SLO breach → alert fires (inject latency → verify alert)
      - Temporal workflow execution to Completed status
      - Failure injection → measurable degradation within 30s
    - Use Go test framework with 600s timeout for full suite
    - _Requirements: 3.3, 3.4, 3.5, 3.8, 4.2, 4.3, 9.2_

  - [x] 11.3 Write smoke tests
    - Create `tests/smoke/run-all.sh` executing quick validation checks:
      - k3s cluster Ready + RBAC enabled
      - ArgoCD/LGTM/Temporal pods Ready
      - Grafana dashboard exists with correct panels
      - SLI recording rule produces data
      - Network policies exist per namespace
      - README contains required sections
      - Three failure scripts exist and are executable
    - _Requirements: 1.1, 1.3, 2.1, 3.1, 3.2, 3.6, 4.1, 7.2, 8.1, 9.1_

- [x] 12. Wire everything together and final validation
  - [x] 12.1 Create ArgoCD sync for security manifests and validate full stack
    - Add security manifests to the appropriate ArgoCD Applications (network policies, RBAC per namespace)
    - Verify the root app-of-apps references all child applications correctly
    - Ensure all namespace labels are set for network policy namespace selectors
    - Validate that bootstrap.sh applies the root Application that triggers full reconciliation of all components
    - _Requirements: 2.2, 2.4, 2.5, 7.1, 7.2, 7.6, 7.7_

  - [x] 12.2 Create end-to-end demonstration script
    - Create `scripts/demo-e2e.sh` that runs a full demonstration: trigger failure injection → wait for AI SRE detection → verify RCA report produced
    - Ensure the scenario completes within 300s from injection to report
    - Validate RCA report identifies the correct failure category
    - _Requirements: 6.5, 9.4_

- [x] 13. Final checkpoint - Full platform validation
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Python is used for Sample API, Temporal workflows, and all property tests (with `hypothesis` library)
- Python is used for AI SRE Agent (with `hypothesis` for property tests)
- The bootstrap script is the single entry point; all subsequent workloads are deployed via ArgoCD GitOps reconciliation
- Failure injection scripts use simple kubectl/shell commands with no additional infrastructure dependencies

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["2.1", "2.2", "2.3", "2.4", "3.1"] },
    { "id": 3, "tasks": ["3.2", "5.1"] },
    { "id": 4, "tasks": ["5.2", "5.3"] },
    { "id": 5, "tasks": ["5.4", "6.1"] },
    { "id": 6, "tasks": ["6.2", "6.3"] },
    { "id": 7, "tasks": ["6.4", "6.5"] },
    { "id": 8, "tasks": ["8.1", "8.2", "8.3"] },
    { "id": 9, "tasks": ["8.4", "9.1", "9.2", "9.3"] },
    { "id": 10, "tasks": ["9.4", "11.1"] },
    { "id": 11, "tasks": ["11.2", "11.3", "12.1"] },
    { "id": 12, "tasks": ["12.2"] }
  ]
}
```
