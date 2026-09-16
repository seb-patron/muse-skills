"""Offline validation and leakage preflight for the development v4 profile.

Development v4 keeps the frozen development v2/v3 artifacts immutable and adds the
Gen V PR #87 repair-chain stages A, C, and D as disclosed development cases. Every
check here is deterministic and makes no model call and no network call.

The PR-body snapshots are metadata of a private repository, so this public repository
stores only their digests. Checks that need the snapshot text run when the local
snapshot directory is present and are reported as skipped when it is not; the
structural, identity, and immutability checks always run.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
BEHAVIORAL = ROOT / "evals/behavioral"
V3_CASES = BEHAVIORAL / "cases/development-v3-cases.yaml"
V4_CASES = BEHAVIORAL / "cases/development-v4-cases.yaml"
V3_MANIFEST = BEHAVIORAL / "candidates/development-v3-manifest.yaml"
V4_MANIFEST = BEHAVIORAL / "candidates/development-v4-manifest.yaml"
V3_CONFIG = BEHAVIORAL / "development-v3-promptfooconfig.yaml"
V4_CONFIG = BEHAVIORAL / "development-v4-promptfooconfig.yaml"
V3_PROMPT = BEHAVIORAL / "prompts/review.txt"
V4_PROMPT = BEHAVIORAL / "prompts/review-v4.txt"
SNAPSHOTS = BEHAVIORAL / "fixtures/genv-pr87/SNAPSHOTS.yaml"
SNAPSHOT_LOADER = BEHAVIORAL / "fixtures/genv-pr87/snapshot_var.py"
SNAPSHOT_LOADER_VAR = "file://fixtures/genv-pr87/snapshot_var.py"
PROVIDER = BEHAVIORAL / "providers/muse_provider.py"

SPIKE_MODEL = "muse-spark-1.3-contributor"
EVAL_TIMEOUT_MS = 900_000
V3_CASES_SHA256 = "095023d053d9360417203d2b8bb69171fef9028a616d7a4cb991ed6461889dd8"
V3_PROMPT_SHA256 = "d66dbd7c66ea1747d64e05dad975f57afbc750ffe6c28166cd7d101cd0ca947e"
RUBRIC_SHA256 = {
    "gold_recall": "ce31880e8553e32fa65eec3f25eb75e691c1fcd6543fa6be7f7b786cf12ea3aa",
    "blocking_recall": "d2156973fd48d5bbde351be7d37e2c49442708a427efccbcefe5038c09a7c27d",
    "supported_precision": "7c2a3d7083ce116a587005481b49aaa14d662a69582aebaffa966f0471de48ed",
}
V3_CASE_IDS = [
    "genv-pr87-first-repair-type-boundary",
    "genv-pr90-evidence-claim",
    "genv-pr90-synchronized-clean",
]
NEW_CASES = {
    "genv-pr87-initial-implementation": "a",
    "genv-pr87-merge-ready-stale-body": "c",
    "genv-pr87-merge-ready-corrected-body": "d",
}
PROVIDER_LABELS = {
    "development-v4-current": ("current", "current"),
    "development-v4-evidence-claims": ("candidate", "evidence-claims-v2"),
}
GRADER_ONLY_VARS = ("gold_findings", "resolved_findings", "expected_verdict")
PROPOSED_GOLD_STATUS = "proposed-pending-human-adjudication"
# Patterns that must never appear in a sanitized snapshot. The redacted commercial title
# is deliberately not spelled out here; digest equality against delivered_sha256 is what
# proves a delivered body is exactly the sanitized export.
FORBIDDEN_SNAPSHOT_PATTERNS = {
    "truncated input digest": re.compile(r"`[0-9a-f]{5,8}…`"),
    "absolute path": re.compile(r"(?:/Users/|/home/|/private/tmp)"),
    "template delimiter": re.compile(r"{{|{%|{#"),
}
FORBIDDEN_REVIEW_PHRASES = (
    "Merge-readiness review: fixes required",
    "REQUEST CHANGES — do not merge yet",
    "Blocking findings",
)
TEXT_SUFFIXES = {".md", ".txt", ".yaml", ".yml", ".json", ".py", ".mjs", ".js"}
_VAR_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")
_IF_RE = re.compile(r"{%\s*if\s+([a-zA-Z0-9_]+)\s*%}(.*?){%\s*endif\s*%}", re.DOTALL)
_TAG_RE = re.compile(r"{%\s*(\w+)")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_LOADER = None


def _load(path: Path) -> Any:
    with Path(path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def loader():
    """Import the hash-verifying snapshot loader by path."""

    global _LOADER
    if _LOADER is None:
        spec = importlib.util.spec_from_file_location(
            "genv_pr87_snapshot_var", SNAPSHOT_LOADER
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADER = module
    return _LOADER


def snapshots_available() -> bool:
    return loader().available()


def delivered(text: str) -> str:
    """Promptfoo trims a file:// variable before rendering it into the prompt."""

    return text.strip()


