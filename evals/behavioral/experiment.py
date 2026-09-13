"""Offline validation for the evidence-backed review-skill spike."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "evals/behavioral/candidates/manifest.yaml"
CASES = ROOT / "evals/behavioral/cases/spike_cases.yaml"
CONFIG = ROOT / "evals/behavioral/spike-promptfooconfig.yaml"
FINALIST_RECORD = ROOT / "evals/behavioral/reports/FINALIST.yaml"
ALLOWED_MODELS = {"gpt-5.6-sol", "gpt-5.6-luna"}
FORBIDDEN_MODELS = {"gpt-6-astra"}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_SPLITS = {
    "train": {"pr1-broken-promptfoo", "pr1-structural-lint-header-gap", "clean-control-main"},
    "validation": {"clean-control-pr1-merge", "clean-control-pr3-merge"},
    "heldout": {"pr2-review-loop", "pr4-cross-model-reference", "pr3-behavioral-baseline"},
}
PROMOTED_SKILL_BLOB = "a92f49327db57accd965ffa9e4586d8732655916"
SPIKE_MODEL = "muse-spark-1.3-contributor"
SPIKE_EVAL_TIMEOUT_MS = 900_000


def _load(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def validate_manifest() -> list[str]:
    errors: list[str] = []
    data = _load(MANIFEST)
    if data.get("hash_algorithm") != "sha256" or data.get("immutable") is not True:
        errors.append("manifest must declare immutable sha256 candidates")
    candidates = data.get("candidates")
    expected_candidate_ids = {"current", "minimal", "risk-first", "upstream-adapted"}
    if (
        not isinstance(candidates, list)
        or len(candidates) != len(expected_candidate_ids)
        or {item.get("id") for item in candidates} != expected_candidate_ids
    ):
        errors.append("manifest must contain exactly the four immutable candidates")
        candidates = candidates if isinstance(candidates, list) else []
    for item in candidates:
        path = ROOT / str(item.get("path", ""))
        expected = str(item.get("sha256", ""))
        if not SHA_RE.fullmatch(expected):
            errors.append(f"{item.get('id')}: invalid sha256")
            continue
        if not path.is_file():
            errors.append(f"{item.get('id')}: missing candidate file {path}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"{item.get('id')}: hash mismatch expected {expected}, got {actual}")
        if item.get("id") != "current":
            lines = path.read_text(encoding="utf-8").splitlines()
            if not 80 <= len(lines) <= 120:
                errors.append(f"{item.get('id')}: candidate must be 80-120 lines, got {len(lines)}")
        if item.get("id") != "current" and not str(item.get("path", "")).startswith("evals/behavioral/candidates/"):
            errors.append(f"{item.get('id')}: non-current candidate must live under candidates/")
        if item.get("id") == "current":
            try:
                blob = subprocess.run(
                    ["git", "rev-parse", "HEAD:skills/adversarial-review/SKILL.md"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.strip()
            except OSError:
                blob = ""
            try:
                working_blob = subprocess.run(
                    ["git", "hash-object", str(path)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.strip()
            except OSError:
                working_blob = ""
            if blob != PROMOTED_SKILL_BLOB or working_blob != blob:
                errors.append("promoted adversarial-review skill changed from the frozen baseline")
    models = set(data.get("candidate_models", []))
    if models != ALLOWED_MODELS:
        errors.append(f"candidate model allowlist must be exactly {sorted(ALLOWED_MODELS)}")
    if set(data.get("grader", {}).values()) & FORBIDDEN_MODELS:
        errors.append("Astra is forbidden from the spike")
    return errors


def validate_cases() -> list[str]:
    errors: list[str] = []
    cases = _load(CASES)
    if not isinstance(cases, list) or len(cases) != 8:
        errors.append("spike corpus must contain exactly eight frozen cases")
        return errors
    seen: set[str] = set()
    actual_splits: dict[str, set[str]] = {key: set() for key in EXPECTED_SPLITS}
    for case in cases:
        metadata = case.get("metadata", {})
        variables = case.get("vars", {})
        case_id = metadata.get("case_id")
        split = metadata.get("split")
        if case_id in seen:
            errors.append(f"duplicate case id {case_id}")
        seen.add(case_id)
        if split not in actual_splits:
            errors.append(f"{case_id}: invalid split {split}")
        else:
            actual_splits[split].add(case_id)
            if not str(case.get("description", "")).startswith(f"[{split}] "):
                errors.append(f"{case_id}: description does not agree with metadata.split")
        for key in ("base_sha", "head_sha"):
            value = variables.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
                errors.append(f"{case_id}: {key} must be a full SHA")
        pending = metadata.get("gold_status") == "pending-human-adjudication"
        expected = variables.get("expected_verdict")
        if pending and expected != "PENDING_HUMAN_ADJUDICATION":
            errors.append(f"{case_id}: pending gold must not have a scored verdict")
        if not pending and expected not in {"APPROVE", "NEEDS_FIXES"}:
            errors.append(f"{case_id}: verified gold must have a scored verdict")
    if actual_splits != EXPECTED_SPLITS:
        errors.append(f"frozen split mismatch: {actual_splits}")
    for case in cases:
        case_id = case.get("metadata", {}).get("case_id")
        variables = case.get("vars", {})
        revisions: dict[str, str] = {}
        for key in ("base_sha", "head_sha"):
            revision = variables.get(key)
            if not isinstance(revision, str):
                continue
            result = subprocess.run(
                ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            resolved = result.stdout.strip()
            if result.returncode != 0 or len(resolved) != 40:
                errors.append(f"{case_id}: {key} is not present in the source checkout")
            else:
                revisions[key] = resolved
        if len(revisions) == 2:
            ancestry = subprocess.run(
                ["git", "merge-base", "--is-ancestor", revisions["base_sha"], revisions["head_sha"]],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            if ancestry.returncode != 0:
                errors.append(f"{case_id}: base_sha is not an ancestor of head_sha")
    return errors


def validate_config() -> list[str]:
    errors: list[str] = []
    config = _load(CONFIG)
    providers = config.get("providers", [])
    expected_providers = {
        "spike-current": ("current", "current"),
        "spike-minimal": ("candidate", "minimal"),
        "spike-risk-first": ("candidate", "risk-first"),
        "spike-upstream-adapted": ("candidate", "upstream-adapted"),
    }
    labels = [provider.get("label") for provider in providers]
    if len(providers) != len(expected_providers) or set(labels) != set(expected_providers):
        errors.append("spike config must contain exactly one provider for each frozen candidate label")
    for provider in providers:
        label = provider.get("label")
        expected = expected_providers.get(label)
        if expected and (
            provider.get("id"),
            provider.get("config", {}).get("variant"),
            provider.get("config", {}).get("candidate_id"),
        ) != ("file://providers/muse_provider.py", *expected):
            errors.append(f"{label}: provider variant/candidate mapping changed")
    manifest_by_id = {item.get("id"): item for item in _load(MANIFEST).get("candidates", [])}
    ids = {item.get("config", {}).get("candidate_id") for item in providers}
    if ids != {"current", "minimal", "risk-first", "upstream-adapted"}:
        errors.append("spike config must deliver exactly the four candidate ids")
    for provider in providers:
        item = provider.get("config", {})
        if item.get("variant") not in {"current", "candidate"}:
            errors.append(f"invalid candidate variant in {provider.get('label')}")
        if item.get("timeout_seconds") != 540 or item.get("max_model_steps") != 24:
            errors.append(f"{provider.get('label')}: baseline runtime/step budget changed")
        model = item.get("model")
        if model != SPIKE_MODEL:
            errors.append(f"{provider.get('label')}: tested Muse model must remain {SPIKE_MODEL}")
        if model in FORBIDDEN_MODELS:
            errors.append("Astra is forbidden from candidate providers")
        candidate = manifest_by_id.get(item.get("candidate_id"))
        if candidate and item.get("candidate_sha256") != candidate.get("sha256"):
            errors.append(f"{provider.get('label')}: configured hash differs from manifest")
    evaluate_options = config.get("evaluateOptions", {})
    if evaluate_options.get("maxConcurrency") != 1:
        errors.append("spike target-provider concurrency must remain 1")
    timeout_ms = evaluate_options.get("timeoutMs")
    provider_timeout_ms = max(
        (
            item.get("config", {}).get("timeout_seconds", 0) * 1000
            for item in providers
            if isinstance(item.get("config", {}).get("timeout_seconds"), int)
        ),
        default=0,
    )
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= provider_timeout_ms:
        errors.append("spike eval-step timeout must exceed every Muse provider process timeout")
    if timeout_ms != SPIKE_EVAL_TIMEOUT_MS:
        errors.append(
            f"spike per-row timeout must remain {SPIKE_EVAL_TIMEOUT_MS}ms so Promptfoo grades inline"
        )
    default_options = config.get("defaultTest", {}).get("options", {})
    default = default_options.get("provider", {})
    if default.get("id") != "openai:codex-sdk:gpt-5.6-terra":
        errors.append("independent grader provider/model changed from Terra")
    if default.get("config", {}).get("model_reasoning_effort") != "high":
        errors.append("independent grader effort must remain high")
    grader_model = str(default.get("id", "")).rsplit(":", 1)[-1]
    if any(provider.get("config", {}).get("model") == grader_model for provider in providers):
        errors.append("tested candidate model must not be the independent grader")
    prompt = (ROOT / "evals/behavioral/prompts/review.txt").read_text(encoding="utf-8")
    if "gold_findings" in prompt or "resolved_findings" in prompt:
        errors.append("candidate prompt must not expose gold variables")
    return errors


def validate_heldout(finalist_label: str, finalist_sha256: str) -> list[str]:
    errors = validate_cases()
    if not SHA_RE.fullmatch(finalist_sha256):
        errors.append("held-out finalist hash must be a 64-character SHA-256")
    cases = _load(CASES)
    for case in cases:
        metadata = case.get("metadata", {})
        if metadata.get("split") != "heldout":
            continue
        case_id = metadata.get("case_id")
        variables = case.get("vars", {})
        if metadata.get("gold_status") != "verified-human":
            errors.append(f"{case_id}: held-out gold is not human-adjudicated")
        if variables.get("expected_verdict") not in {"APPROVE", "NEEDS_FIXES"}:
            errors.append(f"{case_id}: held-out verdict is not scored human gold")
        for key in ("gold_findings", "resolved_findings"):
            value = str(variables.get(key, ""))
            if not value.strip() or "PENDING_HUMAN_ADJUDICATION" in value:
                errors.append(f"{case_id}: held-out {key} is still pending")

    config = _load(CONFIG)
    providers = {provider.get("label"): provider for provider in config.get("providers", [])}
    configured_labels = set(providers)
    if not FINALIST_RECORD.is_file():
        errors.append("held-out finalist record is missing")
    else:
        record = _load(FINALIST_RECORD)
        validation_finalists = record.get("validation_finalists")
        selected_label = record.get("selected_label")
        selected_hash = record.get("selected_sha256")
        if (
            record.get("status") != "frozen"
            or not isinstance(validation_finalists, list)
            or not all(isinstance(label, str) for label in validation_finalists)
        ):
            errors.append("held-out finalist record is not a frozen validation selection")
        elif (
            len(validation_finalists) != 2
            or len(set(validation_finalists)) != 2
            or any(label not in configured_labels for label in validation_finalists)
            or finalist_label not in validation_finalists
        ):
            errors.append("held-out finalist is not one of the two frozen validation finalists")
        if selected_label != finalist_label or selected_hash != finalist_sha256:
            errors.append("held-out finalist does not match the frozen validation selection record")
    candidate_provider = providers.get(finalist_label)
    candidate = candidate_provider.get("config", {}) if candidate_provider else None
    if candidate is None:
        errors.append(f"held-out finalist must be one configured provider label, got {finalist_label!r}")
    elif candidate.get("candidate_sha256") != finalist_sha256:
        errors.append("held-out finalist hash does not match the configured candidate hash")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate", "validate-heldout"))
    parser.add_argument("--finalist-label")
    parser.add_argument("--finalist-sha256")
    args = parser.parse_args()
    errors = validate_manifest() + validate_config()
    if args.command == "validate":
        errors += validate_cases()
    else:
        if not args.finalist_label or not args.finalist_sha256:
            errors.append("validate-heldout requires --finalist-label and --finalist-sha256")
        else:
            errors += validate_heldout(args.finalist_label, args.finalist_sha256)
    if errors:
        print("SPIKE VALIDATION FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("SPIKE VALIDATION PASSED: candidates, hashes, frozen splits, budgets, grader, and gold isolation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
