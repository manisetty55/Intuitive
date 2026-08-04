# Requirements Document

## Introduction

A production-grade local platform stack designed as a Staff SRE engineering assessment. The platform runs on k3s (local Kubernetes) and demonstrates the full operational stack — GitOps-managed workloads, LGTM observability, workflow orchestration, a sample instrumented application, and an AI-powered SRE agent for automated root cause analysis. The stack is production-grade in structure, security posture, and operational readiness; the only concession is running on k3s instead of EKS.

## Glossary

- **Platform_Stack**: The complete set of infrastructure components deployed on the k3s cluster, managed via GitOps
- **k3s_Cluster**: A lightweight Kubernetes distribution used as the local compute substrate
- **ArgoCD**: A GitOps continuous delivery tool that reconciles cluster state from a Git repository
- **LGTM_Stack**: The observability stack comprising Loki (logs), Grafana (dashboards), Tempo (traces), and Mimir/Prometheus (metrics)
- **Temporal**: A workflow orchestration engine for durable, fault-tolerant execution of operational workflows
- **Sample_API**: A demonstration HTTP API application that emits structured logs, metrics, and distributed traces
- **AI_SRE_Agent**: An autonomous agent that queries the observability stack to perform automated root cause analysis against failures
- **RCA_Report**: A structured Root Cause Analysis report produced by the AI_SRE_Agent containing timeline, impact, root cause, and remediation
- **Bootstrap_Script**: An automated script that provisions the entire platform from a clean machine state
- **GitOps_Repo**: The Git repository containing all Kubernetes manifests and ArgoCD Application definitions that define the desired cluster state
- **SLI**: Service Level Indicator — a quantitative measure of a service aspect (e.g., request latency p99)
- **SLO**: Service Level Objective — a target value for an SLI (e.g., p99 latency < 500ms)
- **Network_Policy**: A Kubernetes resource that controls pod-to-pod network traffic at the namespace level

## Requirements

### Requirement 1: k3s Cluster Provisioning

**User Story:** As a platform engineer, I want to provision a k3s cluster from scratch using an automated script, so that the entire platform has a reproducible compute foundation.

#### Acceptance Criteria

1. WHEN the Bootstrap_Script is executed, THE k3s_Cluster SHALL be provisioned and reach a Ready state within 120 seconds, where Ready state is defined as: all nodes report Ready status and system pods (CoreDNS, local-path-provisioner, metrics-server) are in Running state
2. WHEN the k3s_Cluster is provisioned, THE Bootstrap_Script SHALL configure kubectl current-context to target the new cluster and verify connectivity by successfully executing a cluster-info request
3. THE k3s_Cluster SHALL enforce RBAC authorization mode for all API requests
4. IF the k3s_Cluster fails to provision, THEN THE Bootstrap_Script SHALL output an error message indicating the failed component and failure reason, and exit with a non-zero status code
5. IF the Bootstrap_Script is executed and a k3s_Cluster is already running, THEN THE Bootstrap_Script SHALL skip provisioning and report that the cluster already exists, without modifying the existing cluster state

### Requirement 2: GitOps Workload Management via ArgoCD

**User Story:** As a platform engineer, I want all workloads managed declaratively through ArgoCD from a GitOps repository, so that no manual kubectl apply is needed after initial bootstrap.

#### Acceptance Criteria

1. WHEN the Bootstrap_Script completes cluster provisioning, THE Bootstrap_Script SHALL install ArgoCD such that all ArgoCD component pods (server, repo-server, application-controller) reach a Ready state within 120 seconds, and configure ArgoCD with repository credentials pointing to the GitOps_Repo
2. WHEN the Bootstrap_Script completes ArgoCD installation, THE ArgoCD SHALL reconcile all Application definitions from the GitOps_Repo to a Synced status with Healthy health state within 300 seconds
3. WHEN a manifest is committed to the GitOps_Repo, THE ArgoCD SHALL detect the change and reconcile the affected Application to a Synced status within 180 seconds
4. THE ArgoCD SHALL deploy at least one ArgoCD Application resource targeting each of the following namespaces: observability, temporal, applications, and ai-sre
5. IF an ArgoCD Application enters a Degraded or Unknown sync state, THEN THE ArgoCD SHALL emit a Kubernetes event containing the application name and failure reason within 60 seconds of state transition
6. IF the ArgoCD cannot reach the GitOps_Repo for more than 300 seconds, THEN THE ArgoCD SHALL report a connection failure condition on affected Application resources and emit a Kubernetes event indicating repository unreachability

### Requirement 3: LGTM Observability Stack

**User Story:** As an SRE, I want a full LGTM observability stack with dashboards, log aggregation, distributed tracing, and metrics collection, so that I can monitor all platform workloads.

#### Acceptance Criteria

