"""Deterministic row scoring helpers used by the spike reports and fixtures.

Semantic gold matching remains an independent Promptfoo rubric. These helpers only
aggregate already-observed row facts; they never decide whether a finding matches
gold and never let an incomplete row masquerade as a zero-recall review.
"""

from __future__ import annotations

import argparse
import json
import os
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
        # A row without its complete retained trace cannot be audited.
        # Only review-only stages promise retained traces; older stages keep their metrics.
        unevaluable_trace = grading == "deterministic" and provider_metadata.get(
            "traceStatus"
        ) in {"failed", "not-retained"}
        quarantined = (
            bool(flags) or unevaluable_trace or _named_score(raw, "answer_key_boundary") == 0
        )
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
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--grading", choices=sorted(GRADING_MODES), default="rubric")
    parser.add_argument("--schedule", type=Path, default=None)
    parser.add_argument("--ledger", type=Path, default=None)
    parser.add_argument("--accounting-output", type=Path, default=None)
    args = parser.parse_args()
    if args.accounting_output is not None and args.schedule is None:
        parser.error("--accounting-output requires --schedule")
    if args.output is None and args.accounting_output is None:
        parser.error("nothing to do: pass --output and/or --accounting-output")
    if args.accounting_output is not None:
        schedule = json.loads(args.schedule.read_text(encoding="utf-8"))
        ledger_lines: list[dict[str, Any]] = []
        if args.ledger is not None and args.ledger.exists():
            for line in args.ledger.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    ledger_lines.append(record)
        payload = None
        if args.input is not None and args.input.exists():
            payload = json.loads(args.input.read_text(encoding="utf-8"))
        accounting = account_attempts(schedule, ledger_lines, payload)
        if args.accounting_output.exists():
            print(
                f"refuses to overwrite existing accounting output: {args.accounting_output}",
                flush=True,
            )
            return 1
        args.accounting_output.parent.mkdir(parents=True, exist_ok=True)
        try:
            _write_new_file(
                args.accounting_output,
                json.dumps(accounting, indent=2, sort_keys=True) + "\n",
            )
        except OSError as exc:
            print(
                f"refuses to overwrite existing accounting output: "
                f"{args.accounting_output} ({exc})",
                flush=True,
            )
            return 1
        print(
            f"accounted {len(accounting['accounting']['slots'])} scheduled slots "
            f"with {ACCOUNTING_ID}"
        )
    if args.output is not None:
        if args.input is None:
            parser.error("--output requires --input")
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = normalize(payload, args.grading)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"normalized {len(result['rows'])} Promptfoo rows with {NORMALIZATION_ID}")
    return 0


def _write_new_file(path: Path, text: str) -> None:
    """Write a new file with O_EXCL and mode 0600; raise on existing paths."""

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        data = text.encode("utf-8")
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    finally:
        os.close(descriptor)


ACCOUNTING_ID = "muse-attempt-accounting-v1"
READOUT_ID = "muse-adjudicated-readout-v1"

CONSEQUENTIAL_SEVERITIES = {"blocking", "should-fix"}
READOUT_SEVERITIES = {"blocking", "should-fix", "low"}
ADJUDICATION_DECISIONS = {"cleared", "confirmed-exposure", "excluded"}
FINDING_STATUSES = {"matched", "novel-valid", "unsupported", "duplicate", "out-of-scope"}


def _raw_promptfoo_rows(payload: Any) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    inner = payload.get("results") if isinstance(payload.get("results"), Mapping) else payload
    raw_rows = inner.get("results") if isinstance(inner, Mapping) else None
    if not isinstance(raw_rows, list):
        return []
    return [row for row in raw_rows if isinstance(row, Mapping)]


def _row_label(row: Mapping[str, Any]) -> str | None:
    provider = row.get("provider")
    if isinstance(provider, Mapping) and isinstance(provider.get("label"), str):
        return provider["label"]
    return None


def _row_case(row: Mapping[str, Any]) -> str | None:
    metadata = row.get("metadata")
    if isinstance(metadata, Mapping) and isinstance(metadata.get("case_id"), str):
        return metadata["case_id"]
    return None


def _row_attempt_id(row: Mapping[str, Any]) -> str | None:
    response = row.get("response")
    metadata = response.get("metadata") if isinstance(response, Mapping) else None
    if isinstance(metadata, Mapping):
        attempt = metadata.get("attemptId")
        if isinstance(attempt, str) and attempt:
            return attempt
    return None


