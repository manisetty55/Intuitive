#!/usr/bin/env bash
#
# bootstrap.sh — Provisions the local platform stack on k3s.
#
# Exit codes:
#   0 — Success (provisioned or cluster already exists)
#   1 — Prerequisite missing (Docker, k3s, helm)
#   2 — k3s provisioning failed
#   3 — ArgoCD installation failed
#   4 — Sync timeout (child Applications not Synced+Healthy)
#

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
readonly K3S_READY_TIMEOUT=120
readonly ARGOCD_READY_TIMEOUT=120
readonly SYNC_TIMEOUT=300
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly GITOPS_APPS_PATH="${SCRIPT_DIR}/gitops/apps"

# ─────────────────────────────────────────────────────────────────────────────
# Utility functions
# ─────────────────────────────────────────────────────────────────────────────
log_info() {
  echo "[INFO]  $(date '+%Y-%m-%d %H:%M:%S') $*"
}

log_error() {
  echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') $*" >&2
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Prerequisite validation
# ─────────────────────────────────────────────────────────────────────────────
check_prerequisites() {
  log_info "Checking prerequisites..."

  local missing=()

  if ! command -v docker &>/dev/null; then
    missing+=("docker — install from https://docs.docker.com/get-docker/")
  fi

  if ! command -v k3s &>/dev/null; then
    missing+=("k3s — install with: curl -sfL https://get.k3s.io | sh -")
  fi

  if ! command -v helm &>/dev/null; then
    missing+=("helm — install from https://helm.sh/docs/intro/install/")
  fi

  if [[ ${#missing[@]} -gt 0 ]]; then
    log_error "Missing prerequisites:"
    for tool in "${missing[@]}"; do
      echo "  • ${tool}"
    done
    exit 1
  fi

  log_info "All prerequisites satisfied."
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Existing cluster detection
# ─────────────────────────────────────────────────────────────────────────────
check_existing_cluster() {
  log_info "Checking for existing k3s cluster..."

  if k3s kubectl cluster-info &>/dev/null; then
    log_info "Existing k3s cluster detected. Skipping provisioning."
    exit 0
  fi

  log_info "No existing cluster found. Proceeding with provisioning."
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: k3s installation and readiness
# ─────────────────────────────────────────────────────────────────────────────
install_k3s() {
  log_info "Installing k3s..."

  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode 644 --disable traefik" sh - || {
    log_error "k3s installation command failed."
    exit 2
  }

  log_info "Configuring kubectl context..."
  export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
  mkdir -p "${HOME}/.kube"
  cp /etc/rancher/k3s/k3s.yaml "${HOME}/.kube/config" 2>/dev/null || true
  chmod 600 "${HOME}/.kube/config" 2>/dev/null || true

  wait_for_k3s_ready
}

wait_for_k3s_ready() {
  log_info "Waiting for k3s node Ready and system pods Running (timeout: ${K3S_READY_TIMEOUT}s)..."

  local deadline=$((SECONDS + K3S_READY_TIMEOUT))

  while [[ $SECONDS -lt $deadline ]]; do
    # Check node readiness
    local node_ready
    node_ready=$(k3s kubectl get nodes -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "False")

    if [[ "${node_ready}" != "True" ]]; then
      sleep 5
      continue
    fi

    # Check system pods (CoreDNS, local-path-provisioner, metrics-server)
    local not_running
    not_running=$(k3s kubectl get pods -n kube-system --no-headers 2>/dev/null \
      | grep -v "Running\|Completed" | wc -l || echo "1")

    if [[ "${not_running}" -eq 0 ]]; then
      log_info "k3s cluster is ready — node Ready, system pods Running."
      return 0
    fi

    sleep 5
  done

  log_error "k3s failed to reach Ready state within ${K3S_READY_TIMEOUT}s."
  log_error "Node status:"
  k3s kubectl get nodes -o wide 2>/dev/null || true
  log_error "System pod status:"
  k3s kubectl get pods -n kube-system 2>/dev/null || true
  exit 2
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: ArgoCD Helm installation
# ─────────────────────────────────────────────────────────────────────────────
install_argocd() {
  log_info "Installing ArgoCD via Helm into 'argocd' namespace..."

  k3s kubectl create namespace argocd --dry-run=client -o yaml | k3s kubectl apply -f - || true

  helm repo add argo https://argoproj.github.io/argo-helm 2>/dev/null || true
  helm repo update

  helm upgrade --install argocd argo/argo-cd \
    --namespace argocd \
    --wait \
    --timeout "${ARGOCD_READY_TIMEOUT}s" || {
    log_error "ArgoCD Helm installation failed."
    log_error "ArgoCD pod statuses:"
    k3s kubectl get pods -n argocd 2>/dev/null || true
    log_error "ArgoCD events:"
    k3s kubectl get events -n argocd --sort-by='.lastTimestamp' 2>/dev/null | tail -20 || true
    exit 3
  }

  wait_for_argocd_ready
}

wait_for_argocd_ready() {
  log_info "Waiting for ArgoCD pods to reach Ready state (timeout: ${ARGOCD_READY_TIMEOUT}s)..."

  local deadline=$((SECONDS + ARGOCD_READY_TIMEOUT))

  while [[ $SECONDS -lt $deadline ]]; do
    local not_ready
    not_ready=$(k3s kubectl get pods -n argocd --no-headers 2>/dev/null \
      | grep -v "Running\|Completed" | wc -l || echo "1")

    if [[ "${not_ready}" -eq 0 ]]; then
      log_info "All ArgoCD pods are Ready."
      return 0
    fi

    sleep 5
  done

  log_error "ArgoCD pods did not reach Ready state within ${ARGOCD_READY_TIMEOUT}s."
  log_error "ArgoCD pod statuses:"
  k3s kubectl get pods -n argocd 2>/dev/null || true
  log_error "ArgoCD events:"
  k3s kubectl get events -n argocd --sort-by='.lastTimestamp' 2>/dev/null | tail -20 || true
  exit 3
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Apply root app-of-apps Application
# ─────────────────────────────────────────────────────────────────────────────
apply_root_application() {
  log_info "Applying root app-of-apps Application pointing to gitops/apps/..."

  k3s kubectl apply -f - <<EOF
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: root-app-of-apps
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/placeholder/gitops-repo.git
    targetRevision: main
    path: gitops/apps
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
EOF

  if [[ $? -ne 0 ]]; then
    log_error "Failed to apply root Application manifest."
    exit 3
  fi

  log_info "Root Application applied successfully."
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Wait for all child Applications to reach Synced+Healthy
# ─────────────────────────────────────────────────────────────────────────────
wait_for_sync() {
  log_info "Waiting for all child Applications to reach Synced+Healthy (timeout: ${SYNC_TIMEOUT}s)..."

  local deadline=$((SECONDS + SYNC_TIMEOUT))

  while [[ $SECONDS -lt $deadline ]]; do
    # Get all ArgoCD Applications (excluding the root)
    local apps
    apps=$(k3s kubectl get applications -n argocd -o json 2>/dev/null)

    if [[ -z "${apps}" ]]; then
      sleep 10
      continue
    fi

    # Check if all applications are Synced and Healthy
    local total
    total=$(echo "${apps}" | jq '.items | length')

    if [[ "${total}" -eq 0 ]]; then
      sleep 10
      continue
    fi

    local synced_healthy
    synced_healthy=$(echo "${apps}" | jq '[.items[] | select(.status.sync.status == "Synced" and .status.health.status == "Healthy")] | length')

    if [[ "${synced_healthy}" -eq "${total}" ]]; then
      log_info "All ${total} Applications are Synced and Healthy."
      return 0
    fi

    local remaining=$((total - synced_healthy))
    log_info "  ${synced_healthy}/${total} Applications synced. Waiting for ${remaining} more..."
    sleep 10
  done

  log_error "Sync timeout: not all Applications reached Synced+Healthy within ${SYNC_TIMEOUT}s."
  log_error "Application statuses:"
  k3s kubectl get applications -n argocd \
    -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status' \
    2>/dev/null || true
  log_error "Non-synced application conditions:"
  k3s kubectl get applications -n argocd -o json 2>/dev/null \
    | jq -r '.items[] | select(.status.sync.status != "Synced" or .status.health.status != "Healthy") | "\(.metadata.name): sync=\(.status.sync.status) health=\(.status.health.status) message=\(.status.conditions // [] | map(.message) | join(", "))"' \
    || true
  exit 4
}

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
main() {
  log_info "=== Local Platform Stack Bootstrap ==="
  log_info ""

  check_prerequisites
  check_existing_cluster
  install_k3s
  install_argocd
  apply_root_application
  wait_for_sync

  log_info ""
  log_info "=== Platform bootstrap complete! ==="
  log_info "All services deployed and synced via ArgoCD."
  log_info ""
  log_info "Access points:"
  log_info "  • kubectl: export KUBECONFIG=/etc/rancher/k3s/k3s.yaml"
  log_info "  • ArgoCD UI: kubectl port-forward svc/argocd-server -n argocd 8080:443"
  log_info ""
  exit 0
}

main "$@"
