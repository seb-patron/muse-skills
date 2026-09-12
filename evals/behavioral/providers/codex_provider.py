"""Promptfoo provider that runs a Codex reference agent on a historical Git head."""

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
        repo_root,
        validate_skill_name,
    )
except ImportError:  # Promptfoo loads provider files outside their package.
    from workspace import (  # type: ignore[no-redef]
        ProviderError,
        prepare_historical_workspace,
        repo_root,
        validate_skill_name,
    )


MODELS = {"gpt-5.6-luna", "gpt-5.6-sol"}
VARIANTS = {"none", "current"}


def _text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _parse_codex_stream(stdout: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    messages: list[str] = []
    usage: dict[str, int] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            messages.append(item["text"])
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = {
                key: int(value)
                for key, value in event["usage"].items()
                if isinstance(key, str) and isinstance(value, int)
            }
    return {"output": messages[-1] if messages else "", "events": events, "usage": usage}


def _timeout_detail(stdout: str | bytes | None) -> str:
    parsed = _parse_codex_stream(_text(stdout))
    recent: list[str] = []
    for event in parsed["events"]:
        item = event.get("item")
        if event.get("type") != "item.completed" or not isinstance(item, dict):
            continue
        summary = str(item.get("type", "unknown"))
        command = item.get("command")
        if isinstance(command, str) and command:
            summary += f":{command[:120]}"
        recent.append(summary)
    tail = ", ".join(recent[-3:]) or "none"
    return f"partialEvents={len(parsed['events'])}; recentCompletedItems={tail}"


def _candidate_prompt(repo: Path, skill_name: str, variant: str, prompt: str) -> tuple[str, str]:
    validate_skill_name(skill_name)
    if variant not in VARIANTS:
        raise ProviderError(f"unknown variant {variant!r}; expected one of {sorted(VARIANTS)}")
    if variant == "none":
        prefix = (
            "This is the no-skill Codex reference condition. Do not read or use files under "
            "`skills/` or `.agents/skills/`; review using your normal capabilities only."
        )
        return f"{prefix}\n\n{prompt}", "withheld"

    source = repo / "skills" / skill_name / "SKILL.md"
    if not source.is_file():
        raise ProviderError(f"current skill does not exist: {source}")
    body = source.read_text(encoding="utf-8")
    prefix = (
        "This is the skill-content Codex reference condition. Follow the candidate Muse skill "
        "below as review instructions. Do not read other skill files from the repository.\n\n"
        f"<candidate-muse-skill name=\"{skill_name}\">\n{body}\n</candidate-muse-skill>"
    )
    return f"{prefix}\n\n{prompt}", "prompt-injected"


def _command(
    codex_binary: str,
    workspace: Path,
    model: str,
    reasoning_effort: str,
    prompt: str,
) -> list[str]:
    if model not in MODELS:
        raise ProviderError(f"unsupported Codex reference model {model!r}; expected one of {sorted(MODELS)}")
    if reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ProviderError(f"unsupported reasoning effort {reasoning_effort!r}")
    return [
        codex_binary,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(workspace),
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--config",
        "sandbox_workspace_write.network_access=false",
        "--config",
        'web_search="disabled"',
        "--color",
        "never",
        "--json",
        prompt,
    ]


def _run_codex(
    args: list[str], workspace: Path, timeout_seconds: int, env: dict[str, str]
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=workspace,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
        env=env,
    )


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    config = options.get("config") or {}
    variables = context.get("vars") or {}
    variant = str(config.get("variant", "none"))
    skill_name = str(variables.get("skill_name", ""))
    model = str(config.get("model", ""))
    reasoning_effort = str(config.get("reasoning_effort", "high"))
    codex_binary = str(config.get("codex_binary") or os.environ.get("CODEX_EVAL_BINARY") or "codex")
    timeout_seconds = int(config.get("timeout_seconds", 540))

    try:
        source_repo = repo_root(options)
        if not (source_repo / ".git").exists():
            raise ProviderError(f"repository root is not a Git checkout: {source_repo}")
        if shutil.which(codex_binary) is None and not Path(codex_binary).is_file():
            raise ProviderError(f"Codex executable not found: {codex_binary}")

        with tempfile.TemporaryDirectory(prefix="codex-skill-eval-") as temp:
            workspace = Path(temp) / "repo"
            base_sha, head_sha = prepare_historical_workspace(
                source_repo,
                workspace,
                str(variables.get("base_sha", "")),
                str(variables.get("head_sha", "")),
            )
            candidate_prompt, delivery = _candidate_prompt(source_repo, skill_name, variant, prompt)
            args = _command(codex_binary, workspace, model, reasoning_effort, candidate_prompt)
            env = os.environ.copy()
            env.update({"NO_COLOR": "1"})

            started = time.monotonic()
            try:
                result = _run_codex(args, workspace, timeout_seconds, env)
            except subprocess.TimeoutExpired as exc:
                detail = _timeout_detail(exc.stdout)
                return {"error": f"Codex {model} timed out after {timeout_seconds}s; {detail}"}
            latency_ms = round((time.monotonic() - started) * 1000)
            parsed = _parse_codex_stream(result.stdout)
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout[-2000:]
                return {"error": f"Codex {model} exited {result.returncode}: {detail}"}
            if not parsed["output"].strip():
                return {"error": f"Codex {model} completed without a final review output"}

            usage = parsed["usage"]
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            return {
                "output": parsed["output"],
                "latencyMs": latency_ms,
                "cached": False,
                "tokenUsage": {
                    "total": input_tokens + output_tokens,
                    "prompt": input_tokens,
                    "completion": output_tokens,
                    "cached": int(usage.get("cached_input_tokens", 0)),
                },
                "metadata": {
                    "runtime": "codex-reference",
                    "variant": variant,
                    "skillName": skill_name if variant == "current" else None,
                    "skillDelivery": delivery,
                    "baseSha": base_sha,
                    "headSha": head_sha,
                    "codexModel": model,
                    "reasoningEffort": reasoning_effort,
                    "eventCount": len(parsed["events"]),
                    "codexExitCode": result.returncode,
                    "codexUsage": usage,
                    "stderrTail": result.stderr[-1000:],
                },
            }
    except (OSError, ProviderError, UnicodeError, ValueError) as exc:
        return {"error": str(exc)}
