#!/usr/bin/env bash
#
# demo-e2e.sh — End-to-end demonstration of the AI SRE pipeline.
#
# Scenario:
#   1. Inject a pod termination failure via inject-pod-kill.sh
#   2. Wait for the AI SRE Agent to detect the anomaly
#   3. Wait for the AI SRE Agent to produce an RCA report
#   4. Validate the RCA report identifies the correct failure category ("pod_termination")
#   5. Print summary with timing information
#
# Total timeout: 300 seconds from injection to report validation.
#
# Usage:
#   ./scripts/demo-e2e.sh
#
# Prerequisites:
#   - k3s cluster running with the full platform stack deployed
#   - kubectl configured to target the cluster
#   - AI SRE Agent running in the ai-sre namespace
#   - Sample API running in the applications namespace

set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOTAL_TIMEOUT=300
POLL_INTERVAL=10
AI_SRE_NAMESPACE="ai-sre"
AI_SRE_POD_LABEL="app=ai-sre-agent"
REPORTS_MOUNT_PATH="/reports"
EXPECTED_CATEGORY="pod_termination"

# --- Colors and formatting ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# --- Helper functions ---
print_header() {
  echo ""
  echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
  echo -e "${BOLD}  $1${NC}"
  echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
  echo ""
}

print_step() {
  echo -e "${BLUE}[STEP]${NC} $1"
}

print_info() {
  echo -e "${YELLOW}[INFO]${NC} $1"
}

print_success() {
  echo -e "${GREEN}[PASS]${NC} $1"
}

print_fail() {
  echo -e "${RED}[FAIL]${NC} $1"
}

print_timer() {
  local elapsed=$1
  local remaining=$((TOTAL_TIMEOUT - elapsed))
  echo -e "       ⏱  Elapsed: ${elapsed}s | Remaining: ${remaining}s | Timeout: ${TOTAL_TIMEOUT}s"
}

elapsed_since() {
  local start=$1
  local now
  now=$(date +%s)
  echo $((now - start))
}

get_ai_sre_pod() {
  kubectl get pod -n "${AI_SRE_NAMESPACE}" -l "${AI_SRE_POD_LABEL}" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo ""
}

# --- Pre-flight checks ---
preflight() {
  print_header "AI SRE End-to-End Demonstration"
  print_step "Running pre-flight checks..."

  # Check kubectl connectivity
  if ! kubectl cluster-info &>/dev/null; then
    print_fail "kubectl cannot reach the cluster. Ensure k3s is running."
    exit 1
  fi
  print_success "kubectl connected to cluster"

  # Check AI SRE Agent pod is running
  local pod
  pod=$(get_ai_sre_pod)
  if [[ -z "${pod}" ]]; then
    print_fail "AI SRE Agent pod not found in namespace '${AI_SRE_NAMESPACE}' with label '${AI_SRE_POD_LABEL}'"
    exit 1
  fi

  local pod_status
  pod_status=$(kubectl get pod "${pod}" -n "${AI_SRE_NAMESPACE}" -o jsonpath='{.status.phase}' 2>/dev/null)
  if [[ "${pod_status}" != "Running" ]]; then
    print_fail "AI SRE Agent pod '${pod}' is not Running (status: ${pod_status})"
    exit 1
  fi
  print_success "AI SRE Agent pod '${pod}' is Running"

  # Check Sample API is running
  local api_pods
  api_pods=$(kubectl get pods -n applications -l app=sample-api --field-selector=status.phase=Running -o name 2>/dev/null | wc -l)
  if [[ "${api_pods}" -lt 1 ]]; then
    print_fail "Sample API pod not found or not Running in 'applications' namespace"
    exit 1
  fi
  print_success "Sample API is Running (${api_pods} pod(s))"

  # Check injection script exists
  if [[ ! -x "${SCRIPT_DIR}/inject-pod-kill.sh" ]]; then
    print_fail "inject-pod-kill.sh not found or not executable at ${SCRIPT_DIR}/inject-pod-kill.sh"
    exit 1
  fi
  print_success "inject-pod-kill.sh is available"

  echo ""
}

