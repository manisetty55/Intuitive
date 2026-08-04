#!/usr/bin/env bash
# Trigger the HealthCheckWorkflow via tctl or the Temporal CLI.
#
# Usage:
#   ./trigger.sh [check_type]
#
# Arguments:
#   check_type - One of: http, metrics, full (default: full)
#
# Examples:
#   ./trigger.sh              # Run a full health check
#   ./trigger.sh http         # Run HTTP health check only
#   ./trigger.sh metrics      # Run Prometheus metrics check only

set -euo pipefail

TEMPORAL_ADDRESS="${TEMPORAL_ADDRESS:-temporal-frontend.temporal.svc.cluster.local:7233}"
TASK_QUEUE="health-check-queue"
WORKFLOW_TYPE="HealthCheckWorkflow"
TARGET_SERVICE="${TARGET_SERVICE:-sample-api}"
CHECK_TYPE="${1:-full}"

# Validate check_type
if [[ ! "$CHECK_TYPE" =~ ^(http|metrics|full)$ ]]; then
  echo "Error: check_type must be one of: http, metrics, full"
  echo "Usage: $0 [check_type]"
  exit 1
fi

# Build workflow input JSON
WORKFLOW_INPUT=$(cat <<EOF
{
  "target_service": "${TARGET_SERVICE}",
  "check_type": "${CHECK_TYPE}"
}
EOF
)

echo "=== Triggering HealthCheckWorkflow ==="
echo "  Temporal Address: ${TEMPORAL_ADDRESS}"
echo "  Task Queue:       ${TASK_QUEUE}"
echo "  Target Service:   ${TARGET_SERVICE}"
echo "  Check Type:       ${CHECK_TYPE}"
echo ""

# Try temporal CLI first (newer), fall back to tctl (legacy)
if command -v temporal &> /dev/null; then
  echo "Using Temporal CLI..."
  temporal workflow start \
    --address "${TEMPORAL_ADDRESS}" \
    --task-queue "${TASK_QUEUE}" \
    --type "${WORKFLOW_TYPE}" \
    --input "${WORKFLOW_INPUT}" \
    --workflow-id "health-check-${TARGET_SERVICE}-$(date +%s)"
elif command -v tctl &> /dev/null; then
  echo "Using tctl..."
  tctl --address "${TEMPORAL_ADDRESS}" \
    workflow start \
    --taskqueue "${TASK_QUEUE}" \
    --workflow_type "${WORKFLOW_TYPE}" \
    --input "${WORKFLOW_INPUT}" \
    --workflow_id "health-check-${TARGET_SERVICE}-$(date +%s)"
else
  echo "Error: Neither 'temporal' CLI nor 'tctl' found on PATH."
  echo "Install the Temporal CLI: https://docs.temporal.io/cli"
  exit 1
fi

echo ""
echo "=== Workflow triggered successfully ==="
echo "Monitor execution at: http://localhost:8080 (Temporal Web UI)"