def _row_completion_status(row: Mapping[str, Any]) -> str | None:
    response = row.get("response")
    metadata = response.get("metadata") if isinstance(response, Mapping) else None
    if isinstance(metadata, Mapping):
        status = metadata.get("completionStatus")
        if isinstance(status, str) and status:
            return status
    return None


def _normalize_repeat(value: Any) -> int | None:
    """Normalize a ledger/row repeat marker to an int slot position.

    ``"null"``/None/missing means the first repeat (0); anything
    unparseable yields None so the record matches by attempt id only and can
    never merge two repeats positionally.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        if value == "null" or not value:
            return 0
        try:
            number = int(value)
        except ValueError:
            return None
        return number if number >= 0 else None
    if value is None:
        return 0
    return None


def _ledger_slot_key(line: Mapping[str, Any]) -> tuple[str, str, int] | None:
    """Return the (provider, case, repeat) slot a ledger line belongs to.

    Explicit ``providerLabel``/``case_id``/``repeatIndex`` fields win; the
    ``scheduleKey`` string is only a fallback for older lines. A ``null``
    repeat means position 0 — it never merges across repeats.
    """

    label = line.get("providerLabel")
    case = line.get("case_id")
    if isinstance(label, str) and label and isinstance(case, str) and case:
        repeat = _normalize_repeat(line.get("repeatIndex"))
        if repeat is not None:
            return (label, case, repeat)
    key = line.get("scheduleKey")
    if isinstance(key, str) and key:
        parts = key.split("|")
        if len(parts) == 3 and parts[0] and parts[1]:
            repeat = _normalize_repeat(parts[2])
            if repeat is not None:
                return (parts[0], parts[1], repeat)
    return None


def _row_schedule_claim(row: Mapping[str, Any]) -> tuple[str, str, int] | None:
    """Return the slot a row claims via its provider-metadata schedule key."""

    response = row.get("response")
    metadata = response.get("metadata") if isinstance(response, Mapping) else None
    if not isinstance(metadata, Mapping):
        return None
    key = metadata.get("scheduleKey")
    if not isinstance(key, str) or not key:
        return None
    parts = key.split("|")
    if len(parts) != 3 or not parts[0] or not parts[1]:
        return None
    repeat = _normalize_repeat(parts[2])
    if repeat is None:
        return None
    return (parts[0], parts[1], repeat)


def _short_error(text: Any, limit: int = 160) -> str:
    """Reduce a provider error to one short line for ``error:<message>``."""

    if not isinstance(text, str) or not text.strip():
        return ""
    return text.strip().splitlines()[0][:limit]


def account_attempts(
    schedule: Mapping[str, Any],
    ledger_lines: Iterable[Mapping[str, Any]] | None,
    promptfoo_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Account for every scheduled attempt of a review batch.

    ``schedule`` is ``{"stage", "providers": [...], "cases": [...],
    "repeat": n}``. Every scheduled slot (provider x case x repeat) is listed
    with status ``completed``, ``error:<completionStatus or message>``,
    ``quarantined`` (row completed but flagged), ``missing`` (no row and no
    ledger record), ``started-not-finished`` or ``unscheduled-extra`` (row not
    in schedule). Original deterministic flags travel verbatim; nothing is
    dropped. Totals count every status.
    """

    providers = list(schedule.get("providers") or [])
    cases = list(schedule.get("cases") or [])
    repeat = schedule.get("repeat", 1)
    repeat = repeat if isinstance(repeat, int) and repeat >= 1 else 1
    raw_rows = _raw_promptfoo_rows(promptfoo_payload)
    converted: list[dict[str, Any]] = []
    if raw_rows:
        try:
            converted = promptfoo_rows({"results": {"results": list(raw_rows)}}, "deterministic")
        except ValueError:
            converted = []
    # Ledger records keyed by attempt id and by (provider, case, repeat) slot.
    # Rows join slots through their attempt id first (rows carry it in provider
    # metadata); a row without a ledger-backed attempt claims its slot through
    # its metadata schedule key. Only rows with neither fall back to
    # positional (label, case) grouping, and consumed rows are never reused by
    # another repeat.
    ledger_by_attempt: dict[str, list[Mapping[str, Any]]] = {}
    ledger_by_slot: dict[tuple[str, str, int], list[Mapping[str, Any]]] = {}
    for line in ledger_lines or []:
        if not isinstance(line, Mapping):
            continue
        attempt = line.get("attemptId")
        if isinstance(attempt, str) and attempt:
            ledger_by_attempt.setdefault(attempt, []).append(line)
        slot_key = _ledger_slot_key(line)
        if slot_key is not None:
            ledger_by_slot.setdefault(slot_key, []).append(line)
    claimed: dict[int, tuple[str, str, int] | None] = {}
    fallback: dict[tuple[str, str], list[int]] = {}
    for index, row in enumerate(raw_rows):
        slot: tuple[str, str, int] | None = None
        attempt = _row_attempt_id(row)
        if attempt and attempt in ledger_by_attempt:
            for line in ledger_by_attempt[attempt]:
                slot = _ledger_slot_key(line)
                if slot is not None:
                    break
        if slot is None:
            claim = _row_schedule_claim(row)
            if (
                claim is not None
                and claim[0] in providers
                and claim[1] in cases
                and 0 <= claim[2] < repeat
            ):
                slot = claim
        claimed[index] = slot
        if slot is None:
            fallback.setdefault((_row_label(row) or "", _row_case(row) or ""), []).append(index)
    slots: list[dict[str, Any]] = []
    extras: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for provider in providers:
        for case in cases:
            for position in range(repeat):
                slot_key = (provider, case, position)
                records = ledger_by_slot.get(slot_key, [])
                phases = {str(line.get("phase")) for line in records if isinstance(line, Mapping)}
                ledger_attempts: list[str] = []
                for line in records:
                    attempt = line.get("attemptId")
                    if isinstance(attempt, str) and attempt and attempt not in ledger_attempts:
                        ledger_attempts.append(attempt)
                exact = sorted(index for index, key in claimed.items() if key == slot_key)
                row_index: int | None = None
                duplicate_rows = 0
                if exact:
                    row_index = exact[0]
                    consumed.update(exact)
                    duplicate_rows = len(exact) - 1
                else:
                    group = fallback.get((provider, case), [])
                    if group:
                        row_index = group.pop(0)
                        consumed.add(row_index)
                if row_index is None:
                    if "finished" in phases:
                        status = "missing"
                    elif "started" in phases:
                        status = "started-not-finished"
                    else:
                        status = "missing"
                    slots.append(
                        {
                            "provider": provider,
                            "case": case,
                            "repeat": position,
                            "status": status,
                            "attemptId": ledger_attempts[0] if ledger_attempts else None,
                            "duplicateAttemptIds": ledger_attempts[1:],
                            "duplicateRows": 0,
                            "ledgerPhase": "finished" if "finished" in phases else (
                                "started" if "started" in phases else None
                            ),
                            "deterministic": None,
                            "flags": None,
                            "error": None,
                            "completion": False,
                        }
                    )
                    continue
                row = raw_rows[row_index]
                facts = converted[row_index] if row_index < len(converted) else {}
                error_text = facts.get("error") if isinstance(facts, Mapping) else None
                quarantined = bool(facts.get("quarantined")) if isinstance(facts, Mapping) else False
                completion_status = _row_completion_status(row)
                if error_text or not (facts.get("completion") if isinstance(facts, Mapping) else False):
                    # A provider error on a completed run (e.g. a stale review)
                    # must name the error, never the misleading
                    # "error:completed"; other runs keep their status label.
                    if completion_status and completion_status != "completed":
                        message = completion_status
                    else:
                        message = _short_error(error_text) or "unknown"
                    status = f"error:{message}"
                elif quarantined:
                    status = "quarantined"
                else:
                    status = "completed"
                row_attempt = _row_attempt_id(row)
                primary = row_attempt or (ledger_attempts[0] if ledger_attempts else None)
                seen_attempts: list[str] = []
                for candidate in [*ledger_attempts, *(
                    _row_attempt_id(raw_rows[i])
                    for i in exact[1:]
                    if _row_attempt_id(raw_rows[i])
                )]:
                    if candidate and candidate not in seen_attempts:
                        seen_attempts.append(candidate)
                if primary and primary not in seen_attempts:
                    seen_attempts.insert(0, primary)
                slots.append(
                    {
                        "provider": provider,
                        "case": case,
                        "repeat": position,
                        "status": status,
                        "attemptId": primary,
                        "duplicateAttemptIds": [a for a in seen_attempts if a != primary],
                        "duplicateRows": duplicate_rows,
                        "ledgerPhase": "finished" if "finished" in phases else (
                            "started" if "started" in phases else None
                        ),
                        "deterministic": facts if isinstance(facts, Mapping) else None,
                        "flags": facts.get("grader_boundary_flags") if isinstance(facts, Mapping) else None,
                        "error": error_text if isinstance(facts, Mapping) else None,
                        "completion": bool(facts.get("completion")) if isinstance(facts, Mapping) else False,
                    }
                )
    for index, row in enumerate(raw_rows):
        if index in consumed:
            continue
        facts = converted[index] if index < len(converted) else {}
        extras.append(
            {
                "provider": _row_label(row),
                "case": _row_case(row),
                "repeat": 0,
                "status": "unscheduled-extra",
                "attemptId": _row_attempt_id(row),
                "ledgerPhase": None,
                "deterministic": facts if isinstance(facts, Mapping) else None,
                "flags": facts.get("grader_boundary_flags") if isinstance(facts, Mapping) else None,
                "error": facts.get("error") if isinstance(facts, Mapping) else None,
                "completion": bool(facts.get("completion")) if isinstance(facts, Mapping) else False,
            }
        )
    totals: dict[str, int] = {}
    for entry in (*slots, *extras):
        totals[entry["status"]] = totals.get(entry["status"], 0) + 1
    ledger_count = sum(1 for line in (ledger_lines or []) if isinstance(line, Mapping))
    duplicate_slots = sum(
        1 for entry in slots if entry["duplicateAttemptIds"] or entry["duplicateRows"]
    )
    return {
        "accounting": {
            "id": ACCOUNTING_ID,
            "stage": schedule.get("stage"),
            "schedule": {
                "stage": schedule.get("stage"),
                "providers": providers,
                "cases": cases,
                "repeat": repeat,
            },
            "slots": slots,
            "extras": extras,
            "totals": totals,
            "duplicateSlots": duplicate_slots,
            "ledgerLines": ledger_count,
            "rows": len(raw_rows),
        }
    }


