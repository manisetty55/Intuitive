"""Property-based test for RCA Report Structural Completeness (Property 5).

Validates: Requirements 6.3

For any RCA report produced by the AI SRE Agent (whether conclusive or
inconclusive), the report SHALL contain all required fields:
- timeline (non-empty array of timestamped events)
- affected_services (non-empty array)
- symptoms (non-empty array)
- root_cause (object with hypothesis and confidence)
- evidence (object with metrics, logs, and traces arrays)
- remediation (array of steps)

Enums validated:
- status: "conclusive" or "inconclusive"
- root_cause.confidence: "high", "medium", "low"
- root_cause.category: "pod_termination", "latency_injection", "resource_pressure", "unknown"
- timeline[].source (optional): "prometheus", "loki", "tempo", "agent"
"""

import sys
from pathlib import Path

# Add the ai-sre-agent directory to the Python path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "ai-sre-agent"))

from hypothesis import given, settings
from hypothesis import strategies as st

from reporter import (
    Evidence,
    RCAReport,
    RootCause,
    TimelineEvent,
    create_inconclusive_report,
    create_report,
)

# --- Strategies ---

VALID_STATUSES = ["conclusive", "inconclusive"]
VALID_CONFIDENCES = ["high", "medium", "low"]
VALID_CATEGORIES = ["pod_termination", "latency_injection", "resource_pressure", "unknown"]
VALID_SOURCES = ["prometheus", "loki", "tempo", "agent"]


@st.composite
def timeline_events(draw):
    """Generate a non-empty list of TimelineEvent instances."""
    count = draw(st.integers(min_value=1, max_value=10))
    events = []
    for _ in range(count):
        ts = draw(st.from_regex(
            r"2024-0[1-9]-[012][0-9]T[01][0-9]:[0-5][0-9]:[0-5][0-9]Z",
            fullmatch=True,
        ))
        event_text = draw(st.text(min_size=1, max_size=100, alphabet=st.characters(
            whitelist_categories=("L", "N", "P", "Z"),
        )))
        source = draw(st.one_of(st.none(), st.sampled_from(VALID_SOURCES)))
        events.append(TimelineEvent(timestamp=ts, event=event_text, source=source))
    return events


@st.composite
def non_empty_string_list(draw, min_size=1, max_size=5):
    """Generate a non-empty list of non-empty strings."""
    count = draw(st.integers(min_value=min_size, max_value=max_size))
    return [
        draw(st.text(min_size=1, max_size=80, alphabet=st.characters(
            whitelist_categories=("L", "N", "P", "Z"),
        )))
        for _ in range(count)
    ]


@st.composite
def evidence_strategy(draw):
    """Generate an Evidence object with varying content."""
    metrics = draw(non_empty_string_list(min_size=0, max_size=5))
    logs = draw(non_empty_string_list(min_size=0, max_size=5))
    traces = draw(non_empty_string_list(min_size=0, max_size=5))
    return Evidence(metrics=metrics, logs=logs, traces=traces)


@st.composite
def root_cause_strategy(draw):
    """Generate a RootCause object with valid enum values."""
    hypothesis = draw(st.text(min_size=1, max_size=200, alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
    )))
    confidence = draw(st.sampled_from(VALID_CONFIDENCES))
    category = draw(st.one_of(st.none(), st.sampled_from(VALID_CATEGORIES)))
    return RootCause(hypothesis=hypothesis, confidence=confidence, category=category)


@st.composite
def rca_report_via_create(draw):
    """Generate an RCA report using the create_report function."""
    status = draw(st.sampled_from(VALID_STATUSES))
    timeline = draw(timeline_events())
    affected_services = draw(non_empty_string_list(min_size=1, max_size=5))
    symptoms = draw(non_empty_string_list(min_size=1, max_size=5))
    root_cause = draw(root_cause_strategy())
    evidence = draw(evidence_strategy())
    remediation = draw(non_empty_string_list(min_size=1, max_size=5))

    return create_report(
        status=status,
        timeline=timeline,
        affected_services=affected_services,
        symptoms=symptoms,
        root_cause=root_cause,
        evidence=evidence,
        remediation=remediation,
    )


@st.composite
def inconclusive_report_strategy(draw):
    """Generate an inconclusive RCA report using the create_inconclusive_report function."""
    timeline = draw(timeline_events())
    affected_services = draw(non_empty_string_list(min_size=1, max_size=5))
    symptoms = draw(non_empty_string_list(min_size=1, max_size=5))
    evidence = draw(evidence_strategy())

    return create_inconclusive_report(
        timeline=timeline,
        affected_services=affected_services,
        symptoms=symptoms,
        evidence=evidence,
    )


# --- Property Tests ---


