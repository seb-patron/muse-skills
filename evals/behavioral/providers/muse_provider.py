"""Promptfoo provider that runs Muse against an isolated historical Git head."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from .workspace import (
        ProviderError,
        prepare_historical_workspace,
        prepare_remote_historical_workspace,
        repo_root as _repo_root,
        resolve_commit as _resolve_commit,
        validate_skill_name,
    )
except ImportError:  # Promptfoo loads provider files outside their package.
    from workspace import (  # type: ignore[no-redef]
        ProviderError,
        prepare_historical_workspace,
        prepare_remote_historical_workspace,
        repo_root as _repo_root,
        resolve_commit as _resolve_commit,
        validate_skill_name,
    )

VARIANTS = {"none", "current", "candidate"}
CANDIDATE_PATHS = {
    "evidence-claims-v2": Path("evals/behavioral/candidates/evidence-claims-v2.md"),
    "minimal": Path("evals/behavioral/candidates/minimal.md"),
    "risk-first": Path("evals/behavioral/candidates/risk-first.md"),
    "upstream-adapted": Path("evals/behavioral/candidates/upstream-adapted.md"),
}
REMOTE_CASE_SOURCES = {
    "gen-v-research-tools": "https://github.com/seb-patron/gen-v-research-tools.git",
}
CONTROL_SKILL = """---
name: {skill_name}
description: Evaluation control placeholder with no review instructions. Do not load it.
---

# Evaluation control

This placeholder intentionally contains no review guidance.
"""


def _prepare_workspace(
    repo_root: Path,
    destination: Path,
    base_revision: str,
    head_revision: str,
    variant: str,
    skill_name: str,
    candidate_id: str | None = None,
    source_repository: str | None = None,
) -> tuple[str, str]:
    if variant not in VARIANTS:
        raise ProviderError(f"unknown variant {variant!r}; expected one of {sorted(VARIANTS)}")
    validate_skill_name(skill_name)
    if source_repository is None or source_repository == "muse-skills":
        base_sha, head_sha = prepare_historical_workspace(
            repo_root,
            destination,
            base_revision,
            head_revision,
        )
    elif source_repository in REMOTE_CASE_SOURCES:
        base_sha, head_sha = prepare_remote_historical_workspace(
            REMOTE_CASE_SOURCES[source_repository],
            destination,
            base_revision,
            head_revision,
        )
    else:
        raise ProviderError(
            f"unknown case source {source_repository!r}; expected one of "
            f"{sorted([*REMOTE_CASE_SOURCES, 'muse-skills'])}"
        )

    target = destination / ".agents" / "skills" / skill_name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if variant in {"current", "candidate"}:
        if variant == "current":
            source = repo_root / "skills" / skill_name / "SKILL.md"
        else:
            if candidate_id not in CANDIDATE_PATHS:
                raise ProviderError(
                    f"unknown candidate {candidate_id!r}; expected one of {sorted(CANDIDATE_PATHS)}"
                )
            source = repo_root / CANDIDATE_PATHS[candidate_id]
        if not source.is_file():
            raise ProviderError(f"current skill does not exist: {source}")
        shutil.copyfile(source, target)
    else:
        target.write_text(CONTROL_SKILL.format(skill_name=skill_name), encoding="utf-8")

    exclude = destination / ".git" / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write("\n.agents/\n")

    return base_sha, head_sha


def _candidate_identity(
    repo_root: Path,
    variant: str,
    skill_name: str,
    candidate_id: str | None,
    expected_sha256: str | None = None,
) -> dict[str, str | None]:
    if variant == "none":
        return {
            "candidateId": None,
            "candidatePath": None,
            "candidateSha256": None,
            "candidateDelivery": "withheld",
        }
    if variant == "current":
        path = Path("skills") / skill_name / "SKILL.md"
        resolved_id = "current"
    elif variant == "candidate" and candidate_id in CANDIDATE_PATHS:
        path = CANDIDATE_PATHS[candidate_id]
        resolved_id = candidate_id
    else:
        raise ProviderError(f"cannot resolve candidate identity for variant={variant!r}, id={candidate_id!r}")
    source = repo_root / path
    if not source.is_file():
        raise ProviderError(f"candidate source does not exist: {source}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ProviderError(
            f"candidate {resolved_id} hash mismatch: expected {expected_sha256}, got {digest}"
        )
    return {
        "candidateId": resolved_id,
        "candidatePath": str(path),
        "candidateSha256": digest,
        "candidateDelivery": "project-skill",
    }


def _verify_source_trees(
    workspace: Path,
    base_sha: str,
    head_sha: str,
    expected_base_tree: str | None,
    expected_head_tree: str | None,
) -> None:
    for label, commit, expected in (
        ("base", base_sha, expected_base_tree),
        ("head", head_sha, expected_head_tree),
    ):
        if expected is None:
            continue
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{40}", expected):
            raise ProviderError(f"expected {label} tree must be a full lowercase SHA")
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{commit}^{{tree}}"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        actual = result.stdout.strip()
        if result.returncode != 0 or actual != expected:
            raise ProviderError(
                f"{label} tree mismatch: expected {expected}, got {actual or 'unavailable'}"
            )


def _activate_skill_prompt(prompt: str, skill_name: str) -> str:
    return (
        f"Call the read_skill tool for `{skill_name}` before inspecting the change, "
        "then follow that project skill for this task.\n\n"
        f"{prompt}"
    )


def _run_muse(
    args: list[str], workspace: Path, timeout_seconds: int, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run Muse with a killable process group so timeouts cannot leak pipe holders."""

    process = subprocess.Popen(
        args,
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
        env=env,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            args,
            timeout_seconds,
            output=stdout or exc.output,
            stderr=stderr or exc.stderr,
        ) from None
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def _usage_from_event(event: dict[str, Any]) -> dict[str, int]:
    payload = event.get("payload")
    sources = [event, payload] if isinstance(payload, dict) else [event]
    for source in sources:
        for key in ("usage", "token_usage", "tokenUsage"):
            usage = source.get(key)
            if not isinstance(usage, dict):
                continue
            values = {
                str(name): int(value)
                for name, value in usage.items()
                if isinstance(name, str) and isinstance(value, int) and value >= 0
            }
            prompt = values.get("prompt", values.get("input_tokens", values.get("input", 0)))
            completion = values.get(
                "completion", values.get("output_tokens", values.get("output", 0))
            )
            total = values.get("total", values.get("total_tokens", prompt + completion))
            return {"prompt": prompt, "completion": completion, "total": total}
    return {}


