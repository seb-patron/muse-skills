"""Deterministic row scoring helpers used by the spike reports and fixtures.

Semantic gold matching remains an independent Promptfoo rubric. These helpers only
aggregate already-observed row facts; they never decide whether a finding matches
gold and never let an incomplete row masquerade as a zero-recall review.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

try:
    from .assertions.review_contract import parse_review
except ImportError:  # CLI execution from evals/behavioral.
    from assertions.review_contract import parse_review  # type: ignore[no-redef]


REQUIRED_NAMED_SCORES = {
    "review_contract",
    "verdict_accuracy",
    "skill_observation",
    "candidate_integrity",
    "independent_grading",
    "evidence",
    "gold_recall",
    "blocking_recall",
    "supported_precision",
}
# Review-only stages record no LLM rubric scores; they quarantine instead.
DETERMINISTIC_NAMED_SCORES = (
    REQUIRED_NAMED_SCORES - {"gold_recall", "blocking_recall", "supported_precision"}
) | {"answer_key_boundary"}
GRADING_MODES = {"rubric": REQUIRED_NAMED_SCORES, "deterministic": DETERMINISTIC_NAMED_SCORES}
NORMALIZATION_ID = "muse-review-metrics-v2"


def _set(value: Any) -> set[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return set()
    return {item for item in value if isinstance(item, str)}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _response_output(raw: Mapping[str, Any]) -> str:
    response = raw.get("response")
    if isinstance(response, Mapping):
        output = response.get("output")
        if isinstance(output, str):
            return output
    return response if isinstance(response, str) else ""


def _actual_verdict(output: str) -> str | None:
    try:
        value = parse_review(output)
    except ValueError:
        return None
    verdict = value.get("verdict") if isinstance(value, Mapping) else None
    return verdict if verdict in {"APPROVE", "NEEDS_FIXES"} else None


def _named_score(raw: Mapping[str, Any], name: str) -> float | None:
    scores = raw.get("namedScores")
    if not isinstance(scores, Mapping):
        return None
    value = scores.get(name)
    if isinstance(value, Mapping):
        value = value.get("score")
    score = _number(value)
    return score if score is not None and 0 <= score <= 1 else None


def _candidate_tokens(raw: Mapping[str, Any]) -> int | None:
    response = raw.get("response")
    usage = response.get("tokenUsage") if isinstance(response, Mapping) else None
    if not isinstance(usage, Mapping):
        return None
    value = usage.get("total")
    return int(value) if isinstance(value, int) and value >= 0 else None


def _case_recall_applicability(raw: Mapping[str, Any]) -> tuple[bool | None, bool | None]:
    """Read recall denominators from the case contract, not provider metadata."""

    variables = raw.get("vars")
    if not isinstance(variables, Mapping):
        return None, None
    value = variables.get("gold_findings")
    if not isinstance(value, str) or not value.strip():
        return None, None
    gold = value.strip()
    if gold == "NONE":
        return False, False
    if "PENDING_HUMAN_ADJUDICATION" in gold:
        return None, None
    severities = re.findall(r"\((blocking|should-fix|low)\):", gold, flags=re.IGNORECASE)
    if not severities:
        return None, None
    return True, any(severity.lower() == "blocking" for severity in severities)


def _execution_error(
    raw: Mapping[str, Any],
    response: Mapping[str, Any],
    output: str,
    required: set[str] = REQUIRED_NAMED_SCORES,
) -> str | None:
    provider_error = response.get("error")
    if provider_error:
        return str(provider_error)
    row_error = str(raw.get("error") or "")
    if not output.strip():
        return row_error or "candidate returned no final output"
    if _named_score(raw, "review_contract") != 1:
        return row_error or "candidate output failed the review contract"
    missing = sorted(name for name in required if _named_score(raw, name) is None)
    if missing:
        return row_error or f"row is missing required scores: {', '.join(missing)}"
    return None


def promptfoo_rows(payload: Mapping[str, Any], grading: str = "rubric") -> list[dict[str, Any]]:
    """Convert raw Promptfoo rows into the facts consumed by ``score_row``.

    Promptfoo keeps the independent rubric scores in ``namedScores`` and the
    candidate provider response (including its token usage) in ``response``.
    All-gold and blocking recall are copied separately; the converter never
    treats a missing/error row as a zero-quality review.
    """

    required = GRADING_MODES[grading]
    inner = payload.get("results") if isinstance(payload.get("results"), Mapping) else payload
    raw_rows = inner.get("results") if isinstance(inner, Mapping) else None
    if not isinstance(raw_rows, list):
        raise ValueError("Promptfoo payload has no results array")
    converted: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            raise ValueError("Promptfoo result row is not an object")
        response = raw.get("response") if isinstance(raw.get("response"), Mapping) else {}
        provider = raw.get("provider") if isinstance(raw.get("provider"), Mapping) else {}
        case_metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
        provider_metadata = response.get("metadata") if isinstance(response.get("metadata"), Mapping) else {}
        facts = provider_metadata.get("evaluationFacts")
        facts = facts if isinstance(facts, Mapping) else {}
        all_gold_applicable, blocking_gold_applicable = _case_recall_applicability(raw)
        output = _response_output(raw)
        execution_error = _execution_error(raw, response, output, required)
        completed = execution_error is None
        flags = provider_metadata.get("graderBoundaryFlags")
        quarantined = bool(flags) or _named_score(raw, "answer_key_boundary") == 0
        converted.append(
            {
                "case_id": case_metadata.get("case_id"),
                "split": case_metadata.get("split"),
                "family": case_metadata.get("family"),
                "data_role": case_metadata.get("data_role") or case_metadata.get("split"),
                "candidate_id": provider_metadata.get("candidateId") or provider.get("label"),
                "candidate_sha256": provider_metadata.get("candidateSha256"),
                "source_repository": provider_metadata.get("sourceRepository"),
                "base_sha": provider_metadata.get("baseSha"),
                "head_sha": provider_metadata.get("headSha"),
                "expected_verdict": (raw.get("vars") or {}).get("expected_verdict"),
                "actual_verdict": _actual_verdict(output),
                "gold_ids": facts.get("gold_ids"),
                "blocking_gold_ids": facts.get("blocking_gold_ids"),
                "matched_gold_ids": facts.get("matched_gold_ids"),
                "matched_blocking_gold_ids": facts.get("matched_blocking_gold_ids"),
                "all_gold_recall_applicable": all_gold_applicable,
                "blocking_recall_applicable": blocking_gold_applicable,
                "blocking_finding_recall": _named_score(raw, "blocking_recall"),
                "all_gold_recall": _named_score(raw, "gold_recall"),
                "supported_precision": _named_score(raw, "supported_precision"),
                "actionable_findings": facts.get("actionable_findings"),
                "supported_findings": facts.get("supported_findings"),
                "completion": completed,
                "quarantined": quarantined,
                "grader_boundary_flags": flags if isinstance(flags, list) else None,
                "error": execution_error,
                "assertion_error": raw.get("error"),
                "latency_ms": raw.get("latencyMs"),
                "candidate_tokens": _candidate_tokens(raw),
                "candidate_token_status": provider_metadata.get("candidateTokenStatus")
                or "unavailable",
                "candidate_cost": response.get("cost") if isinstance(response.get("cost"), (int, float)) else None,
                "novel_human_verified": facts.get("novel_human_verified", 0),
            }
        )
    return converted


def score_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Score an observed row using facts supplied by the harness/grader.

    ``matched_gold_ids`` and ``supported_findings`` are inputs from the independent
    scorer, not inferred here. An incomplete row returns ``None`` for quality
    metrics, making timeout/error classification explicit.
    """

    expected = row.get("expected_verdict")
    actual = row.get("actual_verdict")
    completed = bool(row.get("completion")) and not bool(row.get("error"))
    gold = _set(row.get("gold_ids"))
    blocking = _set(row.get("blocking_gold_ids"))
    matched = _set(row.get("matched_gold_ids")) & gold
    matched_blocking = matched & blocking
    actionable = row.get("actionable_findings")
    supported = row.get("supported_findings")
    precision = None
    if isinstance(actionable, int) and isinstance(supported, int) and actionable:
        precision = supported / actionable

    direct_blocking = _number(row.get("blocking_finding_recall"))
    direct_all = _number(row.get("all_gold_recall"))
    direct_precision = _number(row.get("supported_precision"))
    all_gold_applicable = row.get("all_gold_recall_applicable")
    if not isinstance(all_gold_applicable, bool):
        all_gold_applicable = None
    blocking_applicable = row.get("blocking_recall_applicable")
    if not isinstance(blocking_applicable, bool):
        blocking_applicable = None
    return {
        "case_id": row.get("case_id"),
        "split": row.get("split"),
        "family": row.get("family"),
        "data_role": row.get("data_role"),
        "candidate_id": row.get("candidate_id"),
        "candidate_sha256": row.get("candidate_sha256"),
        "source_repository": row.get("source_repository"),
        "base_sha": row.get("base_sha"),
        "head_sha": row.get("head_sha"),
        "completion": completed,
        "quarantined": bool(row.get("quarantined")),
        "grader_boundary_flags": row.get("grader_boundary_flags"),
        "blocking_finding_recall": (
            direct_blocking if completed and blocking_applicable is True and direct_blocking is not None
            else len(matched_blocking) / len(blocking) if completed and blocking_applicable is True and blocking
            else None
        ),
        "all_gold_recall": (
            direct_all if completed and all_gold_applicable is True and direct_all is not None
            else len(matched) / len(gold) if completed and all_gold_applicable is True and gold
            else None
        ),
        "blocking_recall_applicable": blocking_applicable,
        "all_gold_recall_applicable": all_gold_applicable,
        "supported_precision": direct_precision if completed and direct_precision is not None else precision if completed else None,
        "false_approval": bool(completed and expected == "NEEDS_FIXES" and actual == "APPROVE"),
        "verdict_accuracy": (actual == expected) if completed and expected in {"APPROVE", "NEEDS_FIXES"} else None,
        "latency_ms": row.get("latency_ms"),
        "candidate_tokens": row.get("candidate_tokens"),
        "candidate_token_status": row.get("candidate_token_status", "unavailable"),
        "candidate_cost": row.get("candidate_cost"),
        "novel_human_verified": row.get("novel_human_verified", 0),
    }