def render_prompt(template: str, variables: dict[str, Any]) -> str:
    """Render the subset of Nunjucks syntax the review prompts are allowed to use."""

    def _conditional(match: re.Match[str]) -> str:
        name, body = match.group(1), match.group(2)
        return body if str(variables.get(name, "") or "").strip() else ""

    text = _IF_RE.sub(_conditional, template)
    return _VAR_RE.sub(lambda match: str(variables.get(match.group(1), "")), text)


def snapshot_index() -> dict[str, dict[str, Any]]:
    data = _load(SNAPSHOTS)
    return {str(stage): value for stage, value in (data.get("stages") or {}).items()}


def snapshot_text(stage: str) -> str:
    """The digest-verified delivered body for one stage; raises when unavailable."""

    return loader().load_stage(stage)["body"]


def derived_markers() -> dict[str, str]:
    """Derive one distinctive line per stage from the verified snapshots.

    Markers are computed at run time and never stored, because a stored marker would
    be verbatim private-repository text in a public repository.
    """

    texts = {stage: snapshot_text(stage) for stage in snapshot_index()}
    lines = {
        stage: [line.strip() for line in text.splitlines() if len(line.strip()) >= 40]
        for stage, text in texts.items()
    }
    markers: dict[str, str] = {}
    for stage, candidates in lines.items():
        for line in candidates:
            if all(line not in other for name, other in texts.items() if name != stage):
                markers[stage] = line
                break
    return markers


def resolve_case_vars(case: dict[str, Any]) -> dict[str, Any]:
    """Resolve case variables the way Promptfoo resolves them for the candidate."""

    raw = dict(case.get("vars") or {})
    resolved: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, str) and value.startswith("file://") and value.endswith(".py"):
            resolved[key] = str(loader().get_var(key, "", raw)["output"]).strip()
        elif isinstance(value, str) and value.startswith("file://"):
            resolved[key] = delivered(
                (BEHAVIORAL / value[len("file://"):]).read_text(encoding="utf-8")
            )
        else:
            resolved[key] = value
    return resolved


def candidate_visible_text(case: dict[str, Any]) -> str:
    return render_prompt(V4_PROMPT.read_text(encoding="utf-8"), resolve_case_vars(case))


def later_commits(stage: str) -> list[str]:
    data = _load(SNAPSHOTS)
    chain = data.get("chain") or {}
    order = list(chain.get("order") or [])
    stages = snapshot_index()
    own_head = str(stages[stage]["head_sha"])
    later = [
        str(stages[name]["head_sha"])
        for name in order[order.index(stage) + 1:]
        if str(stages[name]["head_sha"]) != own_head
    ]
    merge_commit = chain.get("merge_commit")
    if merge_commit:
        later.append(str(merge_commit))
    return sorted(set(later))


