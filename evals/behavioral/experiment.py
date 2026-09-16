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
DEVELOPMENT_MANIFEST = ROOT / "evals/behavioral/candidates/development-v2-manifest.yaml"
DEVELOPMENT_CASES = ROOT / "evals/behavioral/cases/development-v2-cases.yaml"
DEVELOPMENT_CONFIG = ROOT / "evals/behavioral/development-v2-promptfooconfig.yaml"
DEVELOPMENT_V3_MANIFEST = ROOT / "evals/behavioral/candidates/development-v3-manifest.yaml"
DEVELOPMENT_V3_CASES = ROOT / "evals/behavioral/cases/development-v3-cases.yaml"
DEVELOPMENT_V3_CONFIG = ROOT / "evals/behavioral/development-v3-promptfooconfig.yaml"
SCREEN_V3_MANIFEST = ROOT / "evals/behavioral/candidates/evidence-claims-v3-screen-manifest.yaml"
SCREEN_V3_CONFIG = ROOT / "evals/behavioral/evidence-claims-v3-screen-promptfooconfig.yaml"
DEVELOPMENT_PROMPT = ROOT / "evals/behavioral/prompts/review.txt"
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
DEVELOPMENT_PROMPT_SHA256 = "d66dbd7c66ea1747d64e05dad975f57afbc750ffe6c28166cd7d101cd0ca947e"
DEVELOPMENT_V3_MANIFEST_SHA256 = "9207e71c852f3a3aa46e7d426648e9879ad37ace69d4d748959f4eeaa62b2e5a"
DEVELOPMENT_V3_CASES_SHA256 = "095023d053d9360417203d2b8bb69171fef9028a616d7a4cb991ed6461889dd8"
DEVELOPMENT_V3_CONFIG_SHA256 = "8f1ce204e5c05885378714b88a412210f9912c4db06093c18445b8e30542c8f1"
SCREEN_V3_MANIFEST_SHA256 = "849e4950c5bde5801b8d47d47d57adbaffe0f608e2e31e58f3e0bdf5461c0ea3"
SCREEN_V3_CONFIG_SHA256 = "b084f0a1fb42c81dacac6d4f8a83ee2017a793da224b68726f7908239d702994"
EVIDENCE_CLAIMS_V3_SHA256 = "d357350e83f01429f97aa5903404ffe9342677bb16e39242024878958dfc4477"
DEVELOPMENT_CASE_IDS = {
    "genv-pr87-first-repair-type-boundary",
    "genv-pr90-evidence-claim",
    "genv-pr90-synchronized-clean",
}
DEVELOPMENT_PROVIDER_LABELS = {
    "development-v2-current": ("current", "current"),
    "development-v2-evidence-claims": ("candidate", "evidence-claims-v2"),
}
DEVELOPMENT_V3_PROVIDER_LABELS = {
    "development-v3-current": ("current", "current"),
    "development-v3-evidence-claims": ("candidate", "evidence-claims-v2"),
}

SCREEN_V3_PROVIDER_LABELS = {
    "screen-evidence-claims-v2": ("candidate", "evidence-claims-v2"),
    "screen-evidence-claims-v3": ("candidate", "evidence-claims-v3"),
}


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


