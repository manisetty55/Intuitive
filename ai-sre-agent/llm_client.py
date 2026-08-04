"""LLM integration for the AI SRE Agent.

Uses LangChain to interact with a configurable LLM (default: gpt-4o-mini) for
root cause hypothesis generation. Includes structured output parsing for
consistent RCA report format and a rule-based fallback when the LLM is
unavailable.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

import structlog
from langchain.prompts import ChatPromptTemplate
from langchain.schema import BaseOutputParser
from langchain_openai import ChatOpenAI

from analyzer import AnalysisEvidence
from config import LLM_MAX_RETRIES, LLM_MODEL, LLM_TIMEOUT_SECONDS

logger = structlog.get_logger(__name__)


@dataclass
class RootCause:
    """Root cause hypothesis from analysis."""

    hypothesis: str
    confidence: str  # "high", "medium", "low"
    category: str  # "pod_termination", "latency_injection", "resource_pressure", "unknown"


@dataclass
class RCAResult:
    """Structured result from LLM or rule-based analysis."""

    root_cause: RootCause
    remediation: list[str] = field(default_factory=list)


# --- Structured Output Parser ---


class RCAOutputParser(BaseOutputParser[RCAResult]):
    """Parse LLM output into a structured RCAResult.

    Expects JSON output with the following schema:
    {
        "hypothesis": "string describing the root cause",
        "confidence": "high" | "medium" | "low",
        "category": "pod_termination" | "latency_injection" | "resource_pressure" | "unknown",
        "remediation": ["step1", "step2", ...]
    }
    """

    def parse(self, text: str) -> RCAResult:
        """Parse LLM text output into RCAResult."""
        # Try to extract JSON from the response
        cleaned = text.strip()

        # Handle markdown code blocks
        if "```json" in cleaned:
            start = cleaned.index("```json") + 7
            end = cleaned.index("```", start)
            cleaned = cleaned[start:end].strip()
        elif "```" in cleaned:
            start = cleaned.index("```") + 3
            end = cleaned.index("```", start)
            cleaned = cleaned[start:end].strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("llm_output_parse_failed", raw_output=text[:200])
            # Return a low-confidence unknown result if parsing fails
            return RCAResult(
                root_cause=RootCause(
                    hypothesis=text[:500],
                    confidence="low",
                    category="unknown",
                ),
                remediation=["Manual investigation recommended"],
            )

        # Validate confidence enum
        confidence = data.get("confidence", "low")
        if confidence not in ("high", "medium", "low"):
            confidence = "low"

        # Validate category enum
        category = data.get("category", "unknown")
        valid_categories = (
            "pod_termination",
            "latency_injection",
            "resource_pressure",
            "unknown",
        )
        if category not in valid_categories:
            category = "unknown"

        return RCAResult(
            root_cause=RootCause(
                hypothesis=data.get("hypothesis", "Unable to determine root cause"),
                confidence=confidence,
                category=category,
            ),
            remediation=data.get("remediation", ["Manual investigation recommended"]),
        )

    @property
    def _type(self) -> str:
        return "rca_output_parser"


# --- LLM Prompt Template ---

RCA_PROMPT_TEMPLATE = """You are an expert Site Reliability Engineer performing root cause analysis on a production incident.

Based on the following observability evidence, determine the most likely root cause.

## Detected Symptoms
{symptoms}

## Metric Deviations (Prometheus)
{metrics}

## Error Logs (Loki)
{logs}

## Slow Traces (Tempo)
{traces}

## Affected Services
{affected_services}

## Event Timeline
{timeline}

Analyze this evidence and provide your root cause analysis as a JSON object with the following schema:
{{
    "hypothesis": "A clear explanation of the root cause",
    "confidence": "high" | "medium" | "low",
    "category": "pod_termination" | "latency_injection" | "resource_pressure" | "unknown",
    "remediation": ["step 1", "step 2", ...]
}}

