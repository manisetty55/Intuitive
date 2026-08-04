#!/usr/bin/env bash
#
# inject-pod-kill.sh — Kills Sample API pod to simulate pod termination failure.
#
# Usage:
#   ./scripts/inject-pod-kill.sh            # Inject pod termination
#   ./scripts/inject-pod-kill.sh --cleanup  # Restart deployment to restore pod
#
# Idempotent: deleting an already-deleted pod is safe (kubectl returns success).
# Rollout restart is always safe to run multiple times.

set -euo pipefail

NAMESPACE="applications"
LABEL_SELECTOR="app=sample-api"
DEPLOYMENT="sample-api"
INJECTION_TYPE="pod_termination"
TARGET="sample-api in applications namespace"

cleanup() {
  echo "[inject-pod-kill] Running cleanup: restarting deployment/${DEPLOYMENT} in namespace ${NAMESPACE}"
  kubectl rollout restart deployment "${DEPLOYMENT}" -n "${NAMESPACE}"
  echo "[inject-pod-kill] Cleanup complete. Deployment rollout restart initiated."
  echo "[inject-pod-kill] Injection type: ${INJECTION_TYPE} | Target: ${TARGET} | Action: cleanup"
}

inject() {
  echo "[inject-pod-kill] Injecting failure: deleting pod(s) with label '${LABEL_SELECTOR}' in namespace ${NAMESPACE}"
  kubectl delete pod -l "${LABEL_SELECTOR}" -n "${NAMESPACE}" --ignore-not-found=true
  echo "[inject-pod-kill] Injection complete."
  echo "[inject-pod-kill] Injection type: ${INJECTION_TYPE} | Target: ${TARGET} | Action: inject"
}

# Main
if [[ "${1:-}" == "--cleanup" ]]; then
  cleanup
else
  inject
fi
