#!/usr/bin/env bash
#
# inject-resource-pressure.sh — Deploys a stress container consuming 80%+ of
# Sample API pod CPU/memory limits in the applications namespace.
#
# Usage:
#   ./scripts/inject-resource-pressure.sh            # Inject resource pressure
#   ./scripts/inject-resource-pressure.sh --cleanup  # Remove the stress pod
#
# Idempotent: creating an already-existing stress pod or deleting a non-existent
# one is safe (uses --ignore-not-found and apply semantics).

set -euo pipefail

NAMESPACE="applications"
STRESS_POD_NAME="stress-injector"
INJECTION_TYPE="resource_pressure"
TARGET="applications namespace"

cleanup() {
  echo "[inject-resource-pressure] Running cleanup: deleting pod/${STRESS_POD_NAME} in namespace ${NAMESPACE}"
  kubectl delete pod "${STRESS_POD_NAME}" -n "${NAMESPACE}" --ignore-not-found=true
  echo "[inject-resource-pressure] Cleanup complete. Stress pod removed."
  echo "Injection type: ${INJECTION_TYPE}, Target: ${TARGET}"
}

inject() {
  echo "[inject-resource-pressure] Injecting failure: deploying stress container in namespace ${NAMESPACE}"
  # Deploy stress pod consuming 80%+ of sample-api limits (200m CPU, 128Mi mem)
  # Using: ~160m CPU (1 cpu worker scaled by cgroup), ~100Mi memory
  kubectl apply -n "${NAMESPACE}" -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${STRESS_POD_NAME}
  namespace: ${NAMESPACE}
  labels:
    app: stress-injector
    purpose: failure-injection
spec:
  containers:
    - name: stress
      image: polinux/stress
      command: ["stress"]
      args:
        - "--cpu"
        - "1"
        - "--vm"
        - "1"
        - "--vm-bytes"
        - "100M"
        - "--timeout"
        - "3600s"
      resources:
        requests:
          cpu: "160m"
          memory: "100Mi"
        limits:
          cpu: "200m"
          memory: "128Mi"
  restartPolicy: Never
EOF
  echo "[inject-resource-pressure] Injection complete. Stress pod deployed."
  echo "Injection type: ${INJECTION_TYPE}, Target: ${TARGET}"
}

# Main
if [[ "${1:-}" == "--cleanup" ]]; then
  cleanup
else
  inject
fi