def _parse_muse_stream(stdout: str, skill_name: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)

    terminal_text = ""
    deltas: list[str] = []
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event.get("payload_type") == "run.output.delta" and isinstance(payload.get("text"), str):
            deltas.append(payload["text"])
        if event.get("payload_type") == "run.terminal.completed" and isinstance(payload.get("text"), str):
            terminal_text = payload["text"]

    if not terminal_text:
        terminal_text = "".join(deltas)

    observed = False
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event.get("payload_type") == "agent.skill_read.observed" and payload.get("skill_id") == skill_name:
            observed = True
            break
        correlation = payload.get("correlation_facts")
        if (
            event.get("payload_type") == "tool.result"
            and isinstance(correlation, dict)
            and correlation.get("tool_name") == "read_skill"
            and f'<read-skill-result name="{skill_name}" status="ok">' in str(payload.get("text", ""))
        ):
            observed = True
            break

    models: set[str] = set()
    usage: dict[str, int] = {}
    for event in events:
        payload = event.get("payload")
        for source in (event, payload) if isinstance(payload, dict) else (event,):
            for key in ("model", "model_id", "modelId"):
                value = source.get(key)
                if isinstance(value, str) and value:
                    models.add(value)
        event_usage = _usage_from_event(event)
        if event_usage:
            usage = event_usage

    return {
        "output": terminal_text,
        "eventCount": len(events),
        "skillObserved": observed,
        "models": sorted(models),
        "usage": usage,
    }


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    config = options.get("config") or {}
    variables = context.get("vars") or {}
    variant = str(config.get("variant", "none"))
    candidate_id = config.get("candidate_id")
    if candidate_id is not None:
        candidate_id = str(candidate_id)
    skill_name = str(variables.get("skill_name", ""))
    muse_binary = str(config.get("muse_binary") or os.environ.get("MUSE_EVAL_BINARY") or "muse")
    timeout_seconds = int(config.get("timeout_seconds", 1200))

    try:
        repo_root = _repo_root(options)
        if not (repo_root / ".git").exists():
            raise ProviderError(f"repository root is not a Git checkout: {repo_root}")
        if shutil.which(muse_binary) is None and not Path(muse_binary).is_file():
            raise ProviderError(f"Muse executable not found: {muse_binary}")

        # Muse's session lease is workspace-root scoped and rejects disposable
        # clones created outside the configured project root. Keep the clone
        # disposable, but place it under that permitted root so the same provider
        # works in headless desktop runs and in the historical fixture tests.
        with tempfile.TemporaryDirectory(prefix=".muse-skill-eval-", dir=repo_root) as temp:
            workspace = Path(temp) / "repo"
            identity = _candidate_identity(
                repo_root,
                variant,
                skill_name,
                candidate_id,
                str(config.get("candidate_sha256")) if config.get("candidate_sha256") else None,
            )
            base_sha, head_sha = _prepare_workspace(
                repo_root,
                workspace,
                str(variables.get("base_sha", "")),
                str(variables.get("head_sha", "")),
                variant,
                skill_name,
                candidate_id,
                str(variables.get("source_repository"))
                if variables.get("source_repository")
                else None,
            )
            _verify_source_trees(
                workspace,
                base_sha,
                head_sha,
                str(variables.get("base_tree_sha"))
                if variables.get("base_tree_sha")
                else None,
                str(variables.get("head_tree_sha"))
                if variables.get("head_tree_sha")
                else None,
            )

            spike_run = "candidate_id" in config
            configured_model = str(config.get("model") or "")
            override_model = os.environ.get("MUSE_EVAL_MODEL")
            if spike_run and override_model and override_model != configured_model:
                raise ProviderError(
                    "MUSE_EVAL_MODEL is disabled for the frozen spike; use the configured "
                    f"model {configured_model!r}"
                )
            if not configured_model and not spike_run:
                configured_model = override_model or ""
            if not configured_model and spike_run:
                raise ProviderError("spike provider requires an explicit Muse model")

            args = [
                muse_binary,
                "exec",
                "--provider",
                "meta",
                "--json",
                "--workspace",
                str(workspace),
                "--trust-workspace",
                "--no-foreign-personal-context",
                "--disable-web-tools",
                "--sandbox-network",
                "restricted",
                "--disable-approval",
                "--user-input-auto-resolve",
                "--max-model-steps",
                str(int(config.get("max_model_steps", 24))),
                "--max-tool-output-bytes",
                str(int(config.get("max_tool_output_bytes", 200000))),
                "--reasoning-effort",
                str(config.get("reasoning_effort", "high")),
            ]
            if configured_model:
                args.extend(["--model", configured_model])
            if variant in {"current", "candidate"}:
                prompt = _activate_skill_prompt(prompt, skill_name)
            args.append(prompt)

            env = os.environ.copy()
            env.update({"MUSE_NO_AUTO_UPDATE": "1", "NO_COLOR": "1"})
            started = time.monotonic()
            try:
                result = _run_muse(args, workspace, timeout_seconds, env)
            except subprocess.TimeoutExpired as exc:
                return {"error": f"Muse timed out after {timeout_seconds}s: {exc}"}
            latency_ms = round((time.monotonic() - started) * 1000)
            parsed = _parse_muse_stream(result.stdout, skill_name)
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout[-2000:]
                return {"error": f"Muse exited {result.returncode}: {detail}"}
            if not parsed["output"].strip():
                return {"error": "Muse completed without a terminal review output"}
            if spike_run and variant in {"current", "candidate"} and not parsed["skillObserved"]:
                return {"error": "Muse completed without observing the delivered project skill"}
            if spike_run and not parsed["models"]:
                return {"error": "Muse completed without observed model telemetry"}
            if spike_run and parsed["models"] != [configured_model]:
                return {
                    "error": (
                        f"Muse model telemetry mismatch: expected {configured_model}, "
                        f"observed {parsed['models']}"
                    )
                }

            response = {
                "output": parsed["output"],
                "latencyMs": latency_ms,
                "cached": False,
                "metadata": {
                    "runtime": "muse",
                    "variant": variant,
                    "skillName": skill_name if variant != "none" else None,
                    "candidateId": identity["candidateId"],
                    "candidatePath": identity["candidatePath"],
                    "candidateSha256": identity["candidateSha256"],
                    "candidateDelivery": identity["candidateDelivery"],
                    "skillDelivery": "project-skill" if variant != "none" else "withheld",
                    "skillActivation": "explicit-read_skill" if variant != "none" else "withheld",
                    "skillObserved": parsed["skillObserved"],
                    "baseSha": base_sha,
                    "headSha": head_sha,
                    "sourceRepository": variables.get("source_repository", "muse-skills"),
                    "museModels": parsed["models"],
                    "museExpectedModel": configured_model or None,
                    "museTokenUsage": parsed["usage"] or None,
                    "candidateTokenStatus": "observed" if parsed["usage"] else "unavailable",
                    "eventCount": parsed["eventCount"],
                    "museExitCode": result.returncode,
                    "controlMode": "project-placeholder" if variant == "none" else None,
                    "stderrTail": result.stderr[-1000:],
                },
            }
            if parsed["usage"]:
                response["tokenUsage"] = parsed["usage"]
            return response
    except (OSError, ProviderError, ValueError) as exc:
        return {"error": str(exc)}
