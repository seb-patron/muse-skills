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
    "evidence-claims-v3": Path("evals/behavioral/candidates/evidence-claims-v3.md"),
    "minimal": Path("evals/behavioral/candidates/minimal.md"),
    "risk-first": Path("evals/behavioral/candidates/risk-first.md"),
    "upstream-adapted": Path("evals/behavioral/candidates/upstream-adapted.md"),
}
# The disposable checkout lives under the eval repository (see call_api), so a
# shell command can still reach grader-only material by path. These names never
# occur in the external case sources, the review prompt, or the candidate skills.
# Historical muse-skills heads do contain them, so the check skips those cases.
GRADER_ONLY_MARKERS = (
    "gold_findings",
    "resolved_findings",
    "development-v2-cases",
    "development-v3-cases",
    "miss-diagnosis",
    "development-v3-calibration",
    "evidence-claims-v3-screen",
)
WORKSPACE_PREFIX = ".muse-skill-eval-"
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


# Narrow runtime exceptions for absolute paths in reviewer commands. Anything
# else outside the checkout is flagged for audit; this is a heuristic, not a
# shell parser, and a clean scan only means no exposure was observed.
ALLOWED_COMMAND_PATHS = re.compile(
    r"^(?:/dev/(?:null|stdin|stdout|stderr)|/(?:usr/)?bin/[A-Za-z0-9._+-]+)$"
)
_ABSOLUTE_PATH = re.compile(r"(?:^|(?<=[\s'\"=(:,;|&<>]))(/(?!/)[^\s'\"`;|&<>()]*)")
_TRAVERSAL = re.compile(r"(?:^|(?<=[\s'\"=/:(]))\.\.(?=/|$|[\s'\"):;])")
_HOME = re.compile(r"\$\{?HOME\b|(?:^|(?<=[\s'\"=:(]))~(?=/|$|[\s'\"):;])")
_CHANGE_DIR = re.compile(r"(?:^|(?<=[\s;&|(]))(?:cd|pushd)(?:\s+([^\s;&|)]+))?(?=$|[\s;&|)])")
_INDIRECT = re.compile(
    r"git\s+-C\b|--git-dir|--work-tree|GIT_DIR|GIT_WORK_TREE|GIT_ALTERNATE_OBJECT_DIRECTORIES"
    r"|objects/info/alternates|expanduser|Path\.home|os\.environ|getenv"
)