def aggregate(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [score_row(row) for row in rows]
    def mean(name: str) -> float | None:
        values = [item[name] for item in scored if isinstance(item.get(name), (int, float))]
        return sum(values) / len(values) if values else None

    # Quarantined rows stay counted but never feed quality aggregates.
    usable = [item for item in scored if not item["quarantined"]]

    def usable_mean(name: str) -> float | None:
        values = [item[name] for item in usable if isinstance(item.get(name), (int, float))]
        return sum(values) / len(values) if values else None

    token_values = [item["candidate_tokens"] for item in scored if isinstance(item.get("candidate_tokens"), int)]
    cost_values = [item["candidate_cost"] for item in scored if isinstance(item.get("candidate_cost"), (int, float))]
    complete_rows = [item for item in scored if item["completion"]]
    return {
        "rows": len(scored),
        "completed": sum(bool(item["completion"]) for item in scored),
        "errors": sum(not item["completion"] for item in scored),
        "quarantined": sum(item["quarantined"] for item in scored),
        "false_approvals": sum(bool(item["false_approval"]) for item in usable),
        "blocking_finding_recall": mean("blocking_finding_recall"),
        "blocking_recall_applicable_rows": sum(
            item["blocking_recall_applicable"] is True for item in scored
        ),
        "blocking_recall_scored_rows": sum(
            isinstance(item.get("blocking_finding_recall"), (int, float)) for item in scored
        ),
        "all_gold_recall": mean("all_gold_recall"),
        "all_gold_recall_applicable_rows": sum(
            item["all_gold_recall_applicable"] is True for item in scored
        ),
        "all_gold_recall_scored_rows": sum(
            isinstance(item.get("all_gold_recall"), (int, float)) for item in scored
        ),
        "supported_precision": mean("supported_precision"),
        "verdict_accuracy": usable_mean("verdict_accuracy"),
        "latency_ms": mean("latency_ms"),
        "candidate_tokens": sum(token_values) if complete_rows and len(token_values) == len(complete_rows) else None,
        "candidate_cost": sum(cost_values) if complete_rows and len(cost_values) == len(complete_rows) else None,
        "candidate_token_rows": len(token_values),
        "novel_human_verified": sum(
            item["novel_human_verified"] for item in scored if isinstance(item.get("novel_human_verified"), int)
        ),
    }


def normalize(payload: Mapping[str, Any], grading: str = "rubric") -> dict[str, Any]:
    rows = promptfoo_rows(payload, grading)
    return {
        "normalization": {
            "id": NORMALIZATION_ID,
            "grading": grading,
            "recall_semantics": "not-applicable and unknown denominators are excluded",
        },
        "rows": [score_row(row) for row in rows],
        "aggregate": aggregate(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize raw Promptfoo spike results")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--grading", choices=sorted(GRADING_MODES), default="rubric")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = normalize(payload, args.grading)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"normalized {len(result['rows'])} Promptfoo rows with {NORMALIZATION_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
