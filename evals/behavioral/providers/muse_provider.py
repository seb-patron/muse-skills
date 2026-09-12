"""Promptfoo provider that runs Muse against an isolated historical Git head."""

from __future__ import annotations

import json
import os
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
        repo_root as _repo_root,
        resolve_commit as _resolve_commit,
        validate_skill_name,
    )
except ImportError:  # Promptfoo loads provider files outside their package.
    from workspace import (  # type: ignore[no-redef]
        ProviderError,
        prepare_historical_workspace,
        repo_root as _repo_root,
        resolve_commit as _resolve_commit,
        validate_skill_name,
    )

VARIANTS = {"none", "current"}
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
) -> tuple[str, str]:
    if variant not in VARIANTS:
        raise ProviderError(f"unknown variant {variant!r}; expected one of {sorted(VARIANTS)}")
    validate_skill_name(skill_name)
    base_sha, head_sha = prepare_historical_workspace(
        repo_root,
        destination,
        base_revision,
        head_revision,
    )

    target = destination / ".agents" / "skills" / skill_name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if variant == "current":
        source = repo_root / "skills" / skill_name / "SKILL.md"
        if not source.is_file():
            raise ProviderError(f"current skill does not exist: {source}")
        shutil.copyfile(source, target)
    else:
        target.write_text(CONTROL_SKILL.format(skill_name=skill_name), encoding="utf-8")

    exclude = destination / ".git" / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write("\n.agents/\n")

    return base_sha, head_sha


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
    for event in events:
        payload = event.get("payload")
        if isinstance(payload, dict):
            for key in ("model", "model_id", "modelId"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    models.add(value)

    return {
        "output": terminal_text,
        "eventCount": len(events),
        "skillObserved": observed,
        "models": sorted(models),
    }


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    config = options.get("config") or {}
    variables = context.get("vars") or {}
    variant = str(config.get("variant", "none"))
    skill_name = str(variables.get("skill_name", ""))
    muse_binary = str(config.get("muse_binary") or os.environ.get("MUSE_EVAL_BINARY") or "muse")
    timeout_seconds = int(config.get("timeout_seconds", 1200))

    try:
        repo_root = _repo_root(options)
        if not (repo_root / ".git").exists():
            raise ProviderError(f"repository root is not a Git checkout: {repo_root}")
        if shutil.which(muse_binary) is None and not Path(muse_binary).is_file():
            raise ProviderError(f"Muse executable not found: {muse_binary}")

        with tempfile.TemporaryDirectory(prefix="muse-skill-eval-") as temp:
            workspace = Path(temp) / "repo"
            base_sha, head_sha = _prepare_workspace(
                repo_root,
                workspace,
                str(variables.get("base_sha", "")),
                str(variables.get("head_sha", "")),
                variant,
                skill_name,
            )

            args = [
                muse_binary,
                "exec",
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
            model = os.environ.get("MUSE_EVAL_MODEL") or config.get("model")
            if model:
                args.extend(["--model", str(model)])
            if variant == "current":
                prompt = (
                    f"Call the read_skill tool for `{skill_name}` before inspecting the change, "
                    "then follow that project skill for this task.\n\n"
                    f"{prompt}"
                )
            args.append(prompt)

            env = os.environ.copy()
            env.update({"MUSE_NO_AUTO_UPDATE": "1", "NO_COLOR": "1"})
            started = time.monotonic()
            try:
                result = subprocess.run(
                    args,
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    check=False,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                return {"error": f"Muse timed out after {timeout_seconds}s: {exc}"}
            latency_ms = round((time.monotonic() - started) * 1000)
            parsed = _parse_muse_stream(result.stdout, skill_name)
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout[-2000:]
                return {"error": f"Muse exited {result.returncode}: {detail}"}
            if not parsed["output"].strip():
                return {"error": "Muse completed without a terminal review output"}

            return {
                "output": parsed["output"],
                "latencyMs": latency_ms,
                "cached": False,
                "metadata": {
                    "runtime": "muse",
                    "variant": variant,
                    "skillName": skill_name if variant == "current" else None,
                    "skillObserved": parsed["skillObserved"],
                    "baseSha": base_sha,
                    "headSha": head_sha,
                    "museModels": parsed["models"],
                    "eventCount": parsed["eventCount"],
                    "museExitCode": result.returncode,
                    "controlMode": "project-placeholder" if variant == "none" else None,
                    "stderrTail": result.stderr[-1000:],
                },
            }
    except (OSError, ProviderError, ValueError) as exc:
        return {"error": str(exc)}