def validate_snapshots() -> list[str]:
    """Structural checks always; digest and content checks when the text is present."""

    errors: list[str] = []
    data = _load(SNAPSHOTS)
    if data.get("source_repository") != "gen-v-research-tools" or data.get("pull_request") != 87:
        errors.append("snapshot manifest does not identify gen-v-research-tools PR #87")
    if not str(data.get("extraction_query", "")).strip().startswith("query"):
        errors.append("snapshot manifest must record the exact GraphQL extraction query")
    if data.get("live_body_stage") != "d":
        errors.append("snapshot manifest must record that the live body is stage D only")
    storage = data.get("storage") or {}
    if storage.get("environment_variable") != "GENV_PR87_SNAPSHOT_DIR" or not storage.get(
        "default_directory"
    ):
        errors.append("snapshot manifest must document the external snapshot directory")
    for key in ("pull_request_title_sha256", "pull_request_title_file_sha256"):
        if not SHA_RE.fullmatch(str(data.get(key, ""))):
            errors.append(f"{key} must be a lowercase SHA-256")
    if any(str(key).startswith("pull_request_title") and key.endswith("title") for key in data):
        errors.append("snapshot manifest must not carry the verbatim pull-request title")
    stages = snapshot_index()
    if sorted(stages) != ["a", "b", "c", "d"]:
        return errors + ["snapshot manifest must freeze exactly stages a, b, c, and d"]
    raw_digests: set[str] = set()
    for stage, entry in sorted(stages.items()):
        for key in ("sha256", "delivered_sha256", "raw_export_sha256"):
            if not SHA_RE.fullmatch(str(entry.get(key, ""))):
                errors.append(f"stage {stage}: {key} must be a lowercase SHA-256")
        raw_digests.add(str(entry.get("raw_export_sha256", "")))
        if not COMMIT_RE.fullmatch(str(entry.get("head_sha", ""))):
            errors.append(f"stage {stage}: head_sha must be a full lowercase SHA")
        if not str(entry.get("edited_at", "")).endswith("Z"):
            errors.append(f"stage {stage}: edited_at must be a UTC snapshot timestamp")
        name = str(entry.get("file", ""))
        if not name or "/" in name:
            errors.append(f"stage {stage}: file must be a bare name in the snapshot directory")
        if (BEHAVIORAL / "fixtures/genv-pr87" / name).exists():
            errors.append(f"stage {stage}: snapshot text must not be committed to this repository")
        if "marker" in entry:
            errors.append(f"stage {stage}: markers are derived at run time, never stored")
    if len(raw_digests) != len(stages):
        errors.append("snapshot manifest reuses one raw export digest for several stages")

    if not snapshots_available():
        return errors
    for stage in sorted(stages):
        try:
            text = snapshot_text(stage)
        except Exception as error:  # noqa: BLE001 - surfaced as a validation error
            errors.append(f"stage {stage}: {error}")
            continue
        for label, pattern in FORBIDDEN_SNAPSHOT_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"stage {stage}: sanitized snapshot still contains a {label}")
    markers = derived_markers()
    missing = sorted(set(stages) - set(markers))
    if missing:
        errors.append(f"no distinctive line could be derived for stages {missing}")
    return errors


def validate_no_committed_snapshot_text() -> list[str]:
    """No tracked or staged file may carry verbatim snapshot text."""

    if not snapshots_available():
        return []
    errors: list[str] = []
    listing = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    lines_by_stage = {
        stage: [
            line.strip()
            for line in snapshot_text(stage).splitlines()
            if len(line.strip()) >= 60
        ]
        for stage in snapshot_index()
    }
    for name in listing.stdout.splitlines():
        path = ROOT / name
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        if path.stat().st_size > 1_000_000:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for stage, lines in lines_by_stage.items():
            if any(line in content for line in lines):
                errors.append(f"{name}: carries verbatim stage {stage} snapshot text")
                break
    return errors


