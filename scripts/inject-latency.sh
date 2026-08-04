#!/usr/bin/env bash
# inject-latency.sh — Injects artificial latency (≥2s) into the Sample API deployment
# by adding an INJECT_LATENCY_MS environment variable to the container.
#
# Usage:
#   ./scripts/inject-latency.sh            # Inject latency
#   ./scripts/inject-latency.sh --cleanup  # Revert latency injection
#
# Idempotent: safe to run multiple times without side effects.

set -euo pipefail

NAMESPACE="applications"
DEPLOYMENT="sample-api"
CONTAINER="sample-api"
ENV_VAR_NAME="INJECT_LATENCY_MS"
ENV_VAR_VALUE="2000"

# ---------- cleanup mode ----------
if [[ "${1:-}" == "--cleanup" ]]; then
    # Remove the INJECT_LATENCY_MS env var from the container spec.
    # Uses JSON patch to remove the env var by filtering it out.
    # If the env var doesn't exist, the patch is a no-op (idempotent).

    CURRENT_ENV=$(kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" \
        -o jsonpath="{.spec.template.spec.containers[?(@.name=='$CONTAINER')].env}" 2>/dev/null || echo "[]")

    # Check if the env var is present; if not, nothing to clean up
    if echo "$CURRENT_ENV" | grep -q "$ENV_VAR_NAME"; then
        # Build a new env list without INJECT_LATENCY_MS using kubectl patch
        kubectl set env deployment/"$DEPLOYMENT" -n "$NAMESPACE" \
            -c "$CONTAINER" "${ENV_VAR_NAME}-" >/dev/null 2>&1

        # Wait for rollout to complete
        kubectl rollout status deployment/"$DEPLOYMENT" -n "$NAMESPACE" --timeout=60s >/dev/null 2>&1
        echo "Cleanup complete: removed $ENV_VAR_NAME from $DEPLOYMENT in $NAMESPACE namespace"
    else
        echo "Cleanup complete: $ENV_VAR_NAME not present on $DEPLOYMENT in $NAMESPACE namespace (already clean)"
    fi
    exit 0
fi

# ---------- injection mode ----------
# Check if the env var is already set (idempotent — patching again is safe)
CURRENT_ENV=$(kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" \
    -o jsonpath="{.spec.template.spec.containers[?(@.name=='$CONTAINER')].env[?(@.name=='$ENV_VAR_NAME')].value}" 2>/dev/null || echo "")

if [[ "$CURRENT_ENV" == "$ENV_VAR_VALUE" ]]; then
    echo "Injection type: latency_injection, Target: $DEPLOYMENT in $NAMESPACE namespace"
    echo "(already injected — no change needed)"
    exit 0
fi

# Patch the deployment to add the INJECT_LATENCY_MS env var
kubectl set env deployment/"$DEPLOYMENT" -n "$NAMESPACE" \
    -c "$CONTAINER" "${ENV_VAR_NAME}=${ENV_VAR_VALUE}" >/dev/null 2>&1

# Wait for rollout to complete
kubectl rollout status deployment/"$DEPLOYMENT" -n "$NAMESPACE" --timeout=60s >/dev/null 2>&1

echo "Injection type: latency_injection, Target: $DEPLOYMENT in $NAMESPACE namespace"