def _command_texts(stdout: str) -> list[str]:
    """Collect command strings Muse logged for tool calls and reported checks."""

    commands: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "command" and isinstance(item, str):
                    commands.append(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and value.lstrip().startswith("{") and '"command"' in value:
            try:
                visit(json.loads(value))
            except json.JSONDecodeError:
                pass

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                visit(json.loads(line))
            except json.JSONDecodeError:
                continue
    return commands


def _command_flags(command: str, workspace: Path) -> set[str]:
    text = command.replace(str(workspace), ".")
    flags: set[str] = set()
    if any(not ALLOWED_COMMAND_PATHS.match(path) for path in _ABSOLUTE_PATH.findall(text)):
        flags.add("command-absolute-path")
    if _TRAVERSAL.search(text):
        flags.add("command-traversal")
    if _HOME.search(text):
        flags.add("command-home-reference")
    for match in _CHANGE_DIR.finditer(text):
        target = match.group(1)
        if not target or target[0] in "/~-$" or ".." in target:
            flags.add("command-directory-change")
    if _INDIRECT.search(text):
        flags.add("command-indirect-access")
    return flags


def _grader_boundary_breaches(stdout: str, repo_root: Path, workspace: Path) -> list[str]:
    """Name grader-only markers, out-of-checkout paths, or risky commands in a trace."""

    breaches = {marker for marker in GRADER_ONLY_MARKERS if marker in stdout}
    # The checkout's own paths are expected; any other child of the eval
    # repository or of the checkout's parent directory is not.
    remainder = stdout.replace(str(workspace), "")
    if f"{repo_root}/" in remainder:
        breaches.add("eval-repository-path")
    parent = workspace.parent.parent
    if parent != repo_root and f"{parent}/" in remainder:
        breaches.add("workspace-parent-path")
    for command in _command_texts(stdout):
        breaches |= _command_flags(command, workspace)
    return sorted(breaches)


def _workspace_parent(config: dict[str, Any], repo_root: Path) -> Path:
    """Return where disposable checkouts go; outside the eval repo when configured."""

    configured = config.get("workspace_parent") or os.environ.get("MUSE_EVAL_WORKSPACE_PARENT")
    if not configured:
        return repo_root
    parent = Path(str(configured)).expanduser().resolve()
    if not parent.is_dir():
        raise ProviderError(f"workspace parent does not exist: {parent}")
    if parent == repo_root or repo_root in parent.parents or parent in repo_root.parents:
        raise ProviderError("workspace parent must not contain or sit inside the eval repository")
    return parent


def _retain_trace(
    trace_dir: str | None,
    context: dict[str, Any],
    candidate_id: str | None,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    """Keep one attempt's raw output privately; return its private locator and hashes."""

    if not trace_dir:
        return {"traceStatus": "not-retained"}
    directory = Path(trace_dir)
    if not directory.is_dir():
        return {"traceStatus": "failed", "traceError": "trace directory does not exist"}
    case_id = str((context.get("test") or {}).get("metadata", {}).get("case_id") or "case")
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", f"{case_id}__{candidate_id or 'none'}__{time.time_ns()}")
    record: dict[str, Any] = {"traceStatus": "retained"}
    for suffix, text in (("stdout.jsonl", stdout), ("stderr.txt", stderr)):
        data = text.encode("utf-8", errors="replace")
        key = "traceStdout" if suffix.startswith("stdout") else "traceStderr"
        # Hashes are recorded even if the write fails, so a finished review is
        # never discarded because its private copy could not be kept.
        record[f"{key}Sha256"] = hashlib.sha256(data).hexdigest()
        try:
            path = directory / f"{stem}.{suffix}"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            record[f"{key}File"] = path.name
        except OSError as exc:
            record["traceStatus"] = "failed"
            record["traceError"] = f"{type(exc).__name__}: {exc.strerror or exc}"
    return record


SCRUBBED_ENV_PREFIXES = ("MUSE_EVAL_", "SPIKE_", "PROMPTFOO_", "npm_", "VIRTUAL_ENV")
SCRUBBED_ENV_NAMES = {"INIT_CWD", "PWD", "OLDPWD"}


def _review_env(repo_root: Path) -> dict[str, str]:
    """Environment for Muse without paths or names that point at the eval harness."""

    env = {
        name: value
        for name, value in os.environ.items()
        if name not in SCRUBBED_ENV_NAMES and not name.startswith(SCRUBBED_ENV_PREFIXES)
    }
    if "PATH" in env:
        root = str(repo_root)
        env["PATH"] = os.pathsep.join(
            entry for entry in env["PATH"].split(os.pathsep)
            if entry and entry != root and not entry.startswith(f"{root}/")
        )
    env.update({"MUSE_NO_AUTO_UPDATE": "1", "NO_COLOR": "1"})
    return env


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


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
    trace_dir = os.environ.get("MUSE_EVAL_TRACE_DIR")

    try:
        repo_root = _repo_root(options)
        if not (repo_root / ".git").exists():
            raise ProviderError(f"repository root is not a Git checkout: {repo_root}")
        if shutil.which(muse_binary) is None and not Path(muse_binary).is_file():
            raise ProviderError(f"Muse executable not found: {muse_binary}")
        # Resolve now: the review environment drops eval-repository PATH entries.
        muse_binary = shutil.which(muse_binary) or muse_binary

        # Muse's session lease is workspace-root scoped and has rejected disposable
        # clones in some locations, so by default the clone sits under the eval
        # repository. A configured workspace parent keeps it away from grader-only
        # files instead.
        parent = _workspace_parent(config, repo_root)
        with tempfile.TemporaryDirectory(prefix=WORKSPACE_PREFIX, dir=parent) as temp:
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

            env = _review_env(repo_root)
            started = time.monotonic()
            timed_out = False
            try:
                result = _run_muse(args, workspace, timeout_seconds, env)
                stdout, stderr = result.stdout, result.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                result = None
                stdout, stderr = _text(exc.output), _text(exc.stderr)
            latency_ms = round((time.monotonic() - started) * 1000)
            parsed = _parse_muse_stream(stdout, skill_name)
            external_source = variables.get("source_repository") in REMOTE_CASE_SOURCES
            checked = spike_run and external_source
            metadata = {
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
                "museExitCode": None if timed_out else result.returncode,
                "termination": "timeout" if timed_out else "exited",
                "durationMs": latency_ms,
                "workspaceOutsideEvalRepo": parent != repo_root,
                # A flagged row is quarantined for inspection, never silently scored.
                "graderBoundaryChecked": checked,
                "graderBoundaryFlags": (
                    _grader_boundary_breaches(stdout, repo_root, workspace) if checked else []
                ),
                "controlMode": "project-placeholder" if variant == "none" else None,
                "stderrTail": stderr[-1000:],
                **_retain_trace(trace_dir, context, candidate_id, stdout, stderr),
            }

            def failed(message: str) -> dict[str, Any]:
                return {"error": message, "latencyMs": latency_ms, "metadata": metadata}

            if timed_out:
                return failed(f"Muse timed out after {timeout_seconds}s")
            if result.returncode != 0:
                detail = stderr.strip() or stdout[-2000:]
                return failed(f"Muse exited {result.returncode}: {detail}")
            if not parsed["output"].strip():
                return failed("Muse completed without a terminal review output")
            if spike_run and variant in {"current", "candidate"} and not parsed["skillObserved"]:
                return failed("Muse completed without observing the delivered project skill")
            if spike_run and not parsed["models"]:
                return failed("Muse completed without observed model telemetry")
            if spike_run and parsed["models"] != [configured_model]:
                return failed(
                    f"Muse model telemetry mismatch: expected {configured_model}, "
                    f"observed {parsed['models']}"
                )

            response = {
                "output": parsed["output"],
                "latencyMs": latency_ms,
                "cached": False,
                "metadata": metadata,
            }
            if parsed["usage"]:
                response["tokenUsage"] = parsed["usage"]
            return response
    except (OSError, ProviderError, ValueError) as exc:
        return {"error": str(exc)}
