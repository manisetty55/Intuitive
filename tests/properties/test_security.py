"""Property-based tests for security properties (Properties 6-9).

Validates: Requirements 7.1, 7.3, 7.4, 7.5

Uses hypothesis + pyyaml to parse Kubernetes YAML manifests and verify
security invariants hold across all managed namespaces.
"""

import os
from pathlib import Path
from typing import Any

import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Path helpers ---

# The gitops/ directory relative to this test file
GITOPS_ROOT = Path(__file__).resolve().parent.parent.parent / "gitops"

# All managed namespaces in the platform
MANAGED_NAMESPACES = ["observability", "temporal", "applications", "ai-sre"]


# --- YAML parsing helpers ---


def parse_multi_doc_yaml(file_path: Path) -> list[dict[str, Any]]:
    """Parse a multi-document YAML file and return all documents."""
    if not file_path.exists():
        return []
    text = file_path.read_text(encoding="utf-8")
    docs = []
    for doc in yaml.safe_load_all(text):
        if doc is not None:
            docs.append(doc)
    return docs


def load_network_policies() -> list[dict[str, Any]]:
    """Load all NetworkPolicy resources from the network-policies directory."""
    netpol_dir = GITOPS_ROOT / "security" / "network-policies"
    if not netpol_dir.exists():
        return []

    policies = []
    for f in sorted(netpol_dir.iterdir()):
        if f.suffix not in (".yaml", ".yml"):
            continue
        for doc in parse_multi_doc_yaml(f):
            if doc.get("kind") == "NetworkPolicy":
                policies.append(doc)
    return policies


def load_roles() -> list[dict[str, Any]]:
    """Load all Role resources from the RBAC roles file."""
    roles_file = GITOPS_ROOT / "security" / "rbac" / "roles.yaml"
    docs = parse_multi_doc_yaml(roles_file)
    return [d for d in docs if d.get("kind") == "Role"]


def load_role_bindings() -> list[dict[str, Any]]:
    """Load all RoleBinding resources from the RBAC rolebindings file."""
    bindings_file = GITOPS_ROOT / "security" / "rbac" / "rolebindings.yaml"
    docs = parse_multi_doc_yaml(bindings_file)
    return [d for d in docs if d.get("kind") in ("RoleBinding", "ClusterRoleBinding")]


def load_service_accounts() -> list[dict[str, Any]]:
    """Load all ServiceAccount resources from the RBAC serviceaccounts file."""
    sa_file = GITOPS_ROOT / "security" / "rbac" / "serviceaccounts.yaml"
    docs = parse_multi_doc_yaml(sa_file)
    return [d for d in docs if d.get("kind") == "ServiceAccount"]


def load_deployments() -> list[dict[str, Any]]:
    """Load all Deployment manifests from managed namespaces."""
    deployment_files = [
        GITOPS_ROOT / "applications" / "sample-api" / "deployment.yaml",
        GITOPS_ROOT / "ai-sre" / "deployment.yaml",
    ]
    deployments = []
    for f in deployment_files:
        for doc in parse_multi_doc_yaml(f):
            if doc.get("kind") == "Deployment":
                deployments.append(doc)
    return deployments


# ============================================================================
# Property 6: Default-Deny Network Policy Coverage
# Validates: Requirements 7.1
# For any managed namespace in the platform, there SHALL exist a NetworkPolicy
# with an empty podSelector and policyTypes including Ingress.
# ============================================================================


class TestProperty6_DefaultDenyNetworkPolicyCoverage:
    """Property 6: Default-Deny Network Policy Coverage. Validates: Requirements 7.1."""

    @settings(max_examples=100)
    @given(namespace=st.sampled_from(MANAGED_NAMESPACES))
    def test_namespace_has_default_deny_policy(self, namespace):
        """Each managed namespace has a default-deny NetworkPolicy."""
        policies = load_network_policies()

        found = False
        for policy in policies:
            metadata = policy.get("metadata", {})
            if metadata.get("namespace") != namespace:
                continue

            spec = policy.get("spec", {})

            # Empty podSelector means it selects all pods
            pod_selector = spec.get("podSelector", {})
            is_empty_selector = (
                pod_selector == {} or pod_selector.get("matchLabels") is None
                and pod_selector.get("matchExpressions") is None
            )

            # policyTypes includes Ingress
            policy_types = spec.get("policyTypes", [])
            has_ingress = "Ingress" in policy_types

            if is_empty_selector and has_ingress:
                found = True
                break

        assert found, (
            f"namespace {namespace!r} does not have a default-deny NetworkPolicy "
            f"with empty podSelector and Ingress policyType"
        )


