"""Scorers for AO Model Evaluation.

Two categories:
  1. Programmatic scorers: run without API keys, deterministic checks.
  2. LLM judges: MLflow built-in + Guidelines, need API keys.

The 6 evaluation dimensions and how they map:
  Accuracy      -> Correctness (built-in, uses ground truth)
  Routing       -> Guidelines (correct path, consistent rationale)
  Tool Usage    -> Guidelines (correct tools, no hallucinated results)
  Completeness  -> Completeness (built-in)
  Actionability -> Guidelines (actionable, schema-conformant)
  Safety        -> Safety (built-in)
"""

import json

from mlflow.entities import Feedback
from mlflow.genai.scorers import Scorer


# ---------------------------------------------------------------------------
# Programmatic scorers (always run, no API key needed)
# ---------------------------------------------------------------------------

class SchemaComplianceScorer(Scorer):
    name: str = "schema_compliance"

    def __call__(self, *, inputs, outputs, expectations, **kwargs) -> Feedback:
        expected = _parse_expected(expectations)
        if expected is None:
            return Feedback(value=None, rationale="No expected output to compare against.")

        expected_keys = set(expected.keys())
        if not expected_keys:
            return Feedback(value=1.0, rationale="No keys expected.")

        if "raw_content" in outputs and len(outputs) == 1:
            return Feedback(
                value=0.0,
                rationale=f"Output not parsed. Expected keys: {sorted(expected_keys)}",
            )

        actual_keys = set(outputs.keys())
        present = expected_keys & actual_keys
        missing = expected_keys - actual_keys
        score = len(present) / len(expected_keys)

        if missing:
            rationale = f"Missing: {sorted(missing)}. Present: {sorted(present)}."
        else:
            rationale = f"All expected fields present: {sorted(expected_keys)}."

        return Feedback(value=score, rationale=rationale)


class LatencyThresholdScorer(Scorer):
    name: str = "latency_threshold"
    warn_ms: int = 5000
    fail_ms: int = 15000

    def __call__(self, *, inputs, outputs, expectations, **kwargs) -> Feedback:
        latency_ms = inputs.get("latency_ms")
        if latency_ms is None:
            return Feedback(value=None, rationale="No latency data available.")

        latency_ms = float(latency_ms)

        if latency_ms <= self.warn_ms:
            return Feedback(value=1.0, rationale=f"{latency_ms:.0f}ms, under {self.warn_ms}ms threshold.")
        elif latency_ms <= self.fail_ms:
            return Feedback(value=0.5, rationale=f"{latency_ms:.0f}ms, between warn ({self.warn_ms}ms) and fail ({self.fail_ms}ms).")
        else:
            return Feedback(value=0.0, rationale=f"{latency_ms:.0f}ms, exceeds fail threshold ({self.fail_ms}ms).")


class ResponseQualityScorer(Scorer):
    name: str = "response_quality"

    def __call__(self, *, inputs, outputs, expectations, **kwargs) -> Feedback:
        checks_passed = 0
        total_checks = 4
        details = []

        if outputs and outputs != {}:
            checks_passed += 1
            details.append("non-empty: PASS")
        else:
            details.append("non-empty: FAIL")

        if "error" not in outputs:
            checks_passed += 1
            details.append("no error: PASS")
        else:
            details.append(f"error found: {outputs.get('error', 'unknown')}")

        if "raw_content" not in outputs:
            checks_passed += 1
            details.append("structured: PASS")
        else:
            details.append("structured: FAIL (raw_content fallback)")

        has_content = any(
            isinstance(v, str) and len(v.strip()) > 0
            for v in outputs.values()
            if not str(v).startswith("_")
        )
        if has_content:
            checks_passed += 1
            details.append("substantive: PASS")
        else:
            details.append("substantive: FAIL")

        score = checks_passed / total_checks
        return Feedback(value=score, rationale="; ".join(details))


def build_programmatic_scorers():
    return [
        SchemaComplianceScorer(),
        LatencyThresholdScorer(),
        ResponseQualityScorer(),
    ]


# ---------------------------------------------------------------------------
# LLM judges (need API keys, skipped with --skip-judges)
# ---------------------------------------------------------------------------

def build_llm_judges(judge_config: dict, provider_being_judged: str):
    """Build LLM judge scorers with cross-model judging.

    Claude judges non-Anthropic models, GPT judges Anthropic models.
    This prevents a model from evaluating its own outputs.
    """
    from mlflow.genai.scorers import Correctness, Safety, Completeness, Guidelines

    if provider_being_judged == "anthropic":
        model = judge_config.get("anthropic_model", "openai:/gpt-4o")
    else:
        model = judge_config.get("default_model", "anthropic:/claude-sonnet-4-20250514")

    return [
        # Accuracy: correct identification of issues, systems, severity
        Correctness(model=model),

        # Completeness: all elements of the prompt addressed
        Completeness(model=model),

        # Safety: risks flagged, rollback steps, human oversight for high-impact
        Safety(model=model),

        # Routing: chose the correct path with consistent rationale
        Guidelines(
            name="routing",
            guidelines=[
                "The model chose the correct routing path based on the input category.",
                "The routing rationale is consistent with the chosen path.",
                "The confidence level is appropriate for the input clarity.",
            ],
            model=model,
        ),

        # Actionability: output is specific, schema-conformant, usable by downstream nodes
        Guidelines(
            name="actionability",
            guidelines=[
                "The response provides specific, actionable recommendations.",
                "An IT operator could execute the recommendations without additional context.",
                "The output conforms to the expected JSON schema with all required fields.",
                "Values are specific (not generic placeholders like 'TBD' or 'N/A').",
            ],
            model=model,
        ),

        # Tool Usage: correct tools called, no hallucinated results
        Guidelines(
            name="tool_usage",
            guidelines=[
                "Only tools available in the workflow were referenced.",
                "No hallucinated tool outputs or fabricated data.",
                "Tool calls are efficient with no redundant or unnecessary calls.",
            ],
            model=model,
        ),
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_expected(expectations):
    raw = expectations.get("expected_response")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