Rules:
- "pod_termination": Use when evidence shows sudden pod restarts, CrashLoopBackOff, or container terminated signals
- "latency_injection": Use when evidence shows uniform latency increase across requests without errors
- "resource_pressure": Use when evidence shows CPU/memory throttling, OOM kills, or resource quota pressure
- "unknown": Use when evidence is insufficient or contradictory

Respond ONLY with the JSON object, no additional text."""


# --- LLM Client ---


def _build_llm() -> ChatOpenAI:
    """Build the LangChain LLM client with configured model."""
    return ChatOpenAI(
        model=LLM_MODEL,
        temperature=0.1,
        request_timeout=LLM_TIMEOUT_SECONDS,
        max_retries=LLM_MAX_RETRIES,
    )


def _format_evidence_for_prompt(evidence: AnalysisEvidence) -> dict[str, str]:
    """Format evidence fields into strings suitable for the prompt template."""
    return {
        "symptoms": "\n".join(f"- {s}" for s in evidence.symptoms) or "No symptoms detected",
        "metrics": "\n".join(f"- {m}" for m in evidence.metrics) or "No metric data available",
        "logs": "\n".join(f"- {log}" for log in evidence.logs[:20]) or "No error logs found",
        "traces": "\n".join(f"- {t}" for t in evidence.traces[:20]) or "No slow traces found",
        "affected_services": ", ".join(evidence.affected_services) or "unknown",
        "timeline": "\n".join(
            f"- [{e.get('source', 'agent')}] {e.get('timestamp', '')}: {e.get('event', '')}"
            for e in evidence.timeline
        )
        or "No timeline events",
    }


def analyze_with_llm(evidence: AnalysisEvidence) -> Optional[RCAResult]:
    """Generate a root cause hypothesis using the configured LLM.

    Sends gathered evidence to the LLM via LangChain and parses the
    structured response into an RCAResult.

    Args:
        evidence: The gathered observability evidence.

    Returns:
        RCAResult if LLM analysis succeeds, None if LLM is unavailable.
    """
    try:
        llm = _build_llm()
        prompt = ChatPromptTemplate.from_template(RCA_PROMPT_TEMPLATE)
        parser = RCAOutputParser()

        # Build the chain
        chain = prompt | llm | parser

        # Format evidence for the prompt
        prompt_vars = _format_evidence_for_prompt(evidence)

        logger.info("llm_analysis_started", model=LLM_MODEL)
        result = chain.invoke(prompt_vars)
        logger.info(
            "llm_analysis_completed",
            hypothesis=result.root_cause.hypothesis[:100],
            confidence=result.root_cause.confidence,
            category=result.root_cause.category,
        )
        return result

    except Exception as exc:
        logger.error(
            "llm_analysis_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            model=LLM_MODEL,
        )
        return None


# --- Rule-Based Fallback ---


def _classify_by_rules(evidence: AnalysisEvidence) -> RCAResult:
    """Apply rule-based heuristics to classify the failure category.

    This fallback is used when the LLM is unavailable. It applies simple
    pattern matching against the gathered evidence to produce a hypothesis.
    """
    metrics_text = " ".join(evidence.metrics).lower()
    logs_text = " ".join(evidence.logs).lower()
    traces_text = " ".join(evidence.traces).lower()
    symptoms_text = " ".join(evidence.symptoms).lower()

    # Rule 1: Pod termination indicators
    pod_kill_signals = [
        "crashloopbackoff",
        "oomkilled",
        "terminated",
        "pod killed",
        "container restart",
        "connection refused",
        "unavailable",
        "error_rate",
    ]
    pod_kill_score = sum(
        1
        for signal in pod_kill_signals
        if signal in metrics_text or signal in logs_text or signal in symptoms_text
    )

    # Rule 2: Latency injection indicators
    latency_signals = [
        "latency",
        "p99",
        "duration",
        "slow",
        "timeout",
        "deadline",
        "latency_p99",
    ]
    latency_score = sum(
        1
        for signal in latency_signals
        if signal in metrics_text
        or signal in traces_text
        or signal in symptoms_text
    )

    # Rule 3: Resource pressure indicators
    resource_signals = [
        "cpu",
        "memory",
        "throttl",
        "oom",
        "pressure",
        "quota",
        "resource",
        "limit",
    ]
    resource_score = sum(
        1
        for signal in resource_signals
        if signal in metrics_text or signal in logs_text or signal in symptoms_text
    )

    # Determine category based on highest score
    scores = {
        "pod_termination": pod_kill_score,
        "latency_injection": latency_score,
        "resource_pressure": resource_score,
    }
    max_category = max(scores, key=scores.get)  # type: ignore[arg-type]
    max_score = scores[max_category]

    if max_score == 0:
        # No clear signal - return unknown
        return RCAResult(
            root_cause=RootCause(
                hypothesis="Unable to determine root cause from available evidence. "
                "No clear pattern matches known failure categories.",
                confidence="low",
                category="unknown",
            ),
            remediation=[
                "Check pod events with: kubectl get events -n applications",
                "Review recent deployments for configuration changes",
                "Inspect node resource utilization",
                "Manual investigation recommended",
            ],
        )

    # Determine confidence based on score magnitude
    if max_score >= 3:
        confidence = "high"
    elif max_score >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    # Generate category-specific hypothesis and remediation
    if max_category == "pod_termination":
        hypothesis = (
            "Service pod was terminated or crashed. Evidence shows error rate "
            "increase consistent with sudden service unavailability."
        )
        remediation = [
            "Check pod status: kubectl get pods -n applications",
            "Review pod events: kubectl describe pod -n applications -l app=sample-api",
            "Verify deployment replicas: kubectl get deployment -n applications",
            "Check for OOMKilled: kubectl get pods -n applications -o jsonpath='{.items[*].status.containerStatuses[*].lastState}'",
        ]
    elif max_category == "latency_injection":
        hypothesis = (
            "Artificial latency has been injected into the service. Evidence shows "
            "p99 latency exceeding baseline threshold without corresponding error rate increase."
        )
        remediation = [
            "Check for injected delays: kubectl get deployment sample-api -n applications -o yaml",
            "Review sidecar containers: kubectl describe pod -n applications -l app=sample-api",
            "Check env vars for delay configuration",
            "Remove latency injection and verify recovery",
        ]
    else:  # resource_pressure
        hypothesis = (
            "Resource pressure detected on service pods. Evidence shows CPU or memory "
            "utilization approaching limits, causing throttling or OOM events."
        )
        remediation = [
            "Check resource usage: kubectl top pods -n applications",
            "Review resource limits: kubectl get pod -n applications -o jsonpath='{.items[*].spec.containers[*].resources}'",
            "Look for stress containers: kubectl get pods -n applications -o wide",
            "Scale up resources or remove pressure source",
        ]

    return RCAResult(
        root_cause=RootCause(
            hypothesis=hypothesis,
            confidence=confidence,
            category=max_category,
        ),
        remediation=remediation,
    )


def analyze_with_fallback(evidence: AnalysisEvidence) -> RCAResult:
    """Analyze evidence using LLM with rule-based fallback.

    Attempts LLM analysis first. If the LLM is unavailable or fails,
    falls back to rule-based hypothesis generation.

    Args:
        evidence: The gathered observability evidence.

    Returns:
        RCAResult with root cause hypothesis and remediation steps.
    """
    # Try LLM first
    llm_result = analyze_with_llm(evidence)
    if llm_result is not None:
        return llm_result

    # Fallback to rule-based analysis
    logger.info("falling_back_to_rule_based_analysis")
    result = _classify_by_rules(evidence)
    logger.info(
        "rule_based_analysis_completed",
        category=result.root_cause.category,
        confidence=result.root_cause.confidence,
    )
    return result