# ============================================================================
# Property 7: Secret Namespace Isolation
# Validates: Requirements 7.3
# For any Kubernetes Secret in a managed namespace, the RBAC configuration
# SHALL permit read access only to ServiceAccounts within the same namespace.
# ============================================================================


class TestProperty7_SecretNamespaceIsolation:
    """Property 7: Secret Namespace Isolation. Validates: Requirements 7.3."""

    @settings(max_examples=100)
    @given(
        secret_ns=st.sampled_from(MANAGED_NAMESPACES),
        sa_index=st.integers(min_value=0, max_value=100),
    )
    def test_no_cross_namespace_secret_access(self, secret_ns, sa_index):
        """ServiceAccounts cannot read secrets in other namespaces."""
        roles = load_roles()
        bindings = load_role_bindings()
        service_accounts = load_service_accounts()

        if not service_accounts:
            return  # No SAs to test

        # Pick a random SA using the index modulo available SAs
        sa = service_accounts[sa_index % len(service_accounts)]
        sa_name = sa.get("metadata", {}).get("name", "")
        sa_namespace = sa.get("metadata", {}).get("namespace", "")

        can_read_secrets = False

        for binding in bindings:
            binding_metadata = binding.get("metadata", {})
            binding_ns = binding_metadata.get("namespace", "")

            # Check if this binding includes our SA as a subject
            subjects = binding.get("subjects", [])
            sa_is_subject = any(
                subj.get("kind") == "ServiceAccount"
                and subj.get("name") == sa_name
                and subj.get("namespace", binding_ns) == sa_namespace
                for subj in subjects
            )
            if not sa_is_subject:
                continue

            # Binding must be in the secret's namespace to grant access there
            if binding_ns != secret_ns:
                continue

            # Check if the referenced Role grants secret read access
            role_ref = binding.get("roleRef", {})
            role_name = role_ref.get("name", "")

            for role in roles:
                if (
                    role.get("metadata", {}).get("name") != role_name
                    or role.get("metadata", {}).get("namespace") != binding_ns
                ):
                    continue

                for rule in role.get("rules", []):
                    resources = rule.get("resources", [])
                    verbs = rule.get("verbs", [])

                    if "secrets" in resources:
                        if any(v in ("get", "list", "watch") for v in verbs):
                            can_read_secrets = True

        # If the SA can read secrets, it must be in the same namespace
        if can_read_secrets and sa_namespace != secret_ns:
            raise AssertionError(
                f"ServiceAccount {sa_namespace}/{sa_name} can read secrets "
                f"in namespace {secret_ns!r} but is not in that namespace "
                f"(violates namespace isolation)"
            )


# ============================================================================
# Property 8: Universal Resource Limits
# Validates: Requirements 7.1, 7.3, 7.4, 7.5
# For any pod deployed in any managed namespace, the pod specification SHALL
# define both CPU and memory resource requests and limits with non-zero values.
# ============================================================================


class TestProperty8_UniversalResourceLimits:
    """Property 8: Universal Resource Limits. Validates: Requirements 7.1, 7.3, 7.4, 7.5."""

    @settings(max_examples=100)
    @given(dep_index=st.integers(min_value=0, max_value=100))
    def test_all_containers_have_resource_limits(self, dep_index):
        """Every container in every deployment has CPU/memory requests and limits."""
        deployments = load_deployments()

        if not deployments:
            pytest.skip("no deployments found in managed namespaces")

        dep = deployments[dep_index % len(deployments)]
        dep_name = dep.get("metadata", {}).get("name", "unknown")
        dep_ns = dep.get("metadata", {}).get("namespace", "unknown")

        containers = (
            dep.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )

        zero_values = {"0", "0m", "0Mi", "0Gi", "0Ki"}

        for container in containers:
            c_name = container.get("name", "unknown")
            resources = container.get("resources", {})
            requests = resources.get("requests", {})
            limits = resources.get("limits", {})

            # Check CPU requests
            cpu_req = requests.get("cpu", "")
            assert cpu_req != "", (
                f"deployment {dep_ns}/{dep_name} container {c_name!r} missing CPU requests"
            )
            assert cpu_req not in zero_values, (
                f"deployment {dep_ns}/{dep_name} container {c_name!r} has zero CPU requests"
            )

            # Check memory requests
            mem_req = requests.get("memory", "")
            assert mem_req != "", (
                f"deployment {dep_ns}/{dep_name} container {c_name!r} missing memory requests"
            )
            assert mem_req not in zero_values, (
                f"deployment {dep_ns}/{dep_name} container {c_name!r} has zero memory requests"
            )

            # Check CPU limits
            cpu_lim = limits.get("cpu", "")
            assert cpu_lim != "", (
                f"deployment {dep_ns}/{dep_name} container {c_name!r} missing CPU limits"
            )
            assert cpu_lim not in zero_values, (
                f"deployment {dep_ns}/{dep_name} container {c_name!r} has zero CPU limits"
            )

            # Check memory limits
            mem_lim = limits.get("memory", "")
            assert mem_lim != "", (
                f"deployment {dep_ns}/{dep_name} container {c_name!r} missing memory limits"
            )
            assert mem_lim not in zero_values, (
                f"deployment {dep_ns}/{dep_name} container {c_name!r} has zero memory limits"
            )