# --- Record existing reports to detect new ones ---
get_existing_reports() {
  local pod
  pod=$(get_ai_sre_pod)
  if [[ -n "${pod}" ]]; then
    kubectl exec "${pod}" -n "${AI_SRE_NAMESPACE}" -- ls "${REPORTS_MOUNT_PATH}" 2>/dev/null || echo ""
  fi
}

# --- Phase 1: Inject failure ---
inject_failure() {
  print_header "Phase 1: Failure Injection"
  print_step "Injecting pod termination failure via inject-pod-kill.sh..."
  echo ""

  "${SCRIPT_DIR}/inject-pod-kill.sh"

  echo ""
  print_success "Pod termination injected successfully"
  print_info "The AI SRE Agent polls every 30s — waiting for detection..."
}

# --- Phase 2: Wait for AI SRE detection ---
wait_for_detection() {
  local start_time=$1
  print_header "Phase 2: Waiting for AI SRE Agent Detection"

  local pod
  pod=$(get_ai_sre_pod)

  # We check the AI SRE Agent logs for a detection event
  # The detector logs anomaly detections with structlog
  local detected=false
  local detection_marker="anomaly_detected\|detection_event\|deviating_metric\|anomaly detected"

  while true; do
    local elapsed
    elapsed=$(elapsed_since "${start_time}")

    if [[ ${elapsed} -ge ${TOTAL_TIMEOUT} ]]; then
      print_fail "Timeout (${TOTAL_TIMEOUT}s) reached waiting for anomaly detection"
      return 1
    fi

    # Refresh pod name in case it restarted
    pod=$(get_ai_sre_pod)
    if [[ -z "${pod}" ]]; then
      print_info "AI SRE Agent pod restarting... waiting"
      sleep "${POLL_INTERVAL}"
      continue
    fi

    # Check agent logs for detection events since injection
    local recent_logs
    recent_logs=$(kubectl logs "${pod}" -n "${AI_SRE_NAMESPACE}" --since="${elapsed}s" 2>/dev/null || echo "")

    if echo "${recent_logs}" | grep -qi "${detection_marker}"; then
      detected=true
      print_success "AI SRE Agent detected anomaly!"
      print_timer "${elapsed}"
      echo ""
      print_info "Detection log excerpt:"
      echo "${recent_logs}" | grep -i "${detection_marker}" | tail -3 | sed 's/^/       /'
      echo ""
      return 0
    fi

    # Also check Loki for the detection event (alternative path)
    # Try querying Loki if direct log check didn't find it
    local loki_result
    loki_result=$(kubectl logs "${pod}" -n "${AI_SRE_NAMESPACE}" --since="${elapsed}s" 2>/dev/null | grep -i "rca_report_generated\|report_written\|rca_report_written" || echo "")
    if [[ -n "${loki_result}" ]]; then
      # If we see a report already generated, detection happened implicitly
      detected=true
      print_success "AI SRE Agent detected anomaly (report generation in progress)"
      print_timer "${elapsed}"
      return 0
    fi

    print_info "Polling... no detection yet"
    print_timer "${elapsed}"
    sleep "${POLL_INTERVAL}"
  done
}

# --- Phase 3: Wait for RCA report ---
# Global variable to pass report filename back to main
DETECTED_REPORT_FILE=""