def _validate_key_map(key_map: Mapping[str, Any]) -> None:
    cases = key_map.get("cases")
    if not isinstance(cases, Mapping):
        raise ValueError("key map has no cases object")
    for case_id, case in cases.items():
        if not isinstance(case, Mapping):
            raise ValueError(f"key map case {case_id!r} is not an object")
        for finding in case.get("findings", []):
            severity = finding.get("severity") if isinstance(finding, Mapping) else None
            if severity not in READOUT_SEVERITIES:
                raise ValueError(
                    f"key map case {case_id!r} has invalid severity {severity!r}"
                )


def _validate_adjudication(adjudication: Mapping[str, Any]) -> None:
    attempts = adjudication.get("attempts")
    if not isinstance(attempts, Mapping):
        raise ValueError("adjudication has no attempts object")
    for attempt_id, decision in attempts.items():
        if not isinstance(decision, Mapping):
            raise ValueError(f"adjudication attempt {attempt_id!r} is not an object")
        quarantine = decision.get("quarantine")
        if quarantine is not None and quarantine not in ADJUDICATION_DECISIONS:
            raise ValueError(
                f"adjudication attempt {attempt_id!r} has invalid quarantine {quarantine!r}"
            )
        for finding in decision.get("findings", []):
            if not isinstance(finding, Mapping):
                raise ValueError(f"adjudication attempt {attempt_id!r} has a finding that is not an object")
            if finding.get("status") not in FINDING_STATUSES:
                raise ValueError(
                    f"adjudication attempt {attempt_id!r} has invalid finding status "
                    f"{finding.get('status')!r}"
                )


