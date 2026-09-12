"""Promptfoo assertions for the machine-readable review contract."""

from __future__ import annotations

import json
import re
from typing import Any


def _review(output: str) -> dict[str, Any]:
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
    expected = variant == "current"
    ok = observed == expected
    return _result(ok, 1.0 if ok else 0.0, f"variant={variant!r}, skillObserved={observed}")


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