wait_for_report() {
  local start_time=$1
  local existing_reports=$2
  print_header "Phase 3: Waiting for RCA Report Generation"

  local pod
  local report_found=false
  DETECTED_REPORT_FILE=""

  while true; do
    local elapsed
    elapsed=$(elapsed_since "${start_time}")

    if [[ ${elapsed} -ge ${TOTAL_TIMEOUT} ]]; then
      print_fail "Timeout (${TOTAL_TIMEOUT}s) reached waiting for RCA report"
      return 1
    fi

    pod=$(get_ai_sre_pod)
    if [[ -z "${pod}" ]]; then
      print_info "AI SRE Agent pod not ready... waiting"
      sleep "${POLL_INTERVAL}"
      continue
    fi

    # Check for new reports in the PVC
    local current_reports
    current_reports=$(kubectl exec "${pod}" -n "${AI_SRE_NAMESPACE}" -- ls "${REPORTS_MOUNT_PATH}" 2>/dev/null || echo "")

    # Find reports that weren't in the existing list
    for report_file in ${current_reports}; do
      if ! echo "${existing_reports}" | grep -qF "${report_file}"; then
        DETECTED_REPORT_FILE="${report_file}"
        report_found=true
        break
      fi
    done

    if [[ "${report_found}" == "true" ]]; then
      print_success "New RCA report detected: ${DETECTED_REPORT_FILE}"
      print_timer "${elapsed}"
      echo ""
      return 0
    fi

    # Alternative: check agent logs for rca_report_generated event
    local log_check
    log_check=$(kubectl logs "${pod}" -n "${AI_SRE_NAMESPACE}" --since="${elapsed}s" 2>/dev/null | grep -i "rca_report_generated\|rca_report_written" | tail -1 || echo "")
    if [[ -n "${log_check}" ]]; then
      print_success "RCA report generation confirmed via logs"
      print_timer "${elapsed}"
      # Try to get the filename from the log
      DETECTED_REPORT_FILE=$(echo "${log_check}" | grep -oP 'file_path["\s:=]+\K[^",}\s]+' || echo "")
      # If we couldn't parse the filename, list reports again to find the new one
      if [[ -z "${DETECTED_REPORT_FILE}" ]]; then
        current_reports=$(kubectl exec "${pod}" -n "${AI_SRE_NAMESPACE}" -- ls "${REPORTS_MOUNT_PATH}" 2>/dev/null || echo "")
        for report_file in ${current_reports}; do
          if ! echo "${existing_reports}" | grep -qF "${report_file}"; then
            DETECTED_REPORT_FILE="${report_file}"
            break
          fi
        done
      fi
      return 0
    fi

    print_info "Polling... no new report yet"
    print_timer "${elapsed}"
    sleep "${POLL_INTERVAL}"
  done
}

# --- Phase 4: Validate RCA report ---
validate_report() {
  local start_time=$1
  local report_file=$2
  print_header "Phase 4: Validating RCA Report"

  local pod
  pod=$(get_ai_sre_pod)

  if [[ -z "${pod}" ]]; then
    print_fail "AI SRE Agent pod not available for report validation"
    return 1
  fi

  # Read the report content
  local report_path="${REPORTS_MOUNT_PATH}/${report_file}"
  local report_content
  report_content=$(kubectl exec "${pod}" -n "${AI_SRE_NAMESPACE}" -- cat "${report_path}" 2>/dev/null || echo "")

  if [[ -z "${report_content}" ]]; then
    # Try getting content from logs instead
    print_info "Could not read report file directly, checking agent logs..."
    report_content=$(kubectl logs "${pod}" -n "${AI_SRE_NAMESPACE}" 2>/dev/null | grep "rca_report_generated" | tail -1 || echo "")

    if [[ -z "${report_content}" ]]; then
      print_fail "Could not retrieve RCA report content"
      return 1
    fi
  fi

  print_step "Report content retrieved. Validating failure category..."
  echo ""

  # Check that the report identifies the correct failure category
  local category_found=false

  if echo "${report_content}" | grep -qi "${EXPECTED_CATEGORY}"; then
    category_found=true
  fi

  # Also check for category in various formats the report might use
  if echo "${report_content}" | grep -qi "pod.termination\|pod termination\|pod_termination"; then
    category_found=true
  fi

  if [[ "${category_found}" == "true" ]]; then
    print_success "RCA report correctly identifies failure category: ${EXPECTED_CATEGORY}"
  else
    print_fail "RCA report does NOT identify expected category '${EXPECTED_CATEGORY}'"
    print_info "Report excerpt:"
    echo "${report_content}" | head -30 | sed 's/^/       /'
    echo ""
    return 1
  fi

  # Print report summary
  echo ""
  print_info "RCA Report Summary:"
  echo "  ─────────────────────────────────────────────────────"
  echo "${report_content}" | head -40 | sed 's/^/  /'
  echo "  ─────────────────────────────────────────────────────"
  echo ""

  local elapsed
  elapsed=$(elapsed_since "${start_time}")
  print_success "Validation complete"
  print_timer "${elapsed}"

  return 0
}