def validate_manifest() -> list[str]:
    errors: list[str] = []
    data = _load(V4_MANIFEST)
    if (
        data.get("protocol") != "muse-adversarial-review-development-v4"
        or data.get("semantic_evaluation_version") != "muse-review-metrics-v2"
        or data.get("data_role") != "development"
        or data.get("immutable") is not True
        or data.get("hash_algorithm") != "sha256"
        or data.get("supersedes_profile") != "muse-adversarial-review-development-v3"
    ):
        errors.append("development v4 manifest identity or lineage changed")
    comparable = ("id", "path", "sha256")
    candidates = data.get("candidates")
    v3_candidates = _load(V3_MANIFEST).get("candidates")
    if not isinstance(candidates, list) or not isinstance(v3_candidates, list) or [
        {key: item.get(key) for key in comparable} for item in candidates
    ] != [{key: item.get(key) for key in comparable} for item in v3_candidates]:
        errors.append("development v4 must retain the exact v3 candidate identities")
        candidates = candidates if isinstance(candidates, list) else []
    for item in candidates:
        path = ROOT / str(item.get("path", ""))
        expected = str(item.get("sha256", ""))
        if not path.is_file() or not SHA_RE.fullmatch(expected):
            errors.append(f"candidate {item.get('id')}: missing file or invalid sha256")
        elif _sha256(path.read_bytes()) != expected:
            errors.append(f"candidate {item.get('id')}: hash mismatch")
    if data.get("tested_runtime") != {
        "provider": "muse", "model": SPIKE_MODEL, "reasoning_effort": "high"
    }:
        errors.append("development v4 tested runtime changed")
    grader = data.get("grader") or {}
    if grader.get("model") != "gpt-5.6-terra" or grader.get("reasoning_effort") != "high":
        errors.append("development v4 independent grader changed")
    return errors