def validate_development_manifest() -> list[str]:
    errors: list[str] = []
    data = _load(DEVELOPMENT_MANIFEST)
    if (
        data.get("protocol") != "muse-adversarial-review-development-v2"
        or data.get("semantic_evaluation_version") != "muse-review-metrics-v2"
        or data.get("data_role") != "development"
        or data.get("immutable") is not True
        or data.get("hash_algorithm") != "sha256"
    ):
        errors.append("development manifest identity, role, immutability, or hash contract changed")
    candidates = data.get("candidates")
    expected_ids = {"current", "evidence-claims-v2"}
    if (
        not isinstance(candidates, list)
        or len(candidates) != 2
        or {item.get("id") for item in candidates} != expected_ids
    ):
        errors.append("development manifest must contain exactly current and evidence-claims-v2")
        candidates = candidates if isinstance(candidates, list) else []
    for item in candidates:
        path = ROOT / str(item.get("path", ""))
        expected = str(item.get("sha256", ""))
        if not path.is_file() or not SHA_RE.fullmatch(expected):
            errors.append(f"development candidate {item.get('id')}: missing file or invalid sha256")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(
                f"development candidate {item.get('id')}: hash mismatch expected {expected}, got {actual}"
            )
        if item.get("id") == "evidence-claims-v2":
            lines = path.read_text(encoding="utf-8").splitlines()
            if not 80 <= len(lines) <= 120:
                errors.append(f"evidence-claims-v2 must be 80-120 lines, got {len(lines)}")
        if item.get("id") == "current":
            blob = subprocess.run(
                ["git", "rev-parse", "HEAD:skills/adversarial-review/SKILL.md"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            working_blob = subprocess.run(
                ["git", "hash-object", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if blob != PROMOTED_SKILL_BLOB or working_blob != blob:
                errors.append("development experiment changed the promoted adversarial-review skill")
    runtime = data.get("tested_runtime", {})
    if runtime.get("provider") != "muse" or runtime.get("model") != SPIKE_MODEL:
        errors.append("development tested runtime must remain Muse/Spark")
    grader = data.get("grader", {})
    if grader.get("model") != "gpt-5.6-terra" or grader.get("reasoning_effort") != "high":
        errors.append("development independent grader must remain Terra at high reasoning")
    return errors


def validate_development_cases() -> list[str]:
    errors: list[str] = []
    cases = _load(DEVELOPMENT_CASES)
    if not isinstance(cases, list) or len(cases) != 3:
        return ["development case pack must contain exactly three cases"]
    ids = {case.get("metadata", {}).get("case_id") for case in cases}
    if ids != DEVELOPMENT_CASE_IDS:
        errors.append(f"development case ids changed: {sorted(str(item) for item in ids)}")
    family_members: dict[str, set[str]] = {}
    clean_controls = 0
    for case in cases:
        metadata = case.get("metadata", {})
        variables = case.get("vars", {})
        case_id = metadata.get("case_id")
        family = metadata.get("family")
        family_members.setdefault(str(family), set()).add(str(case_id))
        if metadata.get("split") != "development" or metadata.get("data_role") != "development":
            errors.append(f"{case_id}: must remain disclosed development data")
        if metadata.get("exposure") != "disclosed-before-candidate-freeze":
            errors.append(f"{case_id}: disclosure status changed")
        if metadata.get("human_adjudication") is not False:
            errors.append(f"{case_id}: must not claim human adjudication")
        if metadata.get("gold_status") not in {
            "independently-reproduced-development",
            "independently-verified-clean-scope",
        }:
            errors.append(f"{case_id}: invalid development gold status")
        if variables.get("source_repository") != "gen-v-research-tools":
            errors.append(f"{case_id}: source repository changed")
        for key in ("base_sha", "base_tree_sha", "head_sha", "head_tree_sha"):
            if not isinstance(variables.get(key), str) or not re.fullmatch(
                r"[0-9a-f]{40}", variables.get(key, "")
            ):
                errors.append(f"{case_id}: {key} must be a full lowercase SHA")
        if variables.get("base_sha") == variables.get("head_sha"):
            errors.append(f"{case_id}: development cases must use a nonempty change")
        expected = variables.get("expected_verdict")
        gold = str(variables.get("gold_findings", ""))
        if expected == "APPROVE" and gold == "NONE":
            clean_controls += 1
        elif expected != "NEEDS_FIXES" or not re.search(
            r"\((?:blocking|should-fix|low)\)", gold
        ):
            errors.append(f"{case_id}: defect case lacks a severity-bearing development oracle")
    expected_pr90 = {"genv-pr90-evidence-claim", "genv-pr90-synchronized-clean"}
    if family_members.get("genv-pr90") != expected_pr90:
        errors.append("related PR #90 repair heads must remain one development family")
    if clean_controls != 1:
        errors.append("development case pack must contain exactly one scoped nonempty clean control")
    return errors


def validate_development_config() -> list[str]:
    errors: list[str] = []
    config = _load(DEVELOPMENT_CONFIG)
    if config.get("prompts") != ["file://prompts/review.txt"]:
        errors.append("development config must use only the canonical review prompt")
    if (
        not DEVELOPMENT_PROMPT.is_file()
        or hashlib.sha256(DEVELOPMENT_PROMPT.read_bytes()).hexdigest()
        != DEVELOPMENT_PROMPT_SHA256
    ):
        errors.append("canonical development review prompt content changed")
    providers = config.get("providers", [])
    labels = [provider.get("label") for provider in providers]
    if len(providers) != 2 or set(labels) != set(DEVELOPMENT_PROVIDER_LABELS):
        errors.append("development config must contain exactly two candidate providers")
    manifest = _load(DEVELOPMENT_MANIFEST)
    manifest_by_id = {item.get("id"): item for item in manifest.get("candidates", [])}
    for provider in providers:
        label = provider.get("label")
        expected = DEVELOPMENT_PROVIDER_LABELS.get(label)
        item = provider.get("config", {})
        if expected and (
            provider.get("id"), item.get("variant"), item.get("candidate_id")
        ) != ("file://providers/muse_provider.py", *expected):
            errors.append(f"{label}: development provider mapping changed")
        candidate = manifest_by_id.get(item.get("candidate_id"))
        if not candidate or item.get("candidate_sha256") != candidate.get("sha256"):
            errors.append(f"{label}: configured candidate hash differs from development manifest")
        expected_config = {
            "variant": expected[0] if expected else None,
            "candidate_id": expected[1] if expected else None,
            "candidate_sha256": candidate.get("sha256") if candidate else None,
            "model": SPIKE_MODEL,
            "reasoning_effort": "high",
            "timeout": 600_000,
            "max_model_steps": 24,
            "max_tool_output_bytes": 200_000,
            "timeout_seconds": 540,
        }
        if item != expected_config:
            errors.append(f"{label}: development runtime profile changed")
    options = config.get("evaluateOptions", {})
    if options != {"maxConcurrency": 1, "timeoutMs": SPIKE_EVAL_TIMEOUT_MS}:
        errors.append("development concurrency or per-row timeout changed")
    default_test = config.get("defaultTest", {})
    default_options = default_test.get("options", {})
    expected_grader = {
        "id": "openai:codex-sdk:gpt-5.6-terra",
        "config": {
            "model_reasoning_effort": "high",
            "sandbox_mode": "read-only",
            "working_dir": "../..",
        },
    }
    if default_options != {"disableVarExpansion": True, "provider": expected_grader}:
        errors.append("development grader or variable-expansion boundary changed")
    assertions = default_test.get("assert", [])
    expected_python = {
        "review_contract": "file://assertions/review_contract.py:assert_review_contract",
        "verdict_accuracy": "file://assertions/review_contract.py:assert_expected_verdict",
        "skill_observation": "file://assertions/review_contract.py:assert_skill_observation",
        "candidate_integrity": "file://assertions/review_contract.py:assert_candidate_identity",
        "independent_grading": "file://assertions/review_contract.py:assert_no_self_grading",
        "evidence": "file://assertions/review_contract.py:assert_evidence",
    }
    expected_rubrics = {
        "gold_recall": (
            {"gold_findings"},
            "73bf5bb8466548fd729a53791840e86ab8ef7abf3fc63a574bf699820084592f",
        ),
        "blocking_recall": (
            {"gold_findings"},
            "aa419e6a7a9d38a256db437fab974762b499caf84330b56965afb84e2e820df6",
        ),
        "supported_precision": (
            {"resolved_findings"},
            "7c2a3d7083ce116a587005481b49aaa14d662a69582aebaffa966f0471de48ed",
        ),
    }
    if not isinstance(assertions, list) or len(assertions) != 9:
        errors.append("development config must contain exactly six Python and three rubric assertions")
        assertions = assertions if isinstance(assertions, list) else []
    by_metric = {
        item.get("metric"): item for item in assertions if isinstance(item, dict)
    }
    if set(by_metric) != set(expected_python) | set(expected_rubrics):
        errors.append("development assertion metric set changed")
    for metric, value in expected_python.items():
        item = by_metric.get(metric, {})
        if item != {"type": "python", "value": value, "metric": metric}:
            errors.append(f"{metric}: development Python assertion changed")
    for metric, (template_variables, rubric_sha256) in expected_rubrics.items():
        item = by_metric.get(metric, {})
        value = item.get("value")
        observed_variables = set(
            re.findall(r"{{\s*([a-zA-Z0-9_]+)\s*}}", value)
        ) if isinstance(value, str) else set()
        if (
            item.get("type") != "llm-rubric"
            or item.get("threshold") != 0.75
            or observed_variables != template_variables
            or not isinstance(value, str)
            or hashlib.sha256(value.encode("utf-8")).hexdigest() != rubric_sha256
            or set(item) != {"type", "metric", "threshold", "value"}
        ):
            errors.append(f"{metric}: development rubric contract changed")
    if config.get("tests") != "file://cases/development-v2-cases.yaml":
        errors.append("development config case pack changed")
    if config.get("sharing") is not False:
        errors.append("development result sharing must remain disabled")
    prompt = DEVELOPMENT_PROMPT.read_text(encoding="utf-8") if DEVELOPMENT_PROMPT.is_file() else ""
    if "gold_findings" in prompt or "resolved_findings" in prompt:
        errors.append("candidate prompt must keep development gold grader-only")
    return errors


def validate_development_v3_manifest() -> list[str]:
    """Validate the versioned calibration profile without rewriting development v2."""

    errors: list[str] = []
    if hashlib.sha256(DEVELOPMENT_V3_MANIFEST.read_bytes()).hexdigest() != DEVELOPMENT_V3_MANIFEST_SHA256:
        errors.append("development v3 manifest bytes changed")
    data = _load(DEVELOPMENT_V3_MANIFEST)
    if (
        data.get("protocol") != "muse-adversarial-review-development-v3"
        or data.get("semantic_evaluation_version") != "muse-review-metrics-v2"
        or data.get("data_role") != "development"
        or data.get("immutable") is not True
        or data.get("hash_algorithm") != "sha256"
        or data.get("supersedes_profile") != "muse-adversarial-review-development-v2"
    ):
        errors.append("development v3 manifest identity or lineage changed")
    v2 = _load(DEVELOPMENT_MANIFEST)
    comparable = ("id", "path", "sha256")
    candidates = data.get("candidates")
    v2_candidates = v2.get("candidates")
    if not isinstance(candidates, list) or not isinstance(v2_candidates, list) or [
        {key: item.get(key) for key in comparable} for item in candidates
    ] != [{key: item.get(key) for key in comparable} for item in v2_candidates]:
        errors.append("development v3 must retain the exact v2 candidate identities")
        candidates = candidates if isinstance(candidates, list) else []
    for item in candidates:
        path = ROOT / str(item.get("path", ""))
        expected = str(item.get("sha256", ""))
        if not path.is_file() or not SHA_RE.fullmatch(expected):
            errors.append(f"development v3 candidate {item.get('id')}: missing file or invalid sha256")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(f"development v3 candidate {item.get('id')}: hash mismatch")
    runtime = data.get("tested_runtime", {})
    grader = data.get("grader", {})
    if runtime != {
        "provider": "muse", "model": SPIKE_MODEL, "reasoning_effort": "high"
    }:
        errors.append("development v3 tested runtime changed")
    if grader.get("model") != "gpt-5.6-terra" or grader.get("reasoning_effort") != "high":
        errors.append("development v3 independent grader changed")
    return errors


def validate_development_v3_cases() -> list[str]:
    errors: list[str] = []
    if hashlib.sha256(DEVELOPMENT_V3_CASES.read_bytes()).hexdigest() != DEVELOPMENT_V3_CASES_SHA256:
        errors.append("development v3 case-pack bytes changed")
    cases = _load(DEVELOPMENT_V3_CASES)
    if not isinstance(cases, list) or len(cases) != 3:
        return ["development v3 case pack must contain exactly three cases"]
    by_id = {case.get("metadata", {}).get("case_id"): case for case in cases}
    if set(by_id) != DEVELOPMENT_CASE_IDS:
        errors.append("development v3 case identities changed")
        return errors
    v2_by_id = {
        case.get("metadata", {}).get("case_id"): case for case in _load(DEVELOPMENT_CASES)
    }
    for case_id, case in by_id.items():
        metadata = case.get("metadata", {})
        variables = case.get("vars", {})
        if metadata.get("split") != "development" or metadata.get("data_role") != "development":
            errors.append(f"{case_id}: must remain disclosed development data")
        if metadata.get("human_adjudication") is not False:
            errors.append(f"{case_id}: must not claim human adjudication")
        if metadata.get("exposure") != "disclosed-before-candidate-freeze":
            errors.append(f"{case_id}: disclosure status changed")
        if metadata.get("gold_status") != "independently-reproduced-development":
            errors.append(f"{case_id}: corrected gold must remain agent-verified development data")
        for key in ("source_repository", "base_sha", "base_tree_sha", "head_sha", "head_tree_sha"):
            if variables.get(key) != v2_by_id[case_id].get("vars", {}).get(key):
                errors.append(f"{case_id}: pinned source identity changed")
        if variables.get("expected_verdict") != v2_by_id[case_id].get("vars", {}).get("expected_verdict"):
            errors.append(f"{case_id}: expected terminal verdict changed")
        if not re.search(r"\((?:blocking|should-fix|low)\)", str(variables.get("gold_findings", ""))):
            errors.append(f"{case_id}: corrected oracle lacks a severity-bearing finding")
    control = by_id["genv-pr90-synchronized-clean"].get("vars", {})
    if (
        "review-history-metadata-mismatch (low)" not in str(control.get("gold_findings", ""))
        or "tracked finalization metadata are synchronized" in str(control.get("resolved_findings", ""))
    ):
        errors.append("development v3 approval control must retain the corrected low finding")
    return errors


def validate_development_v3_config() -> list[str]:
    return _validate_v3_profile_config(
        DEVELOPMENT_V3_CONFIG,
        DEVELOPMENT_V3_CONFIG_SHA256,
        DEVELOPMENT_V3_MANIFEST,
        DEVELOPMENT_V3_PROVIDER_LABELS,
        "development v3",
    )


def _validate_v3_profile_config(
    config_path: Path,
    config_sha256: str,
    manifest_path: Path,
    provider_labels: dict[str, tuple[str, str]],
    name: str,
) -> list[str]:
    """Pin a config to the development-v3 prompt, cases, grader, rubrics and budgets."""

    errors: list[str] = []
    if hashlib.sha256(config_path.read_bytes()).hexdigest() != config_sha256:
        errors.append(f"{name} config bytes changed")
    config = _load(config_path)
    if config.get("prompts") != ["file://prompts/review.txt"]:
        errors.append(f"{name} must use only the canonical review prompt")
    if (
        not DEVELOPMENT_PROMPT.is_file()
        or hashlib.sha256(DEVELOPMENT_PROMPT.read_bytes()).hexdigest()
        != DEVELOPMENT_PROMPT_SHA256
    ):
        errors.append("canonical development review prompt content changed")
    providers = config.get("providers", [])
    manifest = _load(manifest_path)
    manifest_by_id = {item.get("id"): item for item in manifest.get("candidates", [])}
    if len(providers) != 2 or {item.get("label") for item in providers} != set(provider_labels):
        errors.append(f"{name} must contain exactly two candidate providers")
    for provider in providers:
        label = provider.get("label")
        expected = provider_labels.get(label)
        item = provider.get("config", {})
        candidate = manifest_by_id.get(item.get("candidate_id"))
        if expected and (provider.get("id"), item.get("variant"), item.get("candidate_id")) != (
            "file://providers/muse_provider.py", *expected
        ):
            errors.append(f"{label}: {name} provider mapping changed")
        expected_config = {
            "variant": expected[0] if expected else None,
            "candidate_id": expected[1] if expected else None,
            "candidate_sha256": candidate.get("sha256") if candidate else None,
            "model": SPIKE_MODEL,
            "reasoning_effort": "high",
            "timeout": 600_000,
            "max_model_steps": 24,
            "max_tool_output_bytes": 200_000,
            "timeout_seconds": 540,
        }
        if item != expected_config:
            errors.append(f"{label}: {name} runtime profile changed")
    if config.get("evaluateOptions") != {"maxConcurrency": 1, "timeoutMs": SPIKE_EVAL_TIMEOUT_MS}:
        errors.append(f"{name} concurrency or timeout changed")
    expected_grader = {
        "id": "openai:codex-sdk:gpt-5.6-terra",
        "config": {"model_reasoning_effort": "high", "sandbox_mode": "read-only", "working_dir": "../.."},
    }
    options = config.get("defaultTest", {}).get("options", {})
    if options != {"disableVarExpansion": True, "provider": expected_grader}:
        errors.append(f"{name} grader or variable-expansion boundary changed")
    assertions = config.get("defaultTest", {}).get("assert", [])
    expected_python = {
        "review_contract": "file://assertions/review_contract.py:assert_review_contract",
        "verdict_accuracy": "file://assertions/review_contract.py:assert_expected_verdict",
        "skill_observation": "file://assertions/review_contract.py:assert_skill_observation",
        "candidate_integrity": "file://assertions/review_contract.py:assert_candidate_identity",
        "independent_grading": "file://assertions/review_contract.py:assert_no_self_grading",
        "evidence": "file://assertions/review_contract.py:assert_evidence",
    }
    expected_rubrics = {
        "gold_recall": ({"gold_findings"}, "ce31880e8553e32fa65eec3f25eb75e691c1fcd6543fa6be7f7b786cf12ea3aa"),
        "blocking_recall": ({"gold_findings"}, "d2156973fd48d5bbde351be7d37e2c49442708a427efccbcefe5038c09a7c27d"),
        "supported_precision": ({"resolved_findings"}, "7c2a3d7083ce116a587005481b49aaa14d662a69582aebaffa966f0471de48ed"),
    }
    if not isinstance(assertions, list) or len(assertions) != 9:
        errors.append(f"{name} must contain exactly six Python and three rubric assertions")
        assertions = assertions if isinstance(assertions, list) else []
    by_metric = {item.get("metric"): item for item in assertions if isinstance(item, dict)}
    if set(by_metric) != set(expected_python) | set(expected_rubrics):
        errors.append(f"{name} assertion metric set changed")
    for metric, value in expected_python.items():
        if by_metric.get(metric) != {"type": "python", "value": value, "metric": metric}:
            errors.append(f"{metric}: {name} Python assertion changed")
    for metric, (variables, digest) in expected_rubrics.items():
        item = by_metric.get(metric, {})
        value = item.get("value")
        observed = set(re.findall(r"{{\s*([a-zA-Z0-9_]+)\s*}}", value)) if isinstance(value, str) else set()
        if (
            item.get("type") != "llm-rubric" or item.get("threshold") != 0.75
            or observed != variables or not isinstance(value, str)
            or hashlib.sha256(value.encode()).hexdigest() != digest
            or set(item) != {"type", "metric", "threshold", "value"}
        ):
            errors.append(f"{metric}: {name} rubric contract changed")
    if config.get("tests") != "file://cases/development-v3-cases.yaml":
        errors.append(f"{name} must reuse the development v3 case pack")
    if config.get("sharing") is not False:
        errors.append(f"{name} result sharing must remain disabled")
    prompt = DEVELOPMENT_PROMPT.read_text(encoding="utf-8") if DEVELOPMENT_PROMPT.is_file() else ""
    if "gold_findings" in prompt or "resolved_findings" in prompt:
        errors.append(f"candidate prompt must keep {name} gold grader-only")
    return errors


def validate_screen_v3_manifest() -> list[str]:
    """Pin the issue #18 screen to exact v2/v3 bytes and the development-v3 runtime."""

    errors: list[str] = []
    if hashlib.sha256(SCREEN_V3_MANIFEST.read_bytes()).hexdigest() != SCREEN_V3_MANIFEST_SHA256:
        errors.append("v3 screen manifest bytes changed")
    data = _load(SCREEN_V3_MANIFEST)
    profile = _load(DEVELOPMENT_V3_MANIFEST)
    if (
        data.get("protocol") != "muse-evidence-claims-v3-screen"
        or data.get("evaluation_profile") != profile.get("protocol")
        or data.get("semantic_evaluation_version") != profile.get("semantic_evaluation_version")
        or data.get("data_role") != "development"
        or data.get("immutable") is not True
        or data.get("hash_algorithm") != "sha256"
    ):
        errors.append("v3 screen manifest identity or profile lineage changed")
    if data.get("tested_runtime") != profile.get("tested_runtime") or data.get("grader") != profile.get("grader"):
        errors.append("v3 screen must reuse the development v3 runtime and grader")
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return errors + ["v3 screen candidates must be a list"]
    baseline = next(
        (item for item in profile.get("candidates", []) if item.get("id") == "evidence-claims-v2"), {}
    )
    expected = [
        {key: baseline.get(key) for key in ("id", "path", "sha256")},
        {
            "id": "evidence-claims-v3",
            "path": "evals/behavioral/candidates/evidence-claims-v3.md",
            "sha256": EVIDENCE_CLAIMS_V3_SHA256,
        },
    ]
    if [{key: item.get(key) for key in ("id", "path", "sha256")} for item in candidates] != expected:
        errors.append("v3 screen must compare exactly the frozen v2 and unchanged v3 candidates")
    for item in candidates:
        path = ROOT / str(item.get("path", ""))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item.get("sha256"):
            errors.append(f"v3 screen candidate {item.get('id')}: missing file or hash mismatch")
    return errors


def validate_screen_v3_config() -> list[str]:
    return _validate_v3_profile_config(
        SCREEN_V3_CONFIG,
        SCREEN_V3_CONFIG_SHA256,
        SCREEN_V3_MANIFEST,
        SCREEN_V3_PROVIDER_LABELS,
        "v3 screen",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "validate",
            "validate-heldout",
            "validate-development",
            "validate-development-v3",
            "validate-evidence-claims-v3-screen",
        ),
    )
    parser.add_argument("--finalist-label")
    parser.add_argument("--finalist-sha256")
    args = parser.parse_args()
    errors = validate_manifest() + validate_config()
    if args.command == "validate":
        errors += validate_cases()
    elif args.command == "validate-heldout":
        if not args.finalist_label or not args.finalist_sha256:
            errors.append("validate-heldout requires --finalist-label and --finalist-sha256")
        else:
            errors += validate_heldout(args.finalist_label, args.finalist_sha256)
    elif args.command == "validate-development":
        errors += validate_cases()
        errors += validate_development_manifest()
        errors += validate_development_cases()
        errors += validate_development_config()
    elif args.command == "validate-evidence-claims-v3-screen":
        errors += validate_cases()
        errors += validate_development_v3_manifest()
        errors += validate_development_v3_cases()
        errors += validate_development_v3_config()
        errors += validate_screen_v3_manifest()
        errors += validate_screen_v3_config()
    else:
        errors += validate_cases()
        errors += validate_development_v3_manifest()
        errors += validate_development_v3_cases()
        errors += validate_development_v3_config()
    if errors:
        print("SPIKE VALIDATION FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    if args.command == "validate-development":
        print(
            "DEVELOPMENT V2 VALIDATION PASSED: frozen v1 plus two candidates, "
            "three disclosed cases, identities, budgets, grader, and gold boundary"
        )
    elif args.command == "validate-development-v3":
        print(
            "DEVELOPMENT V3 VALIDATION PASSED: frozen v1/v2 plus corrected development "
            "truth, exact candidates, budgets, grader, and gold boundary"
        )
    elif args.command == "validate-evidence-claims-v3-screen":
        print(
            "EVIDENCE-CLAIMS V3 SCREEN VALIDATION PASSED: exact v2/v3 candidates on the "
            "unchanged development v3 cases, runtime, budgets, grader, and gold boundary"
        )
    else:
        print("SPIKE VALIDATION PASSED: candidates, hashes, frozen splits, budgets, grader, and gold sentinels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