# --- Cleanup ---
cleanup() {
  print_header "Cleanup"
  print_step "Reverting failure injection..."
  "${SCRIPT_DIR}/inject-pod-kill.sh" --cleanup
  print_success "Cleanup complete — pod termination reverted"
}

# --- Summary ---
print_summary() {
  local start_time=$1
  local result=$2
  local elapsed
  elapsed=$(elapsed_since "${start_time}")

  print_header "Demonstration Summary"

  echo -e "  ${BOLD}Scenario:${NC}          Pod termination → AI SRE detection → RCA report"
  echo -e "  ${BOLD}Expected Category:${NC} ${EXPECTED_CATEGORY}"
  echo -e "  ${BOLD}Total Time:${NC}        ${elapsed}s (budget: ${TOTAL_TIMEOUT}s)"
  echo -e "  ${BOLD}Timeout Budget:${NC}    $((TOTAL_TIMEOUT - elapsed))s remaining"
  echo ""

  if [[ "${result}" == "0" ]]; then
    echo -e "  ${GREEN}${BOLD}RESULT: PASS ✓${NC}"
    echo ""
    echo -e "  The AI SRE Agent successfully:"
    echo -e "    1. Detected the pod termination failure"
    echo -e "    2. Produced an RCA report within the ${TOTAL_TIMEOUT}s budget"
    echo -e "    3. Correctly identified the failure category as '${EXPECTED_CATEGORY}'"
  else
    echo -e "  ${RED}${BOLD}RESULT: FAIL ✗${NC}"
    echo ""
    echo -e "  The end-to-end demonstration did not complete successfully."
    echo -e "  Check the AI SRE Agent logs for more details:"
    echo -e "    kubectl logs -n ${AI_SRE_NAMESPACE} -l ${AI_SRE_POD_LABEL} --tail=50"
  fi

  echo ""
}

# --- Main ---
main() {
  local start_time
  start_time=$(date +%s)
  local result=0

  # Pre-flight
  preflight

  # Record existing reports before injection
  print_step "Recording existing reports for diff detection..."
  local existing_reports
  existing_reports=$(get_existing_reports)
  print_info "Found $(echo "${existing_reports}" | wc -w | tr -d ' ') existing report(s)"
  echo ""

  # Phase 1: Inject failure
  inject_failure

  # Phase 2: Wait for detection
  if ! wait_for_detection "${start_time}"; then
    result=1
  fi

  # Phase 3: Wait for RCA report (only if detection succeeded)
  if [[ "${result}" == "0" ]]; then
    if ! wait_for_report "${start_time}" "${existing_reports}"; then
      result=1
    fi
  fi

  # Phase 4: Validate report (only if report was found)
  if [[ "${result}" == "0" ]] && [[ -n "${DETECTED_REPORT_FILE}" ]]; then
    if ! validate_report "${start_time}" "${DETECTED_REPORT_FILE}"; then
      result=1
    fi
  fi

  # Always cleanup
  cleanup

  # Print summary
  print_summary "${start_time}" "${result}"

  exit "${result}"
}

# Ensure cleanup runs on script interruption
trap 'echo ""; print_info "Interrupted — running cleanup..."; cleanup; exit 130' INT TERM

main "$@"