def validate_cases() -> list[str]:
    errors: list[str] = []
    v3_bytes = V3_CASES.read_bytes()
    if _sha256(v3_bytes) != V3_CASES_SHA256:
        errors.append("frozen development v3 case pack changed")
    if not V4_CASES.read_bytes().startswith(v3_bytes):
        errors.append("development v4 must begin with the byte-identical v3 case pack")
    cases = _load(V4_CASES)
    if not isinstance(cases, list) or len(cases) != 6:
        return errors + ["development v4 case pack must contain exactly six cases"]
    ids = [case.get("metadata", {}).get("case_id") for case in cases]
    if ids[:3] != V3_CASE_IDS or sorted(ids[3:]) != sorted(NEW_CASES):
        return errors + [f"development v4 case identities changed: {ids}"]
    if cases[:3] != _load(V3_CASES):
        errors.append("development v4 altered a retained v3 case")
    by_id = {case["metadata"]["case_id"]: case for case in cases}
    stages = snapshot_index()
    clean_controls = 0
    for case_id, stage in NEW_CASES.items():
        case = by_id[case_id]
        metadata = case.get("metadata", {})
        variables = case.get("vars", {})
        if metadata.get("split") != "development" or metadata.get("data_role") != "development":
            errors.append(f"{case_id}: must remain disclosed development data")
        if metadata.get("family") != "genv-pr87":
            errors.append(f"{case_id}: A-D stages must stay in the genv-pr87 family")
        if metadata.get("exposure") != "disclosed-before-candidate-freeze":
            errors.append(f"{case_id}: disclosure status changed")
        if metadata.get("human_adjudication") is not False:
            errors.append(f"{case_id}: must not claim human adjudication")
        if metadata.get("gold_status") != PROPOSED_GOLD_STATUS:
            errors.append(f"{case_id}: gold must stay proposed pending human adjudication")
        if metadata.get("chain_stage") != stage:
            errors.append(f"{case_id}: chain stage does not match the frozen snapshot stage")
        if not str(metadata.get("reproduction", "")).strip():
            errors.append(f"{case_id}: missing a recorded reproduction attempt")
        if variables.get("source_repository") != "gen-v-research-tools":
            errors.append(f"{case_id}: source repository changed")
        for key in ("base_sha", "base_tree_sha", "head_sha", "head_tree_sha"):
            if not COMMIT_RE.fullmatch(str(variables.get(key, ""))):
                errors.append(f"{case_id}: {key} must be a full lowercase SHA")
        entry = stages.get(stage, {})
        if variables.get("head_sha") != entry.get("head_sha"):
            errors.append(f"{case_id}: head does not match the frozen stage head")
        if variables.get("pr_body_stage") != stage:
            errors.append(f"{case_id}: pr_body_stage must select this case's own stage")
        for key in ("pr_body_snapshot", "pr_title_snapshot"):
            if variables.get(key) != SNAPSHOT_LOADER_VAR:
                errors.append(f"{case_id}: {key} must come from the verifying snapshot loader")
        if metadata.get("pr_body_file") != entry.get("file"):
            errors.append(f"{case_id}: pr_body_file disagrees with the snapshot manifest")
        for name, key in (
            ("sha256", "pr_body_sha256"),
            ("delivered_sha256", "pr_body_delivered_sha256"),
        ):
            if metadata.get(key) != entry.get(name):
                errors.append(f"{case_id}: {key} disagrees with the snapshot manifest")
        if metadata.get("pr_body_edited_at") != entry.get("edited_at"):
            errors.append(f"{case_id}: body snapshot timestamp disagrees with the manifest")
        gold = str(variables.get("gold_findings", ""))
        expected = variables.get("expected_verdict")
        if expected == "APPROVE" and gold.strip() == "NONE":
            clean_controls += 1
        elif expected != "NEEDS_FIXES" or not re.search(r"\((?:blocking|should-fix|low)\):", gold):
            errors.append(f"{case_id}: defect case lacks a severity-bearing development oracle")
        if not str(variables.get("resolved_findings", "")).strip():
            errors.append(f"{case_id}: closure expectations must stay in a grader-only field")
    if clean_controls != 1:
        errors.append("the added stages must contain exactly one no-known-defect control")
    stale = by_id["genv-pr87-merge-ready-stale-body"]["vars"]
    corrected = by_id["genv-pr87-merge-ready-corrected-body"]["vars"]
    if (
        stale.get("head_sha") != corrected.get("head_sha")
        or stale.get("head_tree_sha") != corrected.get("head_tree_sha")
    ):
        errors.append("stages C and D must pin the identical code head and tree")
    if stale.get("pr_body_stage") == corrected.get("pr_body_stage"):
        errors.append("stages C and D must differ by their body snapshot")
    if stale.get("expected_verdict") != "NEEDS_FIXES" or corrected.get("expected_verdict") != "APPROVE":
        errors.append("the same-code pair must expect NEEDS_FIXES then APPROVE")
    return errors


def validate_prompt() -> list[str]:
    errors: list[str] = []
    template = V4_PROMPT.read_text(encoding="utf-8")
    if _sha256(V3_PROMPT.read_bytes()) != V3_PROMPT_SHA256:
        errors.append("frozen v3 review prompt changed")
    tags = {match.group(1) for match in _TAG_RE.finditer(template)}
    if tags - {"if", "endif"} or len(_IF_RE.findall(template)) != 1:
        errors.append("v4 prompt may only add one optional snapshot block")
    for name in GRADER_ONLY_VARS:
        if name in template:
            errors.append(f"candidate prompt must keep {name} grader-only")
    if "pr_body_snapshot" not in template or "pr_title_snapshot" not in template:
        errors.append("v4 prompt does not deliver the PR title/body snapshot")
    for case in _load(V4_CASES)[:3]:
        variables = resolve_case_vars(case)
        if render_prompt(template, variables) != render_prompt(
            V3_PROMPT.read_text(encoding="utf-8"), variables
        ):
            case_id = case.get("metadata", {}).get("case_id")
            errors.append(f"{case_id}: retained v3 case no longer renders the v3 prompt")
    return errors


