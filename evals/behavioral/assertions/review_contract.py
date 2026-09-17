"""Promptfoo assertions for the machine-readable review contract."""

from __future__ import annotations

import json
import re
from typing import Any


def parse_review(output: str) -> dict[str, Any]:
    text = output.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("output is not a JSON object") from None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"output is not valid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise ValueError("output JSON must be an object")
    return value


_review = parse_review


def _result(pass_: bool, score: float, reason: str) -> dict[str, Any]:
    return {"pass": pass_, "score": round(score, 4), "reason": reason}


def assert_review_contract(output: str, context: dict[str, Any]) -> dict[str, Any]:
    try:
        review = _review(output)
    except ValueError as exc:
        return _result(False, 0.0, str(exc))

    expected_head = str(context.get("vars", {}).get("head_sha", ""))
    reported_head = review.get("head_sha")
    components = {
        "head_sha": isinstance(reported_head, str)
        and bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", reported_head))
        and expected_head.lower().startswith(reported_head.lower()),
        "verdict": review.get("verdict") in {"APPROVE", "NEEDS_FIXES"},
        "summary": isinstance(review.get("summary"), str) and bool(review["summary"].strip()),
        "findings": isinstance(review.get("findings"), list),
        "checks": isinstance(review.get("checks"), list),
        "unrun": isinstance(review.get("unrun"), list),
        "revision_rounds": isinstance(review.get("revision_rounds"), str)
        and bool(re.fullmatch(r"\d+/3", review["revision_rounds"])),
    }
    score = sum(components.values()) / len(components)
    failed = [name for name, ok in components.items() if not ok]
    return _result(all(components.values()), score, "missing/invalid: " + ", ".join(failed) if failed else "review contract satisfied")


def assert_expected_verdict(output: str, context: dict[str, Any]) -> dict[str, Any]:
    expected = str(context.get("vars", {}).get("expected_verdict", ""))
    try:
        actual = _review(output).get("verdict")
    except ValueError as exc:
        return _result(False, 0.0, str(exc))
    ok = actual == expected
    return _result(ok, 1.0 if ok else 0.0, f"expected {expected}, got {actual}")


def assert_skill_observation(output: str, context: dict[str, Any]) -> dict[str, Any]:
    del output
    metadata = context.get("metadata") or context.get("providerResponse", {}).get("metadata") or {}
    variant = metadata.get("variant")
    observed = bool(metadata.get("skillObserved"))
    expected = variant in {"current", "candidate"}
    ok = observed == expected
    return _result(ok, 1.0 if ok else 0.0, f"variant={variant!r}, skillObserved={observed}")


def assert_skill_delivery(output: str, context: dict[str, Any]) -> dict[str, Any]:
    del output
    metadata = context.get("metadata") or context.get("providerResponse", {}).get("metadata") or {}
    variant = metadata.get("variant")
    runtime = metadata.get("runtime")
    delivery = metadata.get("skillDelivery")
    expected = "prompt-injected" if variant == "current" else "withheld"
    ok = runtime == "codex-reference" and delivery == expected
    return _result(
        ok,
        1.0 if ok else 0.0,
        f"runtime={runtime!r}, variant={variant!r}, skillDelivery={delivery!r}",
    )


def assert_candidate_identity(output: str, context: dict[str, Any]) -> dict[str, Any]:
    del output
    metadata = context.get("metadata") or context.get("providerResponse", {}).get("metadata") or {}
    variant = metadata.get("variant")
    if variant == "none":
        return _result(True, 1.0, "control has no candidate identity")
    candidate_id = metadata.get("candidateId")
    digest = metadata.get("candidateSha256")
    ok = (
        isinstance(candidate_id, str)
        and bool(candidate_id)
        and isinstance(digest, str)
        and bool(re.fullmatch(r"[0-9a-f]{64}", digest))
        and metadata.get("candidateDelivery") == "project-skill"
    )
    return _result(ok, 1.0 if ok else 0.0, f"candidateId={candidate_id!r}, hash={digest!r}")


