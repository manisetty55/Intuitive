"""RCA Report generation module for the AI SRE Agent.

Generates structured Root Cause Analysis reports in Markdown format,
writes them to a PVC-mounted directory, and logs them to stdout as a
fallback for Loki ingestion.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

from config import REPORTS_DIR

logger = structlog.get_logger(__name__)


@dataclass
class TimelineEvent:
    """A single event in the incident timeline."""

    timestamp: str
    event: str
    source: Optional[str] = None  # "prometheus", "loki", "tempo", "agent"

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        result = {"timestamp": self.timestamp, "event": self.event}
        if self.source:
            result["source"] = self.source
        return result


@dataclass
class RootCause:
    """Root cause hypothesis with confidence and category."""

    hypothesis: str
    confidence: str  # "high", "medium", "low"
    category: Optional[str] = None  # "pod_termination", "latency_injection", "resource_pressure", "unknown"

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        result = {"hypothesis": self.hypothesis, "confidence": self.confidence}
        if self.category:
            result["category"] = self.category
        return result


@dataclass
class Evidence:
    """Evidence gathered from observability sources."""

    metrics: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    traces: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "metrics": self.metrics,
            "logs": self.logs,
            "traces": self.traces,
        }


@dataclass
class RCAReport:
    """Complete RCA Report with all required fields per schema."""

    id: str
    timestamp: str
    status: str  # "conclusive" or "inconclusive"
    timeline: list[TimelineEvent]
    affected_services: list[str]
    symptoms: list[str]
    root_cause: RootCause
    evidence: Evidence
    remediation: list[str]

    def to_dict(self) -> dict:
        """Convert the full report to a dictionary matching the JSON schema."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "status": self.status,
            "timeline": [event.to_dict() for event in self.timeline],
            "affected_services": self.affected_services,
            "symptoms": self.symptoms,
            "root_cause": self.root_cause.to_dict(),
            "evidence": self.evidence.to_dict(),
            "remediation": self.remediation,
        }


def create_report(
    status: str,
    timeline: list[TimelineEvent],
    affected_services: list[str],
    symptoms: list[str],
    root_cause: RootCause,
    evidence: Evidence,
    remediation: list[str],
) -> RCAReport:
    """Create a new RCA report with generated ID and timestamp.

    Args:
        status: "conclusive" or "inconclusive"
        timeline: List of timeline events with timestamps
        affected_services: List of affected service names
        symptoms: List of observed symptoms
        root_cause: Root cause hypothesis with confidence and category
        evidence: Evidence gathered from metrics, logs, and traces
        remediation: List of remediation steps

    Returns:
        A fully populated RCAReport instance.
    """
    report_id = str(uuid.uuid4())
    report_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return RCAReport(
        id=report_id,
        timestamp=report_timestamp,
        status=status,
        timeline=timeline,
        affected_services=affected_services,
        symptoms=symptoms,
        root_cause=root_cause,
        evidence=evidence,
        remediation=remediation,
    )


def create_inconclusive_report(
    timeline: list[TimelineEvent],
    affected_services: list[str],
    symptoms: list[str],
    evidence: Evidence,
) -> RCAReport:
    """Create an inconclusive RCA report when root cause cannot be determined.

    The report still contains all gathered evidence for manual review.

    Args:
        timeline: List of timeline events with timestamps
        affected_services: List of affected service names
        symptoms: List of observed symptoms
        evidence: Evidence gathered from metrics, logs, and traces

    Returns:
        An RCAReport with status "inconclusive".
    """
    return create_report(
        status="inconclusive",
        timeline=timeline,
        affected_services=affected_services,
        symptoms=symptoms,
        root_cause=RootCause(
            hypothesis="Unable to determine root cause with sufficient confidence",
            confidence="low",
            category="unknown",
        ),
        evidence=evidence,
        remediation=["Manual investigation recommended based on gathered evidence"],
    )