def adjudicated_readout(
    accounting: Mapping[str, Any],
    key_map: Mapping[str, Any],
    adjudication: Mapping[str, Any],
) -> dict[str, Any]:
    """Combine attempt accounting with the owner key map and adjudication.

    Per attempt the ``deterministic`` facts stay verbatim (including the
    original quarantine flag and error) next to the ``adjudicated`` outcome. A
    ``cleared`` decision sets ``eligible`` without editing the original flag;
    ``confirmed-exposure`` and ``excluded`` make the attempt ineligible, as
    does any incomplete attempt (counted as no useful delivery, never as a
    semantic miss or approval). Consequential findings are ``blocking`` and
    ``should-fix``; ``low`` matches never enter recall. Aggregates are
    reported per provider.
    """

    _validate_key_map(key_map)
    _validate_adjudication(adjudication)
    inner = accounting.get("accounting") if isinstance(accounting.get("accounting"), Mapping) else accounting
    slots = inner.get("slots") if isinstance(inner, Mapping) else None
    slot_list = list(slots) if isinstance(slots, list) else []
    key_cases = key_map.get("cases") if isinstance(key_map.get("cases"), Mapping) else {}
    owner_attempts = adjudication.get("attempts") if isinstance(adjudication.get("attempts"), Mapping) else {}

    def consequential_key_ids(case_id: Any) -> set[str]:
        case = key_cases.get(case_id)
        if not isinstance(case, Mapping):
            return set()
        ids: set[str] = set()
        for finding in case.get("findings", []):
            if not isinstance(finding, Mapping):
                continue
            if finding.get("severity") in CONSEQUENTIAL_SEVERITIES and isinstance(finding.get("id"), str):
                ids.add(finding["id"])
        return ids

    def key_severity(case_id: Any, key_id: Any) -> str | None:
        case = key_cases.get(case_id)
        if not isinstance(case, Mapping):
            return None
        for finding in case.get("findings", []):
            if isinstance(finding, Mapping) and finding.get("id") == key_id:
                severity = finding.get("severity")
                return severity if isinstance(severity, str) else None
        return None

    attempts: list[dict[str, Any]] = []
    for slot in slot_list:
        if not isinstance(slot, Mapping):
            continue
        provider = slot.get("provider")
        case_id = slot.get("case")
        status = slot.get("status")
        attempt_id = slot.get("attemptId")
        deterministic = slot.get("deterministic")
        owner = owner_attempts.get(attempt_id) if isinstance(attempt_id, str) else None
        owner = owner if isinstance(owner, Mapping) else None
        quarantine = owner.get("quarantine") if owner else None
        owner_findings = owner.get("findings", []) if owner else []
        owner_findings = owner_findings if isinstance(owner_findings, list) else []
        complete = status in {"completed", "quarantined"}
        if not complete:
            eligible = False
            ineligible_reason = "incomplete"
        elif quarantine in {"confirmed-exposure", "excluded"}:
            eligible = False
            ineligible_reason = quarantine
        elif status == "quarantined" and quarantine != "cleared":
            eligible = False
            ineligible_reason = "quarantine-pending"
        else:
            eligible = True
            ineligible_reason = None
        key_ids = consequential_key_ids(case_id)
        adjudication_status = "present" if owner is not None else "missing"
        matched_ids: set[str] = set()
        optional_low_matched = 0
        novel_valid = 0
        unsupported = 0
        novel_valid_consequential = False
        for finding in owner_findings:
            if not isinstance(finding, Mapping):
                continue
            finding_status = finding.get("status")
            key_id = finding.get("key_id")
            severity = key_severity(case_id, key_id)
            if severity is None and isinstance(finding.get("severity"), str):
                severity = finding["severity"]
            if finding_status == "matched":
                # Recall counts distinct matched key ids intersected with the
                # case's consequential ids: duplicates and non-key matches
                # never inflate it.
                if isinstance(key_id, str) and key_id in key_ids:
                    matched_ids.add(key_id)
                elif severity == "low":
                    optional_low_matched += 1
            elif finding_status == "novel-valid":
                novel_valid += 1
                if severity in CONSEQUENTIAL_SEVERITIES:
                    novel_valid_consequential = True
            elif finding_status == "unsupported":
                unsupported += 1
        matched = len(matched_ids)
        if eligible and owner is not None and key_ids:
            recall: float | None = matched / len(key_ids)
        else:
            recall = None
        case = key_cases.get(case_id) if isinstance(key_cases, Mapping) else None
        expected = case.get("expected_verdict") if isinstance(case, Mapping) else None
        actual = deterministic.get("actual_verdict") if isinstance(deterministic, Mapping) else None
        false_approval = bool(eligible and expected == "NEEDS_FIXES" and actual == "APPROVE")
        false_block = bool(
            eligible
            and expected == "APPROVE"
            and actual == "NEEDS_FIXES"
            and not novel_valid_consequential
        )
        attempts.append(
            {
                "provider": provider,
                "case": case_id,
                "repeat": slot.get("repeat"),
                "attemptId": attempt_id,
                "status": status,
                "eligible": eligible,
                "ineligible_reason": ineligible_reason,
                "deterministic": deterministic,
                "adjudicated": {
                    "quarantine": quarantine,
                    "adjudication": adjudication_status,
                    "matched_consequential": matched if eligible else 0,
                    "consequential_recall": recall,
                    "optional_low_matched": optional_low_matched if eligible else 0,
                    "novel_valid": novel_valid if eligible else 0,
                    "unsupported": unsupported if eligible else 0,
                    "false_approval": false_approval,
                    "false_block": false_block,
                },
            }
        )
    providers: dict[str, Any] = {}
    scheduled = inner.get("schedule") if isinstance(inner, Mapping) else None
    provider_names = scheduled.get("providers") if isinstance(scheduled, Mapping) else None
    provider_names = list(provider_names) if isinstance(provider_names, list) else sorted(
        {str(entry.get("provider")) for entry in attempts}
    )
    for provider in provider_names:
        owned = [entry for entry in attempts if entry.get("provider") == provider]
        eligible_entries = [entry for entry in owned if entry.get("eligible")]
        recalls = [
            entry["adjudicated"]["consequential_recall"]
            for entry in eligible_entries
            if isinstance(entry["adjudicated"].get("consequential_recall"), (int, float))
        ]
        matched_total = sum(
            int(entry["adjudicated"].get("matched_consequential", 0)) for entry in owned
        )
        key_total = sum(len(consequential_key_ids(entry.get("case"))) for entry in owned)
        decisions: dict[str, int] = {}
        for entry in owned:
            attempt_id = entry.get("attemptId")
            owner = owner_attempts.get(attempt_id) if isinstance(attempt_id, str) else None
            decision = owner.get("quarantine") if isinstance(owner, Mapping) else None
            if entry.get("status") == "quarantined" or decision in ADJUDICATION_DECISIONS:
                decisions[str(decision)] = decisions.get(str(decision), 0) + 1
        providers[str(provider)] = {
            "eligible_attempts": len(eligible_entries),
            "scheduled_attempts": len(owned),
            "not_adjudicated": sum(
                1 for entry in eligible_entries
                if entry["adjudicated"].get("adjudication") == "missing"
            ),
            "completed_review_recall": (sum(recalls) / len(recalls)) if recalls else None,
            "useful_delivery": (matched_total / key_total) if key_total else None,
            "false_approvals": sum(1 for entry in eligible_entries if entry["adjudicated"].get("false_approval")),
            "false_blocks": sum(1 for entry in eligible_entries if entry["adjudicated"].get("false_block")),
            "novel_valid": sum(int(entry["adjudicated"].get("novel_valid", 0)) for entry in eligible_entries),
            "unsupported": sum(int(entry["adjudicated"].get("unsupported", 0)) for entry in eligible_entries),
            "exclusions": sum(
                1 for entry in owned if entry.get("ineligible_reason") in {"confirmed-exposure", "excluded"}
            ),
            "quarantines_by_decision": decisions,
        }
    return {
        "readout": {
            "id": READOUT_ID,
            "key_id": key_map.get("key_id"),
            "key_sha256": key_map.get("key_sha256"),
            "adjudication_id": adjudication.get("adjudication_id"),
            "decided_by": adjudication.get("decided_by"),
            "attempts": attempts,
            "providers": providers,
        }
    }


def readout_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the adjudicated readout")
    parser.add_argument("--accounting", required=True, type=Path)
    parser.add_argument("--key-map", required=True, type=Path)
    parser.add_argument("--adjudication", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        print(f"refuses to overwrite existing output: {args.output}", flush=True)
        return 1
    try:
        accounting = json.loads(args.accounting.read_text(encoding="utf-8"))
        key_map = json.loads(args.key_map.read_text(encoding="utf-8"))
        adjudication = json.loads(args.adjudication.read_text(encoding="utf-8"))
        result = adjudicated_readout(accounting, key_map, adjudication)
    except (OSError, ValueError) as exc:
        print(f"readout failed: {exc}", flush=True)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write_new_file(
            args.output, json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
    except OSError as exc:
        print(f"refuses to overwrite existing output: {args.output} ({exc})", flush=True)
        return 1
    print(f"wrote adjudicated readout {READOUT_ID} to {args.output}")
    return 0


if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) > 1 and _sys.argv[1] == "readout":
        raise SystemExit(readout_main(_sys.argv[2:]))
    raise SystemExit(main())