def assert_answer_key_boundary(output: str, context: dict[str, Any]) -> dict[str, Any]:
    """Quarantine a review whose trace shows grader-only or out-of-checkout material."""

    del output
    metadata = context.get("metadata") or context.get("providerResponse", {}).get("metadata") or {}
    flags = metadata.get("graderBoundaryFlags")
    checked = metadata.get("graderBoundaryChecked") is True
    trace = metadata.get("traceStatus")
    ok = (
        checked and flags == [] and metadata.get("workspaceOutsideEvalRepo") is True
        and trace == "retained"
    )
    # "clear" means no exposure was observed in a complete retained trace.
    reason = (
        "clear (no exposure observed)" if ok else
        f"QUARANTINE: audit before use (checked={checked}, flags={flags!r}, "
        f"workspaceOutsideEvalRepo={metadata.get('workspaceOutsideEvalRepo')!r}, trace={trace!r})"
    )
    return _result(ok, 1.0 if ok else 0.0, reason)


def assert_attempt_integrity(output: str, context: dict[str, Any]) -> dict[str, Any]:
    """Pass only a complete, bound, retained attempt with a written ledger."""

    del output
    metadata = context.get("metadata") or context.get("providerResponse", {}).get("metadata") or {}
    components = {
        "completionStatus": metadata.get("completionStatus") == "completed",
        "headBinding": metadata.get("headBinding") == "match",
        "sessionWorkspaceBinding": metadata.get("sessionWorkspaceBinding") == "match",
        "evidenceStatus": metadata.get("evidenceStatus") == "retained",
        "ledgerStatus": metadata.get("ledgerStatus") not in {"failed", None},
    }
    score = sum(components.values()) / len(components)
    failed = [name for name, ok in components.items() if not ok]
    return _result(
        all(components.values()),
        score,
        "missing/invalid: " + ", ".join(failed) if failed else "attempt integrity satisfied",
    )


def assert_no_self_grading(output: str, context: dict[str, Any]) -> dict[str, Any]:
    del output
    metadata = context.get("metadata") or context.get("providerResponse", {}).get("metadata") or {}
    observed = metadata.get("museModels")
    candidate_models = set(observed) if isinstance(observed, list) and all(isinstance(item, str) for item in observed) else set()
    grader_model = str(context.get("config", {}).get("grader_model", "gpt-5.6-terra"))
    expected = metadata.get("museExpectedModel")
    ok = (
        bool(candidate_models)
        and isinstance(expected, str)
        and candidate_models == {expected}
        and grader_model not in candidate_models
    )
    return _result(
        ok,
        1.0 if ok else 0.0,
        f"grader={grader_model}, expected={expected!r}, candidateModels={sorted(candidate_models)}",
    )


def _complete_evidence(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    return (
        isinstance(item.get("command"), str)
        and bool(item["command"].strip())
        and isinstance(item.get("exit_code"), int)
        and isinstance(item.get("output"), str)
        and bool(item["output"].strip())
    )


def assert_evidence(output: str, context: dict[str, Any]) -> dict[str, Any]:
    del context
    try:
        review = _review(output)
    except ValueError as exc:
        return _result(False, 0.0, str(exc))
    findings = review.get("findings") if isinstance(review.get("findings"), list) else []
    checks = review.get("checks") if isinstance(review.get("checks"), list) else []
    finding_scores = []
    for finding in findings:
        complete = (
            isinstance(finding, dict)
            and isinstance(finding.get("severity"), str)
            and isinstance(finding.get("location"), str)
            and ":" in finding["location"]
            and isinstance(finding.get("impact"), str)
            and bool(finding["impact"].strip())
            and _complete_evidence(finding.get("repro"))
        )
        finding_scores.append(complete)
    all_findings_supported = all(finding_scores)
    at_least_one_check = any(_complete_evidence(check) for check in checks)
    score = (float(all_findings_supported) + float(at_least_one_check)) / 2
    return _result(score == 1.0, score, f"supported findings={sum(finding_scores)}/{len(finding_scores)}; executable checks={sum(_complete_evidence(check) for check in checks)}")
