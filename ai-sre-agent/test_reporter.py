"""Unit tests for the RCA Report generation module."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from reporter import (
    Evidence,
    RCAReport,
    RootCause,
    TimelineEvent,
    create_inconclusive_report,
    create_report,
    format_report_markdown,
    write_report,
)


@pytest.fixture
def sample_timeline():
    """Create a sample timeline for tests."""
    return [
        TimelineEvent(
            timestamp="2024-01-15T10:30:00Z",
            event="Error rate spike detected",
            source="prometheus",
        ),
        TimelineEvent(
            timestamp="2024-01-15T10:30:15Z",
            event="Error logs found in sample-api",
            source="loki",
        ),
        TimelineEvent(
            timestamp="2024-01-15T10:30:20Z",
            event="Slow traces detected",
            source="tempo",
        ),
    ]


@pytest.fixture
def sample_evidence():
    """Create sample evidence for tests."""
    return Evidence(
        metrics=["error_rate: current=0.12, threshold=0.05, range=[0.03, 0.12]"],
        logs=["[sample-api] connection refused to downstream service"],
        traces=["traceID=abc123 service=sample-api duration=2500ms"],
    )


@pytest.fixture
def sample_root_cause():
    """Create a sample root cause for tests."""
    return RootCause(
        hypothesis="Pod termination caused connection failures",
        confidence="high",
        category="pod_termination",
    )


@pytest.fixture
def sample_report(sample_timeline, sample_evidence, sample_root_cause):
    """Create a complete sample report."""
    return create_report(
        status="conclusive",
        timeline=sample_timeline,
        affected_services=["sample-api"],
        symptoms=["error_rate exceeded threshold (0.12 > 0.05)"],
        root_cause=sample_root_cause,
        evidence=sample_evidence,
        remediation=["Restart affected pods", "Investigate pod scheduling constraints"],
    )


class TestCreateReport:
    """Tests for report creation."""

    def test_creates_report_with_all_required_fields(self, sample_report):
        """Verify all required schema fields are present."""
        assert sample_report.id is not None
        assert len(sample_report.id) == 36  # UUID format
        assert sample_report.timestamp is not None
        assert sample_report.status == "conclusive"
        assert len(sample_report.timeline) == 3
        assert sample_report.affected_services == ["sample-api"]
        assert len(sample_report.symptoms) == 1
        assert sample_report.root_cause.hypothesis is not None
        assert sample_report.root_cause.confidence == "high"
        assert sample_report.root_cause.category == "pod_termination"
        assert sample_report.evidence.metrics is not None
        assert sample_report.evidence.logs is not None
        assert sample_report.evidence.traces is not None
        assert len(sample_report.remediation) == 2

    def test_to_dict_matches_json_schema(self, sample_report):
        """Verify to_dict produces all required top-level fields."""
        report_dict = sample_report.to_dict()
        required_keys = [
            "id", "timestamp", "status", "timeline",
            "affected_services", "symptoms", "root_cause",
            "evidence", "remediation",
        ]
        for key in required_keys:
            assert key in report_dict, f"Missing key: {key}"

    def test_to_dict_root_cause_structure(self, sample_report):
        """Verify root_cause contains hypothesis and confidence."""
        rc = sample_report.to_dict()["root_cause"]
        assert "hypothesis" in rc
        assert "confidence" in rc
        assert rc["confidence"] in ["high", "medium", "low"]

    def test_to_dict_evidence_structure(self, sample_report):
        """Verify evidence contains metrics, logs, and traces arrays."""
        ev = sample_report.to_dict()["evidence"]
        assert "metrics" in ev
        assert "logs" in ev
        assert "traces" in ev
        assert isinstance(ev["metrics"], list)
        assert isinstance(ev["logs"], list)
        assert isinstance(ev["traces"], list)


class TestCreateInconclusiveReport:
    """Tests for inconclusive report creation."""

    def test_inconclusive_report_status(self, sample_timeline, sample_evidence):
        """Inconclusive reports have status='inconclusive'."""
        report = create_inconclusive_report(
            timeline=sample_timeline,
            affected_services=["sample-api"],
            symptoms=["error_rate spike"],
            evidence=sample_evidence,
        )
        assert report.status == "inconclusive"

    def test_inconclusive_report_has_low_confidence(self, sample_timeline, sample_evidence):
        """Inconclusive reports have low confidence root cause."""
        report = create_inconclusive_report(
            timeline=sample_timeline,
            affected_services=["sample-api"],
            symptoms=["error_rate spike"],
            evidence=sample_evidence,
        )
        assert report.root_cause.confidence == "low"
        assert report.root_cause.category == "unknown"

    def test_inconclusive_report_contains_evidence(self, sample_timeline, sample_evidence):
        """Inconclusive reports still contain all gathered evidence."""
        report = create_inconclusive_report(
            timeline=sample_timeline,
            affected_services=["sample-api"],
            symptoms=["error_rate spike"],
            evidence=sample_evidence,
        )
        assert len(report.evidence.metrics) > 0
        assert len(report.evidence.logs) > 0
        assert len(report.evidence.traces) > 0


class TestFormatReportMarkdown:
    """Tests for Markdown formatting."""

    def test_contains_report_header(self, sample_report):
        """Markdown output contains the report header with ID."""
        md = format_report_markdown(sample_report)
        assert f"# RCA Report: {sample_report.id}" in md

    def test_contains_status(self, sample_report):
        """Markdown output contains the report status."""
        md = format_report_markdown(sample_report)
        assert "**Status:** conclusive" in md

    def test_contains_affected_services_section(self, sample_report):
        """Markdown output has affected services listed."""
        md = format_report_markdown(sample_report)
        assert "## Affected Services" in md
        assert "- sample-api" in md

    def test_contains_symptoms_section(self, sample_report):
        """Markdown output has symptoms listed."""
        md = format_report_markdown(sample_report)
        assert "## Symptoms" in md

    def test_contains_timeline_table(self, sample_report):
        """Markdown output has a timeline table."""
        md = format_report_markdown(sample_report)
        assert "## Timeline" in md
        assert "| Timestamp | Event | Source |" in md

    def test_contains_root_cause(self, sample_report):
        """Markdown output shows root cause details."""
        md = format_report_markdown(sample_report)
        assert "## Root Cause" in md
        assert "**Hypothesis:**" in md
        assert "**Confidence:** high" in md
        assert "**Category:** pod_termination" in md

    def test_contains_evidence_sections(self, sample_report):
        """Markdown output shows evidence sections."""
        md = format_report_markdown(sample_report)
        assert "## Evidence" in md
        assert "### Metrics" in md
        assert "### Logs" in md
        assert "### Traces" in md

    def test_contains_remediation_steps(self, sample_report):
        """Markdown output shows numbered remediation steps."""
        md = format_report_markdown(sample_report)
        assert "## Remediation" in md
        assert "1. Restart affected pods" in md
        assert "2. Investigate pod scheduling constraints" in md


class TestWriteReport:
    """Tests for report file writing and stdout logging."""

    def test_writes_report_to_file(self, sample_report):
        """Report is written to the configured reports directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("reporter.REPORTS_DIR", tmpdir):
                file_path = write_report(sample_report)
                assert file_path is not None
                assert os.path.exists(file_path)
                assert file_path.endswith(".md")

    def test_written_file_contains_markdown(self, sample_report):
        """Written file contains valid Markdown content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("reporter.REPORTS_DIR", tmpdir):
                file_path = write_report(sample_report)
                with open(file_path, "r") as f:
                    content = f.read()
                assert f"# RCA Report: {sample_report.id}" in content

    def test_handles_write_failure_gracefully(self, sample_report):
        """Returns None when the directory is not writable."""
        with patch("reporter.REPORTS_DIR", "/nonexistent/path/that/does/not/exist"):
            # On most systems this will fail, but mocked Path.mkdir might work
            # Use a truly impossible path
            with patch("reporter.Path.mkdir", side_effect=OSError("Permission denied")):
                file_path = write_report(sample_report)
                assert file_path is None

    def test_filename_contains_timestamp_and_id(self, sample_report):
        """Filename follows the {timestamp}-{incident-id}.md pattern."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("reporter.REPORTS_DIR", tmpdir):
                file_path = write_report(sample_report)
                filename = os.path.basename(file_path)
                assert filename.endswith(".md")
                # Should contain part of the UUID
                short_id = sample_report.id.split("-")[0]
                assert short_id in filename