def validate_config() -> list[str]:
    errors: list[str] = []
    config = _load(V4_CONFIG)
    v3_config = _load(V3_CONFIG)
    if config.get("prompts") != ["file://prompts/review-v4.txt"]:
        errors.append("development v4 must use only the versioned review prompt")
    if config.get("tests") != "file://cases/development-v4-cases.yaml":
        errors.append("development v4 case pack changed")
    if config.get("sharing") is not False:
        errors.append("development v4 result sharing must remain disabled")
    if config.get("evaluateOptions") != {"maxConcurrency": 1, "timeoutMs": EVAL_TIMEOUT_MS}:
        errors.append("development v4 concurrency or timeout changed")
    if config.get("defaultTest") != v3_config.get("defaultTest"):
        errors.append("development v4 grader, assertions, or rubrics differ from v3")
    providers = config.get("providers", [])
    manifest_by_id = {item.get("id"): item for item in _load(V4_MANIFEST).get("candidates", [])}
    v3_by_variant = {
        item.get("config", {}).get("variant"): item.get("config", {})
        for item in v3_config.get("providers", [])
    }
    if len(providers) != 2 or {item.get("label") for item in providers} != set(PROVIDER_LABELS):
        errors.append("development v4 must contain exactly two candidate providers")
    for provider in providers:
        label = provider.get("label")
        expected = PROVIDER_LABELS.get(label)
        item = provider.get("config", {})
        if expected and (provider.get("id"), item.get("variant"), item.get("candidate_id")) != (
            "file://providers/muse_provider.py", *expected
        ):
            errors.append(f"{label}: development v4 provider mapping changed")
        candidate = manifest_by_id.get(item.get("candidate_id"))
        if not candidate or item.get("candidate_sha256") != candidate.get("sha256"):
            errors.append(f"{label}: configured candidate hash differs from the manifest")
        if item != v3_by_variant.get(item.get("variant")):
            errors.append(f"{label}: runtime, budget, or model differs from development v3")
    by_metric = {
        item.get("metric"): item
        for item in config.get("defaultTest", {}).get("assert", [])
        if isinstance(item, dict)
    }
    for metric, digest in RUBRIC_SHA256.items():
        value = by_metric.get(metric, {}).get("value")
        if not isinstance(value, str) or _sha256(value.encode("utf-8")) != digest:
            errors.append(f"{metric}: frozen rubric text changed")
    return errors


def validate_network_isolation() -> list[str]:
    """The candidate provider must keep the network and foreign context closed."""

    source = PROVIDER.read_text(encoding="utf-8")
    missing = [
        flag
        for flag in ("--disable-web-tools", "--sandbox-network", "restricted",
                     "--no-foreign-personal-context", "--trust-workspace")
        if flag not in source
    ]
    return [f"candidate provider no longer pins {flag}" for flag in missing]


