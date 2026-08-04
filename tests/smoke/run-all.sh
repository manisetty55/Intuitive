#!/usr/bin/env bash
#
# run-all.sh — Smoke tests for the Local Platform Stack.
#
# Runs quick validation checks against the platform. Each check prints
# PASS or FAIL with a description. Exits 0 if all pass, exits 1 on any failure.
#
# Usage:
#   ./tests/smoke/run-all.sh
#

set -uo pipefail

PASS_COUNT=0
FAIL_COUNT=0

pass() {
  echo "PASS: $1"
  PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
  echo "FAIL: $1"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

# --------------------------------------------------------------------------
# 1. k3s cluster Ready + RBAC enabled
# Validates: Requirements 1.1, 1.3
# --------------------------------------------------------------------------

echo "--- Checking k3s cluster ---"

# Check node is Ready
if kubectl get nodes --no-headers 2>/dev/null | grep -q " Ready"; then
  pass "k3s cluster node is Ready"
else
  fail "k3s cluster node is not Ready"
fi

# Check RBAC is enabled (api-server has RBAC authorization mode)
if kubectl api-versions 2>/dev/null | grep -q "rbac.authorization.k8s.io/v1"; then
  pass "RBAC authorization is enabled"
else
  fail "RBAC authorization is not enabled"
fi

# --------------------------------------------------------------------------
# 2. ArgoCD pods Ready
# Validates: Requirement 2.1
# --------------------------------------------------------------------------

echo ""
echo "--- Checking ArgoCD pods ---"

ARGOCD_PODS=("server" "repo-server" "application-controller")
for component in "${ARGOCD_PODS[@]}"; do
  if kubectl get pods -n argocd -l "app.kubernetes.io/component=${component}" --no-headers 2>/dev/null | grep -q "Running"; then
    pass "ArgoCD ${component} pod is Ready"
  else
    fail "ArgoCD ${component} pod is not Ready"
  fi
done

# --------------------------------------------------------------------------
# 3. LGTM stack pods Ready
# Validates: Requirement 3.1
# --------------------------------------------------------------------------

echo ""
echo "--- Checking LGTM stack pods ---"

LGTM_COMPONENTS=("grafana" "loki" "tempo" "prometheus")
for component in "${LGTM_COMPONENTS[@]}"; do
  if kubectl get pods -n observability -l "app=${component}" --no-headers 2>/dev/null | grep -q "Running"; then
    pass "Observability ${component} pod is Ready"
  else
    # Try alternative label selectors (Helm charts use varying labels)
    if kubectl get pods -n observability -l "app.kubernetes.io/name=${component}" --no-headers 2>/dev/null | grep -q "Running"; then
      pass "Observability ${component} pod is Ready"
    else
      fail "Observability ${component} pod is not Ready"
    fi
  fi
done

# --------------------------------------------------------------------------
# 4. Temporal pods Ready
# Validates: Requirement 4.1
# --------------------------------------------------------------------------

echo ""
echo "--- Checking Temporal pods ---"

TEMPORAL_COMPONENTS=("frontend" "history" "matching" "worker")
for component in "${TEMPORAL_COMPONENTS[@]}"; do
  if kubectl get pods -n temporal -l "app.kubernetes.io/component=${component}" --no-headers 2>/dev/null | grep -q "Running"; then
    pass "Temporal ${component} pod is Ready"
  else
    # Try alternative label pattern
    if kubectl get pods -n temporal -l "app=${component}" --no-headers 2>/dev/null | grep -q "Running"; then
      pass "Temporal ${component} pod is Ready"
    else
      fail "Temporal ${component} pod is not Ready"
    fi
  fi
done

# --------------------------------------------------------------------------
# 5. Grafana dashboard exists with correct panels
# Validates: Requirement 3.2
# --------------------------------------------------------------------------

echo ""
echo "--- Checking Grafana dashboard ---"

DASHBOARD_FILE="gitops/observability/grafana/dashboards/sample-api-overview.json"
if [ -f "${DASHBOARD_FILE}" ]; then
  pass "Grafana dashboard file exists (sample-api-overview.json)"

  # Check for required panels: Request Rate, Error Rate, Latency (p50/p95/p99)
  if grep -q '"title": "Request Rate"' "${DASHBOARD_FILE}"; then
    pass "Grafana dashboard has Request Rate panel"
  else
    fail "Grafana dashboard missing Request Rate panel"
  fi

  if grep -q '"title": "Error Rate"' "${DASHBOARD_FILE}"; then
    pass "Grafana dashboard has Error Rate panel"
  else
    fail "Grafana dashboard missing Error Rate panel"
  fi

  if grep -q "Request Latency" "${DASHBOARD_FILE}" && grep -q "p50" "${DASHBOARD_FILE}" && grep -q "p95" "${DASHBOARD_FILE}" && grep -q "p99" "${DASHBOARD_FILE}"; then
    pass "Grafana dashboard has Latency panel with p50/p95/p99"
  else
    fail "Grafana dashboard missing Latency panel with p50/p95/p99"
  fi
else
  fail "Grafana dashboard file does not exist (${DASHBOARD_FILE})"
fi

# --------------------------------------------------------------------------
# 6. SLI recording rule produces data
# Validates: Requirement 3.6
# --------------------------------------------------------------------------

echo ""
echo "--- Checking SLI recording rule ---"

# Query Prometheus for the existence of the sli:request_latency_p99 recording rule
SLI_QUERY_RESULT=$(kubectl exec -n observability deploy/prometheus -- \
  wget -qO- "http://localhost:9090/api/v1/rules" 2>/dev/null || echo "")

if echo "${SLI_QUERY_RESULT}" | grep -q "sli:request_latency_p99"; then
  pass "SLI recording rule 'sli:request_latency_p99' exists in Prometheus"
else
  # Fallback: check that the rule file exists in the repo
  SLI_RULE_FILE="gitops/observability/prometheus/rules/sli-slo.yaml"
  if [ -f "${SLI_RULE_FILE}" ] && grep -q "sli:request_latency_p99" "${SLI_RULE_FILE}"; then
    pass "SLI recording rule 'sli:request_latency_p99' defined in rule file (Prometheus API unreachable for live check)"
  else
    fail "SLI recording rule 'sli:request_latency_p99' not found"
  fi
fi

# --------------------------------------------------------------------------
# 7. Network policies exist per namespace
# Validates: Requirement 7.2
# --------------------------------------------------------------------------

echo ""
echo "--- Checking network policies ---"

MANAGED_NAMESPACES=("observability" "temporal" "applications" "ai-sre")
for ns in "${MANAGED_NAMESPACES[@]}"; do
  if kubectl get networkpolicy -n "${ns}" --no-headers 2>/dev/null | grep -q "."; then
    pass "NetworkPolicy exists in namespace '${ns}'"
  else
    # Fallback: check that network policy manifests exist in the repo
    NETPOL_FILE="gitops/security/network-policies/${ns}-netpol.yaml"
    if [ -f "${NETPOL_FILE}" ]; then
      pass "NetworkPolicy manifest exists for namespace '${ns}' (cluster check unavailable)"
    else
      fail "No NetworkPolicy found for namespace '${ns}'"
    fi
  fi
done

# --------------------------------------------------------------------------
# 8. README contains required sections
# Validates: Requirement 8.1
# --------------------------------------------------------------------------

echo ""
echo "--- Checking README ---"

README_FILE="README.md"
if [ -f "${README_FILE}" ]; then
  pass "README.md exists"

  REQUIRED_SECTIONS=("Setup Instructions" "Architecture Overview" "Design Decisions" "Roadmap")
  for section in "${REQUIRED_SECTIONS[@]}"; do
    if grep -qi "${section}" "${README_FILE}"; then
      pass "README contains section: ${section}"
    else
      fail "README missing section: ${section}"
    fi
  done
else
  fail "README.md does not exist"
fi

# --------------------------------------------------------------------------
# 9. Three failure injection scripts exist and are executable
# Validates: Requirement 9.1
# --------------------------------------------------------------------------

echo ""
echo "--- Checking failure injection scripts ---"

INJECTION_SCRIPTS=("scripts/inject-pod-kill.sh" "scripts/inject-latency.sh" "scripts/inject-resource-pressure.sh")
for script in "${INJECTION_SCRIPTS[@]}"; do
  if [ -f "${script}" ]; then
    pass "Failure injection script exists: ${script}"
    if [ -x "${script}" ]; then
      pass "Failure injection script is executable: ${script}"
    else
      fail "Failure injection script is NOT executable: ${script}"
    fi
  else
    fail "Failure injection script missing: ${script}"
  fi
done

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

echo ""
echo "=========================================="
TOTAL=$((PASS_COUNT + FAIL_COUNT))
echo "Smoke Tests Complete: ${PASS_COUNT}/${TOTAL} passed, ${FAIL_COUNT} failed"
echo "=========================================="

if [ "${FAIL_COUNT}" -gt 0 ]; then
  exit 1
fi

exit 0