class TestRCAReportStructuralCompleteness:
    """Property 5: RCA Report Structural Completeness.

    **Validates: Requirements 6.3**
    """

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_report_has_all_required_top_level_fields(self, report: RCAReport):
        """Any RCA report SHALL contain all required top-level fields."""
        report_dict = report.to_dict()

        required_fields = [
            "id", "timestamp", "status", "timeline",
            "affected_services", "symptoms", "root_cause",
            "evidence", "remediation",
        ]
        for field in required_fields:
            assert field in report_dict, f"Missing required field: {field}"

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_timeline_is_non_empty_with_timestamped_events(self, report: RCAReport):
        """Timeline SHALL be a non-empty array of timestamped events."""
        report_dict = report.to_dict()
        timeline = report_dict["timeline"]

        assert isinstance(timeline, list)
        assert len(timeline) > 0, "Timeline must be non-empty"

        for event in timeline:
            assert "timestamp" in event, "Each timeline event must have a timestamp"
            assert "event" in event, "Each timeline event must have an event description"
            assert len(event["timestamp"]) > 0, "Timestamp must be non-empty"
            assert len(event["event"]) > 0, "Event description must be non-empty"

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_affected_services_is_non_empty(self, report: RCAReport):
        """affected_services SHALL be a non-empty array."""
        report_dict = report.to_dict()
        services = report_dict["affected_services"]

        assert isinstance(services, list)
        assert len(services) > 0, "affected_services must be non-empty"
        for service in services:
            assert isinstance(service, str)
            assert len(service) > 0, "Each service name must be non-empty"

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_symptoms_is_non_empty(self, report: RCAReport):
        """symptoms SHALL be a non-empty array."""
        report_dict = report.to_dict()
        symptoms = report_dict["symptoms"]

        assert isinstance(symptoms, list)
        assert len(symptoms) > 0, "symptoms must be non-empty"
        for symptom in symptoms:
            assert isinstance(symptom, str)
            assert len(symptom) > 0, "Each symptom must be non-empty"

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_root_cause_has_hypothesis_and_confidence(self, report: RCAReport):
        """root_cause SHALL be an object with hypothesis and confidence."""
        report_dict = report.to_dict()
        root_cause = report_dict["root_cause"]

        assert isinstance(root_cause, dict)
        assert "hypothesis" in root_cause, "root_cause must have 'hypothesis'"
        assert "confidence" in root_cause, "root_cause must have 'confidence'"
        assert len(root_cause["hypothesis"]) > 0, "Hypothesis must be non-empty"
        assert root_cause["confidence"] in VALID_CONFIDENCES, (
            f"confidence must be one of {VALID_CONFIDENCES}, got: {root_cause['confidence']}"
        )

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_root_cause_category_is_valid_enum(self, report: RCAReport):
        """root_cause.category (when present) SHALL be a valid enum value."""
        report_dict = report.to_dict()
        root_cause = report_dict["root_cause"]

        if "category" in root_cause and root_cause["category"] is not None:
            assert root_cause["category"] in VALID_CATEGORIES, (
                f"category must be one of {VALID_CATEGORIES}, got: {root_cause['category']}"
            )

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_evidence_has_metrics_logs_traces_arrays(self, report: RCAReport):
        """evidence SHALL be an object with metrics, logs, and traces arrays."""
        report_dict = report.to_dict()
        evidence = report_dict["evidence"]

        assert isinstance(evidence, dict)
        assert "metrics" in evidence, "evidence must have 'metrics'"
        assert "logs" in evidence, "evidence must have 'logs'"
        assert "traces" in evidence, "evidence must have 'traces'"
        assert isinstance(evidence["metrics"], list)
        assert isinstance(evidence["logs"], list)
        assert isinstance(evidence["traces"], list)

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_remediation_is_array(self, report: RCAReport):
        """remediation SHALL be an array of steps."""
        report_dict = report.to_dict()
        remediation = report_dict["remediation"]

        assert isinstance(remediation, list)
        for step in remediation:
            assert isinstance(step, str)

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_status_is_valid_enum(self, report: RCAReport):
        """status SHALL be "conclusive" or "inconclusive"."""
        report_dict = report.to_dict()
        assert report_dict["status"] in VALID_STATUSES, (
            f"status must be one of {VALID_STATUSES}, got: {report_dict['status']}"
        )

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_timeline_source_is_valid_enum_when_present(self, report: RCAReport):
        """timeline[].source (when present) SHALL be a valid enum value."""
        report_dict = report.to_dict()
        for event in report_dict["timeline"]:
            if "source" in event and event["source"] is not None:
                assert event["source"] in VALID_SOURCES, (
                    f"source must be one of {VALID_SOURCES}, got: {event['source']}"
                )

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_id_is_valid_uuid_format(self, report: RCAReport):
        """id SHALL be a valid UUID string."""
        report_dict = report.to_dict()
        report_id = report_dict["id"]
        assert isinstance(report_id, str)
        assert len(report_id) == 36, "UUID must be 36 characters (with hyphens)"
        # UUID format: 8-4-4-4-12
        parts = report_id.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[1]) == 4
        assert len(parts[2]) == 4
        assert len(parts[3]) == 4
        assert len(parts[4]) == 12

    @settings(max_examples=100)
    @given(report=rca_report_via_create())
    def test_timestamp_is_iso8601_format(self, report: RCAReport):
        """timestamp SHALL be a valid ISO 8601 formatted string."""
        report_dict = report.to_dict()
        ts = report_dict["timestamp"]
        assert isinstance(ts, str)
        assert len(ts) > 0
        # Verify it ends with Z (UTC) and has T separator
        assert "T" in ts, "Timestamp must contain 'T' separator"
        assert ts.endswith("Z"), "Timestamp must end with 'Z' for UTC"

    @settings(max_examples=100)
    @given(report=inconclusive_report_strategy())
    def test_inconclusive_report_has_all_required_fields(self, report: RCAReport):
        """Inconclusive reports SHALL also contain all required fields."""
        report_dict = report.to_dict()

        required_fields = [
            "id", "timestamp", "status", "timeline",
            "affected_services", "symptoms", "root_cause",
            "evidence", "remediation",
        ]
        for field in required_fields:
            assert field in report_dict, f"Missing required field: {field}"

        # Verify inconclusive-specific properties
        assert report_dict["status"] == "inconclusive"
        assert report_dict["root_cause"]["confidence"] == "low"
        assert report_dict["root_cause"]["category"] == "unknown"

        # Timeline and services must still be non-empty
        assert len(report_dict["timeline"]) > 0
        assert len(report_dict["affected_services"]) > 0
        assert len(report_dict["symptoms"]) > 0
        assert len(report_dict["remediation"]) > 0