def check_case_leakage(case: dict[str, Any]) -> list[str]:
    """Fail if a candidate-visible input exposes a later ref or another snapshot.

    Requires the local snapshot directory; callers skip it when the text is absent.
    """

    metadata = case.get("metadata", {})
    case_id = metadata.get("case_id")
    stage = str(metadata.get("chain_stage", ""))
    stages = snapshot_index()
    if stage not in stages:
        return [f"{case_id}: unknown chain stage {stage!r}"]
    errors: list[str] = []
    variables = resolve_case_vars(case)
    visible = render_prompt(V4_PROMPT.read_text(encoding="utf-8"), variables)

    body = str(variables.get("pr_body_snapshot", ""))
    if _sha256(body.encode("utf-8")) != stages[stage].get("delivered_sha256"):
        errors.append(f"{case_id}: delivered body is not the pinned stage snapshot")
    if body not in visible:
        errors.append(f"{case_id}: the stage body snapshot is not delivered to the candidate")

    markers = derived_markers()
    for name in sorted(stages):
        if name == stage:
            continue
        marker = markers.get(name, "")
        if marker and marker in visible:
            errors.append(f"{case_id}: stage {name} body snapshot is visible to the candidate")
        if snapshot_text(name) in visible:
            errors.append(f"{case_id}: a foreign body snapshot is visible to the candidate")

    for commit in later_commits(stage):
        if commit in visible or commit[:7] in visible:
            errors.append(f"{case_id}: later commit {commit[:7]} is visible to the candidate")
    for phrase in FORBIDDEN_REVIEW_PHRASES:
        if phrase in visible:
            errors.append(f"{case_id}: public review text is visible to the candidate")
    for name in GRADER_ONLY_VARS:
        value = str(case.get("vars", {}).get(name, ""))
        if name != "expected_verdict" and value.strip() and value.strip() in visible:
            errors.append(f"{case_id}: grader-only {name} is visible to the candidate")
    return errors


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=workspace, capture_output=True, text=True, check=False
    )


def verify_workspace_isolation(
    workspace: Path, stage: str, head_sha: str, markers: dict[str, str] | None = None
) -> list[str]:
    """Fail if a prepared candidate checkout can reach later answers."""

    errors: list[str] = []
    head = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    if head != head_sha:
        errors.append(f"stage {stage}: checkout is at {head or 'unknown'}, not the frozen head")
    if _git(workspace, "show-ref").stdout.strip():
        errors.append(f"stage {stage}: checkout still carries refs")
    if _git(workspace, "remote").stdout.strip():
        errors.append(f"stage {stage}: checkout still carries a remote")
    for name in (".git/FETCH_HEAD", ".git/objects/info/alternates"):
        if (workspace / name).exists():
            errors.append(f"stage {stage}: checkout retains {name}")
    for commit in later_commits(stage):
        if _git(workspace, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0:
            errors.append(f"stage {stage}: later commit {commit[:7]} is present in the checkout")
    if markers is None:
        markers = derived_markers() if snapshots_available() else {}
    foreign = {name: marker for name, marker in markers.items() if name != stage and marker}
    if not foreign:
        return errors
    for path in sorted(workspace.rglob("*")):
        if ".git" in path.parts or not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 512_000:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, marker in foreign.items():
            if marker in text:
                errors.append(
                    f"stage {stage}: stage {name} body snapshot is visible in the checkout"
                )
    return errors


def validate() -> tuple[list[str], list[str]]:
    """Return (errors, skipped-check notices)."""

    errors = (
        validate_snapshots()
        + validate_manifest()
        + validate_cases()
        + validate_prompt()
        + validate_config()
        + validate_network_isolation()
        + validate_no_committed_snapshot_text()
    )
    skipped: list[str] = []
    if snapshots_available():
        for case in _load(V4_CASES):
            if case.get("metadata", {}).get("case_id") in NEW_CASES:
                errors += check_case_leakage(case)
    else:
        skipped.append(
            "snapshot-dependent checks (digest verification, sanitization scan, per-case "
            f"leakage preflight): set {loader().ENV_VAR} to the local snapshot directory "
            f"(default {loader().DEFAULT_DIR}) to run them"
        )
    return errors, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate",))
    parser.parse_args()
    errors, skipped = validate()
    if errors:
        print("DEVELOPMENT V4 VALIDATION FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "DEVELOPMENT V4 VALIDATION PASSED: frozen v2/v3 artifacts, retained candidates, "
        "budgets and grader, four hash-only PR-body snapshots, proposed-gold boundary, "
        "and no committed snapshot text"
    )
    for notice in skipped:
        print(f"SKIPPED: {notice}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
