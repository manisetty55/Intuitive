"""Property-based test for failure injection idempotency (Property 10).

Validates: Requirements 9.5

For any failure injection script in the platform, executing it N times
(where N in [1,3]) followed by its cleanup command SHALL return the cluster
to a state indistinguishable from the pre-injection state, with all affected
pods returning to Ready status within 30 seconds of cleanup.

This test requires a running k3s cluster with the platform deployed.
Set PLATFORM_INTEGRATION_TEST=1 to enable these tests.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Path helpers ---

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Failure injection scripts available for testing
INJECTION_SCRIPTS = [
    {
        "name": "pod-kill",
        "script_path": REPO_ROOT / "scripts" / "inject-pod-kill.sh",
        "namespace": "applications",
    },
    {
        "name": "latency",
        "script_path": REPO_ROOT / "scripts" / "inject-latency.sh",
        "namespace": "applications",
    },
    {
        "name": "resource-pressure",
        "script_path": REPO_ROOT / "scripts" / "inject-resource-pressure.sh",
        "namespace": "applications",
    },
]


# --- Helper functions ---


def skip_if_no_cluster():
    """Skip if no running cluster or PLATFORM_INTEGRATION_TEST is not set."""
    if os.environ.get("PLATFORM_INTEGRATION_TEST") != "1":
        pytest.skip(
            "Skipping integration property test: set PLATFORM_INTEGRATION_TEST=1 to enable"
        )

    # Verify kubectl connectivity
    result = subprocess.run(
        ["kubectl", "cluster-info"],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        pytest.skip("No running Kubernetes cluster detected")

    # Verify namespace exists
    result = subprocess.run(
        ["kubectl", "get", "namespace", "applications"],
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        pytest.skip("'applications' namespace not found — platform not deployed")


def get_pod_state(namespace: str) -> dict:
    """Capture the current state of pods in a namespace."""
    cmd = [
        "kubectl", "get", "pods", "-n", namespace,
        "-o", "jsonpath="
        "{range .items[*]}"
        "{.metadata.labels.app},{.status.phase},"
        "{range .status.conditions[?(@.type==\"Ready\")]}{.status}{end}"
        "{'\\n'}{end}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"kubectl get pods failed: {result.stderr}")

    lines = result.stdout.strip().split("\n")
    ready_count = 0
    total_count = 0
    labels = set()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < 3:
            continue
        app_label = parts[0]
        ready_status = parts[2]

        total_count += 1
        if ready_status == "True":
            ready_count += 1
            if app_label:
                labels.add(app_label)

    return {
        "ready_count": ready_count,
        "total_count": total_count,
        "labels": sorted(labels),
    }


def wait_for_pods_ready(namespace: str, timeout_seconds: int = 30) -> None:
    """Wait up to timeout for all pods in namespace to be Ready."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        cmd = [
            "kubectl", "get", "pods", "-n", namespace,
            "-o", "jsonpath="
            "{range .items[*]}"
            "{.status.conditions[?(@.type==\"Ready\")].status}"
            "{'\\n'}{end}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            time.sleep(2)
            continue

        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        if lines and all(status == "True" for status in lines):
            return
        time.sleep(2)

    raise TimeoutError(
        f"Timed out waiting for pods to be Ready in namespace {namespace}"
    )


def run_script(script_path: Path, *args: str) -> None:
    """Execute a failure injection script."""
    cmd = ["bash", str(script_path)] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f"Script {script_path.name} failed: {result.stderr}"
        )


# ============================================================================
# Property 10: Failure Injection Idempotency
# Validates: Requirements 9.5
# ============================================================================


class TestProperty10_FailureInjectionIdempotency:
    """Property 10: Failure Injection Idempotency. Validates: Requirements 9.5."""

    @settings(max_examples=20)
    @given(
        script_index=st.integers(min_value=0, max_value=len(INJECTION_SCRIPTS) - 1),
        n=st.integers(min_value=1, max_value=3),
    )
    def test_injection_cleanup_restores_state(self, script_index, n):
        """Executing injection N times + cleanup returns cluster to original state."""
        skip_if_no_cluster()

        script = INJECTION_SCRIPTS[script_index]
        script_path = script["script_path"]
        namespace = script["namespace"]

        # Ensure script exists
        if not script_path.exists():
            pytest.skip(f"Injection script not found: {script_path}")

        # Run cleanup first to ensure clean starting state
        try:
            run_script(script_path, "--cleanup")
        except RuntimeError:
            pass  # Cleanup may fail if nothing was injected

        # Wait for pods to stabilize
        wait_for_pods_ready(namespace, timeout_seconds=30)

        # Record pre-injection state
        pre_state = get_pod_state(namespace)

        # Execute the injection script N times
        for i in range(n):
            run_script(script_path)
            time.sleep(1)  # Small delay between executions

        # Run cleanup
        run_script(script_path, "--cleanup")

        # Wait for pods to return to Ready within 30 seconds
        wait_for_pods_ready(namespace, timeout_seconds=30)

        # Capture post-cleanup state
        post_state = get_pod_state(namespace)

        # Verify state matches pre-injection state
        assert pre_state["ready_count"] == post_state["ready_count"], (
            f"[{script['name']}] ready pod count mismatch after cleanup (N={n}): "
            f"pre={pre_state['ready_count']}, post={post_state['ready_count']}"
        )

        assert pre_state["labels"] == post_state["labels"], (
            f"[{script['name']}] app label mismatch after cleanup (N={n}): "
            f"pre={pre_state['labels']}, post={post_state['labels']}"
        )