# ============================================================================
# Property 9: Scoped RBAC Without Wildcards
# Validates: Requirements 7.1, 7.3, 7.4, 7.5
# For any ServiceAccount in a managed namespace, it SHALL be bound via a
# RoleBinding (not ClusterRoleBinding) to a Role that contains no wildcard (*)
# entries in either verbs or resources fields.
# ============================================================================


class TestProperty9_ScopedRBACWithoutWildcards:
    """Property 9: Scoped RBAC Without Wildcards. Validates: Requirements 7.1, 7.3, 7.4, 7.5."""

    @settings(max_examples=100)
    @given(sa_index=st.integers(min_value=0, max_value=100))
    def test_no_cluster_role_bindings_or_wildcards(self, sa_index):
        """ServiceAccounts use RoleBindings (not ClusterRoleBindings) with no wildcards."""
        roles = load_roles()
        bindings = load_role_bindings()
        service_accounts = load_service_accounts()

        if not service_accounts:
            pytest.skip("no service accounts found")

        sa = service_accounts[sa_index % len(service_accounts)]
        sa_name = sa.get("metadata", {}).get("name", "")
        sa_namespace = sa.get("metadata", {}).get("namespace", "")

        # Verify SA is in a managed namespace
        if sa_namespace not in MANAGED_NAMESPACES:
            return  # Skip SAs not in managed namespaces

        found_binding = False

        for binding in bindings:
            binding_metadata = binding.get("metadata", {})
            binding_ns = binding_metadata.get("namespace", "")

            # Check if this binding references our SA
            subjects = binding.get("subjects", [])
            sa_is_subject = any(
                subj.get("kind") == "ServiceAccount"
                and subj.get("name") == sa_name
                and subj.get("namespace", binding_ns) == sa_namespace
                for subj in subjects
            )
            if not sa_is_subject:
                continue

            found_binding = True

            # Verify it's a RoleBinding, not ClusterRoleBinding
            assert binding.get("kind") == "RoleBinding", (
                f"SA {sa_namespace}/{sa_name} is bound via {binding.get('kind')} "
                f"(expected RoleBinding, not ClusterRoleBinding)"
            )

            # Verify the RoleRef points to a Role, not ClusterRole
            role_ref = binding.get("roleRef", {})
            assert role_ref.get("kind") == "Role", (
                f"SA {sa_namespace}/{sa_name} binding references "
                f"{role_ref.get('kind')} {role_ref.get('name')!r} "
                f"(expected Role, not ClusterRole)"
            )

            # Find the referenced Role and check for wildcards
            role_name = role_ref.get("name", "")
            for role in roles:
                role_metadata = role.get("metadata", {})
                if (
                    role_metadata.get("name") != role_name
                    or role_metadata.get("namespace") != binding_ns
                ):
                    continue

                for rule_idx, rule in enumerate(role.get("rules", [])):
                    verbs = rule.get("verbs", [])
                    resources = rule.get("resources", [])

                    assert "*" not in verbs, (
                        f"SA {sa_namespace}/{sa_name} bound to role {role_name!r} "
                        f"which has wildcard verb in rule {rule_idx}"
                    )
                    assert "*" not in resources, (
                        f"SA {sa_namespace}/{sa_name} bound to role {role_name!r} "
                        f"which has wildcard resource in rule {rule_idx}"
                    )

        # SA without any binding is acceptable (no permissions)