def format_report_markdown(report: RCAReport) -> str:
    """Format an RCA report as a Markdown document.

    Args:
        report: The RCA report to format.

    Returns:
        A Markdown-formatted string representation of the report.
    """
    lines: list[str] = []

    # Header
    lines.append(f"# RCA Report: {report.id}")
    lines.append("")
    lines.append(f"**Generated:** {report.timestamp}")
    lines.append(f"**Status:** {report.status}")
    lines.append("")

    # Affected Services
    lines.append("## Affected Services")
    lines.append("")
    for service in report.affected_services:
        lines.append(f"- {service}")
    lines.append("")

    # Symptoms
    lines.append("## Symptoms")
    lines.append("")
    for symptom in report.symptoms:
        lines.append(f"- {symptom}")
    lines.append("")

    # Timeline
    lines.append("## Timeline")
    lines.append("")
    lines.append("| Timestamp | Event | Source |")
    lines.append("|-----------|-------|--------|")
    for event in report.timeline:
        source = event.source or "—"
        lines.append(f"| {event.timestamp} | {event.event} | {source} |")
    lines.append("")

    # Root Cause
    lines.append("## Root Cause")
    lines.append("")
    lines.append(f"**Hypothesis:** {report.root_cause.hypothesis}")
    lines.append(f"**Confidence:** {report.root_cause.confidence}")
    if report.root_cause.category:
        lines.append(f"**Category:** {report.root_cause.category}")
    lines.append("")

    # Evidence
    lines.append("## Evidence")
    lines.append("")

    if report.evidence.metrics:
        lines.append("### Metrics")
        lines.append("")
        for metric in report.evidence.metrics:
            lines.append(f"- {metric}")
        lines.append("")

    if report.evidence.logs:
        lines.append("### Logs")
        lines.append("")
        for log_entry in report.evidence.logs:
            lines.append(f"- {log_entry}")
        lines.append("")

    if report.evidence.traces:
        lines.append("### Traces")
        lines.append("")
        for trace in report.evidence.traces:
            lines.append(f"- {trace}")
        lines.append("")

    # Remediation
    lines.append("## Remediation")
    lines.append("")
    for i, step in enumerate(report.remediation, 1):
        lines.append(f"{i}. {step}")
    lines.append("")

    return "\n".join(lines)


def _generate_filename(report: RCAReport) -> str:
    """Generate the report filename from timestamp and incident ID.

    Format: {timestamp}-{incident-id}.md
    The timestamp is sanitized to be filesystem-safe (colons replaced).
    """
    # Sanitize timestamp for filesystem (replace colons, keep readable)
    safe_timestamp = report.timestamp.replace(":", "").replace("-", "").replace("T", "T")
    # Use first 8 chars of UUID for brevity in filename
    short_id = report.id.split("-")[0]
    return f"{safe_timestamp}-{short_id}.md"


def write_report(report: RCAReport) -> Optional[str]:
    """Write the RCA report to the PVC-mounted reports directory.

    Writes a Markdown-formatted report to the configured REPORTS_DIR.
    Also logs the report to stdout as a fallback for Loki ingestion.

    Args:
        report: The RCA report to write.

    Returns:
        The file path where the report was written, or None if file
        write failed (stdout fallback still occurs).
    """
    markdown_content = format_report_markdown(report)
    report_dict = report.to_dict()
    filename = _generate_filename(report)
    file_path = None

    # Attempt to write to PVC
    try:
        reports_dir = Path(REPORTS_DIR)
        reports_dir.mkdir(parents=True, exist_ok=True)
        file_path = str(reports_dir / filename)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        logger.info(
            "rca_report_written",
            report_id=report.id,
            file_path=file_path,
            status=report.status,
        )
    except OSError as exc:
        logger.error(
            "rca_report_write_failed",
            report_id=report.id,
            target_path=str(Path(REPORTS_DIR) / filename),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        file_path = None

    # Always log the report to stdout for Loki ingestion (fallback)
    logger.info(
        "rca_report_generated",
        report_id=report.id,
        report_status=report.status,
        affected_services=report.affected_services,
        root_cause_hypothesis=report.root_cause.hypothesis,
        root_cause_confidence=report.root_cause.confidence,
        root_cause_category=report.root_cause.category,
        symptoms=report.symptoms,
        remediation=report.remediation,
        timeline_events=len(report.timeline),
        evidence_metrics_count=len(report.evidence.metrics),
        evidence_logs_count=len(report.evidence.logs),
        evidence_traces_count=len(report.evidence.traces),
        report_json=json.dumps(report_dict),
    )

    return file_path