1. WHEN the LGTM_Stack is deployed, THE LGTM_Stack SHALL include Grafana, Loki, Tempo, and Prometheus components each with all pods reporting Ready status and all containers running
2. THE Grafana SHALL provide at least one pre-configured dashboard displaying Sample_API request rate, error rate, and latency at the p50, p95, and p99 percentiles
3. THE Loki SHALL ingest JSON-formatted logs from all pods across all namespaces managed by the platform (namespaces created and labeled by the platform deployment)
4. THE Tempo SHALL receive and store distributed traces from instrumented applications with a minimum retention period of 24 hours
5. THE Prometheus SHALL scrape metrics from all pods annotated with `prometheus.io/scrape: "true"` at a 15-second interval
6. THE LGTM_Stack SHALL define at least one SLI measuring Sample_API request latency at the 99th percentile
7. THE LGTM_Stack SHALL define at least one SLO targeting the SLI with a concrete threshold of p99 latency less than 500ms
8. WHEN the SLO is breached, THE Prometheus SHALL fire an alert rule within 60 seconds of the breach condition being met
9. THE LGTM_Stack SHALL enforce resource limits by specifying both CPU and memory requests and limits with non-zero values on all observability component pods

### Requirement 4: Temporal Workflow Orchestration

**User Story:** As a platform engineer, I want Temporal running on the cluster with at least one operational workflow, so that I can demonstrate durable workflow execution as part of the platform.

#### Acceptance Criteria

1. WHEN the Platform_Stack is deployed, THE Temporal SHALL be running with server, frontend, history, matching, and worker components, each with all pods in Ready state (at least 1/1 containers ready)
2. THE Temporal SHALL register at least one workflow that can be triggered on demand and execute to a Completed status
3. WHEN the operational workflow is triggered, THE Temporal SHALL execute it to Completed status within 60 seconds, and the execution history SHALL be queryable via the Temporal API showing workflow start, activity completions, and workflow completion events
4. THE Temporal SHALL emit metrics to Prometheus including workflow execution duration and workflow failure count, scrapeable from a metrics endpoint on Temporal server pods
5. IF a Temporal workflow execution fails, THEN THE Temporal SHALL retry the workflow up to a maximum of 3 attempts with a minimum interval of 5 seconds between retries, as defined in the workflow retry policy
6. WHEN the retry policy maximum attempts are exhausted without success, THE Temporal SHALL mark the workflow execution as Failed and record the failure reason in the execution history

### Requirement 5: Sample API Application

**User Story:** As an SRE, I want a sample API application that emits metrics, structured logs, and distributed traces, so that the observability stack has realistic telemetry to ingest.

#### Acceptance Criteria

1. THE Sample_API SHALL expose at least one HTTP endpoint that returns a JSON response containing at minimum the fields: status, timestamp (ISO 8601), and a request identifier
2. THE Sample_API SHALL emit structured JSON logs to stdout with fields: timestamp (ISO 8601 format), level (one of DEBUG, INFO, WARN, ERROR), message, trace_id, and span_id
3. THE Sample_API SHALL export OpenTelemetry traces to the Tempo collector endpoint, with each inbound HTTP request generating at least one span that includes the service name, operation name, and duration
4. THE Sample_API SHALL expose a Prometheus metrics endpoint at /metrics with the following metrics: request_count (counter), request_duration_seconds (histogram), and error_count (counter)
5. THE Sample_API SHALL define resource requests and limits (CPU and memory) in its pod specification with non-zero values for both requests and limits
6. THE Sample_API SHALL include HTTP-based liveness and readiness probes, each configured with an initialDelaySeconds no greater than 30 seconds, a periodSeconds between 5 and 15 seconds, and a timeoutSeconds no greater than 5 seconds
7. WHEN the Sample_API receives a request, THE Sample_API SHALL propagate W3C Trace Context headers (traceparent) to any downstream HTTP calls made during request processing
8. IF the telemetry backend (Tempo or Prometheus) is unreachable, THEN THE Sample_API SHALL continue serving HTTP requests and log a warning-level message indicating the telemetry export failure

### Requirement 6: AI SRE Agent for Automated Root Cause Analysis

**User Story:** As an SRE, I want an AI agent that queries the observability stack to perform automated root cause analysis on failures, so that incident response is faster and more structured.

#### Acceptance Criteria

1. WHEN a simulated failure is injected (pod kill, latency injection, or resource exhaustion), THE AI_SRE_Agent SHALL detect anomalous signals from the LGTM_Stack within 120 seconds, where detection is confirmed by the agent logging a detection event identifying at least one deviating metric, error log pattern, or trace latency increase
2. WHEN the AI_SRE_Agent detects anomalous signals, THE AI_SRE_Agent SHALL query Prometheus for metric deviations, Loki for error logs occurring within the detection time window, and Tempo for traces exceeding baseline latency
3. WHEN the AI_SRE_Agent completes analysis, THE AI_SRE_Agent SHALL produce an RCA_Report containing: timeline of events with timestamps, affected services by name, observed symptoms, root cause hypothesis, supporting evidence referencing specific metrics or logs, and remediation steps
4. THE RCA_Report SHALL be output as a structured document (Markdown or JSON) to a persistent volume or file path accessible after pod restart
5. THE AI_SRE_Agent SHALL demonstrate at least one end-to-end scenario (inject failure → detect → analyze → produce RCA_Report) completing within 300 seconds from failure injection to RCA_Report availability
6. IF the AI_SRE_Agent cannot determine a root cause, THEN THE AI_SRE_Agent SHALL produce an RCA_Report marked as inconclusive, containing the evidence gathered from each queried data source and the analysis steps attempted

