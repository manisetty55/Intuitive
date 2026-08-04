"""Integration test configuration.

These tests require a running k3s cluster with the full platform deployed.
Set PLATFORM_INTEGRATION_TEST=1 to enable.
Timeout: 600s for the full test suite.
"""

import os
import pytest


def pytest_configure(config):
    """Skip all tests if PLATFORM_INTEGRATION_TEST is not set."""
    if os.environ.get("PLATFORM_INTEGRATION_TEST") != "1":
        pytest.exit(
            "Skipping integration tests: set PLATFORM_INTEGRATION_TEST=1 to enable",
            returncode=0,
        )