### Requirement 7: Production-Grade Security Posture

**User Story:** As a security-conscious engineer, I want the platform to enforce production-grade security controls, so that the stack demonstrates real-world operational hardening.

#### Acceptance Criteria

1. THE Platform_Stack SHALL enforce namespace isolation by deploying a default-deny ingress Network_Policy in each managed namespace (observability, temporal, applications, ai-sre) that blocks all inbound cross-namespace traffic unless explicitly permitted by an additional Network_Policy rule
2. THE Platform_Stack SHALL define Network_Policy resources in each managed namespace that default-deny all ingress traffic and selectively allow only the cross-namespace paths required for platform operation (e.g., Prometheus scraping into application namespaces, Tempo receiving traces)
3. THE Platform_Stack SHALL manage sensitive values (API keys, database credentials, tokens) using Kubernetes Secrets where RBAC permits read access only to ServiceAccounts within the same namespace that owns the Secret
4. THE Platform_Stack SHALL define both CPU and memory resource limits on all pods deployed in managed namespaces (observability, temporal, applications, ai-sre)
5. THE Platform_Stack SHALL configure per-namespace ServiceAccounts bound via RoleBindings (not ClusterRoleBindings) with roles that contain no wildcard verbs and no wildcard resource definitions
6. WHEN a pod specification without CPU or memory resource limits is synced, THE ArgoCD SHALL set the Application sync status to indicate a warning and emit a Kubernetes event describing the policy violation
7. IF a ServiceAccount in a managed namespace is bound to a ClusterRole with cluster-wide permissions, THEN THE Platform_Stack SHALL flag this as a policy violation during sync or admission

### Requirement 8: Bootstrap and Reproducibility

**User Story:** As a reviewer, I want to clone the repository and bootstrap the entire platform from scratch with minimal manual steps, so that I can verify the assessment independently.

#### Acceptance Criteria

1. THE GitOps_Repo SHALL include a README with setup instructions, architecture overview, design decisions, and a roadmap section
2. WHEN a reviewer clones the GitOps_Repo and executes the Bootstrap_Script, THE Platform_Stack SHALL reach a fully operational state where all deployed services pass their health checks and the platform responds to user requests within 600 seconds on a machine with at least 4 CPU cores, 8 GB RAM, and 40 GB free disk space
3. THE Bootstrap_Script SHALL validate prerequisites (Docker, k3s binary, helm) before attempting provisioning
4. IF a prerequisite is missing, THEN THE Bootstrap_Script SHALL exit without modifying the system and output the missing dependency name and installation instructions
5. THE GitOps_Repo SHALL maintain a git commit history of at least 10 commits with messages that reference the feature or fix being introduced, demonstrating incremental development across multiple sessions
6. THE GitOps_Repo SHALL include an AI interaction log that documents for each development phase the prompts used, the AI-generated outputs applied, and any manual modifications made to those outputs
7. IF the Bootstrap_Script is executed on a system where the Platform_Stack is already running, THEN THE Bootstrap_Script SHALL detect the existing deployment and either skip already-provisioned components or report the conflict without corrupting the existing state

### Requirement 9: Failure Simulation and Demonstration

**User Story:** As a reviewer, I want scripted failure injection scenarios with documented expected outcomes, so that the AI SRE agent's capabilities can be independently verified.

#### Acceptance Criteria

1. THE Platform_Stack SHALL include at least three failure injection scripts: pod termination, artificial latency injection (minimum 2 seconds added per request), and resource pressure simulation (CPU or memory exhaustion to at least 80% of pod limits)
2. WHEN a failure injection script is executed, THE Sample_API SHALL exhibit measurable degradation within 30 seconds, defined as at least one of: error rate increase above 5%, request latency p99 exceeding the defined SLO threshold, or error-level log entries appearing in Loki
3. THE GitOps_Repo SHALL document expected observable symptoms for each failure scenario, including: the specific metrics affected, the expected direction of change, and the log patterns or trace anomalies a reviewer should observe
4. WHEN a failure scenario is executed, THE AI_SRE_Agent SHALL produce an RCA_Report within 180 seconds that identifies the injected failure category (pod termination, latency injection, or resource pressure) matching the script that was run
5. THE failure injection scripts SHALL be idempotent and safe to run repeatedly without corrupting cluster state, and each script SHALL include a corresponding cleanup command that reverts the injected failure within 30 seconds
6. THE failure injection scripts SHALL each be executable as a single shell command with no required parameters, and SHALL output a confirmation message indicating the injection type and target resource upon execution
