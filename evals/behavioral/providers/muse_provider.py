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
import uuid
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

try:
    from .attempt_evidence import (
        SESSION_ID_RE,
        _linked_child_ids,
        classify_completion,
        default_sessions_root,
        locate_session_dir,
        path_scope,
        retain_attempt_evidence,
        session_dir_is_ambiguous,
        session_id_from_stream,
        session_record_gap,
        tool_lookups,
        usage_summary,
        written_file_versions,
    )
except ImportError:  # Promptfoo loads provider files outside their package.
    from attempt_evidence import (  # type: ignore[no-redef]
        SESSION_ID_RE,
        _linked_child_ids,
        classify_completion,
        default_sessions_root,
        locate_session_dir,
        path_scope,
        retain_attempt_evidence,
        session_dir_is_ambiguous,
        session_id_from_stream,
        session_record_gap,
        tool_lookups,
        usage_summary,
        written_file_versions,
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
    r"^(?:/dev/(?:null|stdin|stdout|stderr)"
    r"|/(?:usr/|usr/local/|opt/homebrew/)?bin/[A-Za-z0-9._+-]+)$"
)
_ABSOLUTE_PATH = re.compile(r"(?:^|(?<=[\s'\"=(:,;|&<>]))(/(?!/)[^\s'\"`;|&<>()]*)")
_TRAVERSAL = re.compile(r"(?:^|(?<=[\s'\"=/:(]))\.\.(?=/|$|[\s'\"):;])")
_HOME = re.compile(r"\$\{?HOME\b|(?:^|(?<=[\s'\"=:(]))~(?=/|$|[\s'\"):;])")
_CHANGE_DIR = re.compile(r"(?:^|(?<=[\s;&|(]))(?:cd|pushd)(?:\s+([^\s;&|)]+))?(?=$|[\s;&|)])")
_COMMAND_FIELD = re.compile(r'"command"\s*:\s*"((?:[^"\\]|\\.)*)')
_INDIRECT = re.compile(
    r"git\s+-C\b|--git-dir|--work-tree|GIT_DIR|GIT_WORK_TREE|GIT_ALTERNATE_OBJECT_DIRECTORIES"
    r"|objects/info/alternates|expanduser|Path\.home|os\.environ|getenv"
)


def _command_texts(stdout: str) -> list[str]:
    """Collect command strings Muse logged for tool calls and reported checks."""

    commands: list[str] = []

    def salvage(text: str) -> None:
        # Truncated, prefixed or fenced JSON still exposes its "command" strings.
        for match in _COMMAND_FIELD.finditer(text):
            try:
                commands.append(json.loads(f'"{match.group(1)}"'))
            except json.JSONDecodeError:
                commands.append(match.group(1))

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
        elif isinstance(value, str) and '"command"' in value:
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                salvage(value)
            else:
                visit(parsed)

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                visit(json.loads(line))
            except json.JSONDecodeError:
                salvage(line)
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


def _stream_events(stdout: str) -> list[dict[str, Any]]:
    """Parse the ``muse exec --json`` JSONL stream into envelope dicts."""

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
    return events


def _schedule_identity(
    options: dict[str, Any],
    context: dict[str, Any],
) -> tuple[str | None, str | None, str | None, int | None]:
    """Build the attempt schedule key ``<provider>|<case>|<repeat or null>``.

    Under real Promptfoo the Python provider receives ``options`` carrying
    ``label`` (alongside ``config``) and a ``context`` with ``vars``,
    ``test``, ``repeatIndex`` and ``testIdx`` — and no ``context["provider"]``
    label. The label therefore comes only from ``options.get("label")``; a
    missing label yields a None key (the gap is recorded in the ledger line),
    never a ``current|…``/``None`` guess from the context. The repeat index
    comes from ``context.get("repeatIndex")`` first, then the older
    metadata/vars keys. The case id comes from the test metadata or vars.
    """

    label = options.get("label") if isinstance(options, dict) else None
    if not isinstance(label, str) or not label:
        label = None
    variables = context.get("vars") if isinstance(context, dict) else None
    variables = variables if isinstance(variables, dict) else {}
    test = context.get("test") if isinstance(context, dict) else None
    test_metadata = test.get("metadata") if isinstance(test, dict) else None
    test_metadata = test_metadata if isinstance(test_metadata, dict) else {}
    case_id = test_metadata.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        case_id = variables.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        case_id = None
    repeat = None
    context_repeat = context.get("repeatIndex") if isinstance(context, dict) else None
    if isinstance(context_repeat, bool):
        context_repeat = None
    if isinstance(context_repeat, int) and context_repeat >= 0:
        repeat = context_repeat
    else:
        for source in (test_metadata, variables):
            if not isinstance(source, dict):
                continue
            for key in ("repeat_index", "repeatIndex", "repeat", "n"):
                value = source.get(key)
                if isinstance(value, bool):
                    continue
                if isinstance(value, int) and value >= 0:
                    repeat = value
                    break
            if repeat is not None:
                break
    if not label or not case_id:
        return None, label, case_id, repeat
    return f"{label}|{case_id}|{repeat if repeat is not None else 'null'}", label, case_id, repeat


def _reported_head_sha(output: str) -> str | None:
    """Return the review JSON's ``head_sha`` when parseable, else None."""

    text = output.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    value: Any = None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    reported = value.get("head_sha")
    return reported if isinstance(reported, str) and reported else None


def _head_binding(reported: str | None, expected: Any) -> str:
    """Bind the reported head to the expected head SHA.

    ``match`` when the reported value is a >=7-char hex prefix of the
    expected head, ``mismatch`` when both are present but do not bind, and
    ``missing`` when either side is absent or malformed.
    """

    if not isinstance(reported, str) or not re.fullmatch(r"[0-9a-fA-F]{7,40}", reported):
        return "missing"
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{7,40}", expected):
        return "missing"
    return "match" if expected.lower().startswith(reported.lower()) else "mismatch"


def _workspace_binding(
    events: list[dict[str, Any]], workspace: Path, head_sha: Any
) -> str:
    """Bind the stream's workspace observation to this attempt.

    Uses the last ``session.workspace_branch.observed`` record:
    ``payload.record.workspace_root`` must equal the attempt workspace and
    ``payload.record.commit`` must be a prefix of the head SHA. ``unknown``
    when no record (or no head) can verify the binding.
    """

    latest: dict[str, Any] | None = None
    for event in events:
        if event.get("payload_type") != "session.workspace_branch.observed":
            continue
        payload = event.get("payload")
        record = payload.get("record") if isinstance(payload, dict) else None
        if isinstance(record, dict):
            latest = record
    if latest is None:
        return "unknown"
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{7,40}", head_sha):
        return "unknown"
    commit = latest.get("commit")
    if latest.get("workspace_root") == str(workspace) and isinstance(commit, str) and commit and head_sha.lower().startswith(commit.lower()):
        return "match"
    return "mismatch"


def _append_ledger(trace_dir: str | None, line: dict[str, Any]) -> bool:
    """Append one JSON line to the attempt ledger (0600, O_APPEND)."""

    if not trace_dir:
        return True
    try:
        directory = Path(trace_dir)
        path = directory / "attempts.jsonl"
        data = (json.dumps(line, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
        finally:
            os.close(descriptor)
        return True
    except OSError:
        return False


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip().startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _retained_log_records(path: Path) -> tuple[list[dict[str, Any]] | None, bool]:
    """Read a retained session log without following symlinks.

    Returns ``(records_or_None, skipped_symlink)``: None with True means a
    symlink was left behind (never followed); None with False means the log
    is absent.
    """

    try:
        if path.is_symlink():
            return None, True
        if not path.is_file():
            return None, False
    except OSError:
        return None, False
    return _read_jsonl_records(path), False


def _manifest_symlink_child_ids(evidence_attempt_dir: Path) -> set[str]:
    """Recover retention-time symlinked child ids from the manifest.

    Symlinked child logs are never copied, so the retained directory alone
    cannot show they existed; the manifest's skipped entries can.
    """

    try:
        manifest = json.loads(
            (evidence_attempt_dir / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return set()
    found: set[str] = set()
    skipped = manifest.get("skipped") if isinstance(manifest, dict) else None
    if not isinstance(skipped, list):
        return found
    for entry in skipped:
        if not isinstance(entry, dict) or entry.get("type") != "symlink":
            continue
        rel = entry.get("path")
        if not isinstance(rel, str):
            continue
        parts = rel.split("/")
        if len(parts) >= 3 and parts[0] == "session" and parts[1] == "subagent":
            if SESSION_ID_RE.match(parts[2]):
                found.add(parts[2])
    return found


def _retained_attempt_records(
    evidence_attempt_dir: Path,
) -> tuple[
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    list[dict[str, str]],
    dict[str, str],
]:
    """Read parent/child session records back from the retained evidence copy.

    Never follows symlinks: a symlinked log reads as a skipped record, not
    as its target. Returns ``(parent, children, linked, child_inventory)``
    where the inventory maps every expected child id (each
    ``subagent/<id>/`` entry plus every reminder-linked id) to ``observed`` |
    ``missing-log`` | ``skipped-symlink`` | ``unusable`` (empty, unparsable
    or unreadable) | ``missing-child-dir``. Here ``observed`` means only
    that the record was retained and parsed; whether its usage is covered
    is decided by ``usage_summary`` from the per-call records, never from
    mere parseability.
    """

    parent_records, _ = _retained_log_records(
        evidence_attempt_dir / "session" / "session.jsonl"
    )
    parent = parent_records if parent_records is not None else []
    children: dict[str, list[dict[str, Any]]] = {}
    inventory: dict[str, str] = {}
    subagents = evidence_attempt_dir / "session" / "subagent"
    try:
        dir_is_link = subagents.is_symlink()
    except OSError:
        dir_is_link = False
    if not dir_is_link:
        try:
            is_dir = subagents.is_dir()
        except OSError:
            is_dir = False
        if is_dir:
            try:
                entries = sorted(subagents.iterdir(), key=lambda p: p.name)
            except OSError:
                entries = []
            for child in entries:
                log = child / "session.jsonl"
                try:
                    if child.is_symlink() or log.is_symlink():
                        inventory[child.name] = "skipped-symlink"
                        continue
                    if not child.is_dir():
                        continue
                except OSError:
                    inventory[child.name] = "unusable"
                    continue
                records, _ = _retained_log_records(log)
                if records is None:
                    inventory[child.name] = "missing-log"
                elif records:
                    inventory[child.name] = "observed"
                    children[child.name] = records
                else:
                    inventory[child.name] = "unusable"
                    children[child.name] = []
    for child_id in _manifest_symlink_child_ids(evidence_attempt_dir):
        inventory.setdefault(child_id, "skipped-symlink")
    linked: list[dict[str, str]] = []
    # Child ids reach the filesystem below; only session UUIDs pass, so a
    # traversal id can never escape the evidence directory.
    reminder_ids, _ = _linked_child_ids(parent)
    reminders = evidence_attempt_dir / "session" / "reminder"
    for child_id in sorted(reminder_ids):
        child_dir = reminders / child_id
        logs: list[dict[str, Any]] = []
        try:
            dir_link = child_dir.is_symlink()
        except OSError:
            dir_link = False
        if not dir_link:
            try:
                is_dir = child_dir.is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                try:
                    log_files = sorted(child_dir.iterdir(), key=lambda p: p.name)
                except OSError:
                    log_files = []
                for log in log_files:
                    if log.suffix != ".jsonl":
                        continue
                    records, _ = _retained_log_records(log)
                    if records:
                        logs.extend(records)
        linked.append({"id": child_id, "kind": "reminder"})
        if logs and not children.get(child_id):
            # A linked child without a retained log stays absent here so the
            # usage summary reports it as uncovered, never as zero usage.
            children[child_id] = logs
    for child_id in sorted(reminder_ids):
        if child_id in inventory:
            continue
        if children.get(child_id):
            inventory[child_id] = "observed"
        else:
            inventory[child_id] = "missing-child-dir"
    for child_id, records in children.items():
        if records and inventory.get(child_id) != "skipped-symlink":
            # Parseable records win: a log that yielded records is observed
            # even when another source looked empty.
            inventory[child_id] = "observed"
    return parent, children, linked, inventory


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

    # Post-launch state for the finished-ledger guarantee: every provider exit
    # path after the attempt launches writes the finished ledger line, even
    # when retention or accounting itself raises.
    attempt_id: str | None = None
    schedule_key: str | None = None
    provider_label: str | None = None
    ledger_case_id: str | None = None
    ledger_repeat: int | None = None
    finished_written = False

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
            # Per-attempt scratch beside the checkout. A relocated TMPDIR is
            # not preventive isolation (#24 owns proof), so this is recorded
            # as per-attempt-tmpdir with no isolation proof.
            scratch = Path(temp) / "scratch"
            scratch.mkdir(mode=0o700, exist_ok=True)
            try:
                os.chmod(scratch, 0o700)
            except OSError:
                pass
            env["TMPDIR"] = env["TMP"] = env["TEMP"] = str(scratch)
            attempt_id = uuid.uuid4().hex
            schedule_key, provider_label, ledger_case_id, ledger_repeat = _schedule_identity(
                options, context
            )
            started_gaps = [] if schedule_key else ["schedule-key-missing"]
            ledger_ok = _append_ledger(
                trace_dir,
                {
                    "phase": "started",
                    "attemptId": attempt_id,
                    "scheduleKey": schedule_key,
                    "providerLabel": provider_label,
                    "case_id": ledger_case_id,
                    "repeatIndex": ledger_repeat,
                    "gaps": started_gaps,
                    "startedAt": time.time(),
                },
            )
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
            events = _stream_events(stdout)
            completion = classify_completion(
                events, None if timed_out else result.returncode, timed_out
            )
            output = completion.get("output") if completion["status"] == "completed" else ""
            output = output if isinstance(output, str) else ""
            muse_session_id = session_id_from_stream(events)
            reported_head = _reported_head_sha(output)
            head_binding = _head_binding(reported_head, variables.get("head_sha"))
            workspace_binding = _workspace_binding(events, workspace, variables.get("head_sha"))
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
                "attemptId": attempt_id,
                "scheduleKey": schedule_key,
                "museSessionId": muse_session_id,
                "museRunId": completion.get("runId"),
                "completionStatus": completion["status"],
                "terminalReason": completion.get("terminalReason"),
                "reviewedHeadReported": reported_head,
                "headBinding": head_binding,
                "sessionWorkspaceBinding": workspace_binding,
                "scratchIsolation": "per-attempt-tmpdir",
                "scratchIsolationProof": "none",
                **_retain_trace(trace_dir, context, candidate_id, stdout, stderr),
            }

            if trace_dir:
                # Retain attempt evidence after Muse exits (timeout included)
                # and before the temporary directory is removed.
                evidence_root = Path(trace_dir) / "evidence"
                try:
                    evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
                    os.chmod(evidence_root, 0o700)
                except OSError:
                    pass
                sessions_root = default_sessions_root(config)
                session_dir = (
                    locate_session_dir(sessions_root, muse_session_id)
                    if muse_session_id
                    else None
                )
                retention = retain_attempt_evidence(
                    evidence_root, attempt_id, session_dir, scratch, None
                )
                gaps = list(retention["evidenceGaps"])
                if session_dir is None and (
                    session_record_gap(events) == "session-record-ambiguous"
                    or (
                        muse_session_id is not None
                        and session_dir_is_ambiguous(sessions_root, muse_session_id)
                    )
                ):
                    gaps = [
                        "session-record-ambiguous" if gap == "session-record-missing" else gap
                        for gap in gaps
                    ]
                evidence_status = retention["evidenceStatus"]
                metadata["evidenceStatus"] = evidence_status
                metadata["evidenceGaps"] = gaps
                metadata["evidenceManifestSha256"] = retention["evidenceManifestSha256"]
                attempt_evidence_dir = evidence_root / attempt_id
                if evidence_status in {"retained", "partial"}:
                    (
                        retained_parent,
                        retained_children,
                        retained_linked,
                        retained_inventory,
                    ) = _retained_attempt_records(attempt_evidence_dir)
                else:
                    retained_parent, retained_children, retained_linked = [], {}, []
                    retained_inventory = {}
                tagged_records = [{"session": "parent", "record": record} for record in retained_parent]
                tagged_records.extend(
                    {"session": child_id, "record": record}
                    for child_id, logs in retained_children.items()
                    for record in logs
                )
                written_files = (
                    written_file_versions(tagged_records)
                    if evidence_status in {"retained", "partial"}
                    else []
                )
                lookups = tool_lookups(stdout, workspace, tagged_records, scratch)
                usage = usage_summary(
                    retained_parent,
                    retained_children,
                    retained_linked,
                    retained_inventory,
                )
                if evidence_status != "retained" and usage.get("coverage") == "complete":
                    # Usage read from incomplete evidence is not covered usage:
                    # a subagent log that could not be copied (or any other
                    # retention gap) forces partial coverage, so tokenUsage is
                    # omitted and the candidate status is partial, never zeros.
                    total = usage.get("total")
                    usage = {
                        **usage,
                        "total": None,
                        "partialTotal": dict(total) if isinstance(total, dict) else total,
                        "uncoveredChildren": list(usage.get("uncoveredChildren") or []),
                        "coverage": "partial",
                    }
                metadata["writtenFiles"] = written_files
                metadata["toolLookups"] = lookups
                metadata["museUsage"] = usage
                audit_flags: list[str] = []
                if any(
                    isinstance(entry.get("path"), str)
                    and path_scope(entry["path"], workspace, scratch) == "outside"
                    for entry in written_files
                ):
                    audit_flags.append("write-outside-attempt-scratch")
                for lookup in lookups:
                    for flag in lookup.get("flags", []):
                        if flag not in audit_flags:
                            audit_flags.append(flag)
                if evidence_status != "retained":
                    # Missing or partial evidence cannot be audited: quarantine
                    # the row, never approve it.
                    audit_flags.append("evidence-unavailable")
                if checked and audit_flags:
                    metadata["graderBoundaryFlags"] = sorted(
                        set(metadata["graderBoundaryFlags"]) | set(audit_flags)
                    )
                # A complete claim requires established per-call usage for the
                # parent and every child (usage_summary enforces this; a
                # merely parseable record never counts). Anything else keeps
                # tokenUsage absent and the candidate status below observed.
                if usage.get("coverage") == "complete" and isinstance(usage.get("total"), dict):
                    total = usage["total"]
                    prompt_tokens = int(total.get("input_tokens", 0))
                    completion_tokens = int(total.get("output_tokens", 0))
                    compat = {
                        "prompt": prompt_tokens,
                        "completion": completion_tokens,
                        "total": prompt_tokens + completion_tokens,
                    }
                    metadata["museTokenUsage"] = compat
                    metadata["candidateTokenStatus"] = "observed"
                    metadata["_compatTokenUsage"] = compat
                else:
                    metadata.pop("museTokenUsage", None)
                    metadata["candidateTokenStatus"] = (
                        "partial" if usage.get("coverage") == "partial" else "unavailable"
                    )
                    metadata.pop("_compatTokenUsage", None)

            def failed(message: str) -> dict[str, Any]:
                nonlocal finished_written
                finished_written = True
                metadata.pop("_compatTokenUsage", None)
                ledger_ok = _append_ledger(
                    trace_dir,
                    {
                        "phase": "finished",
                        "attemptId": attempt_id,
                        "scheduleKey": schedule_key,
                        "providerLabel": provider_label,
                        "case_id": ledger_case_id,
                        "repeatIndex": ledger_repeat,
                        "completionStatus": completion["status"],
                        "flags": list(metadata.get("graderBoundaryFlags") or []),
                        "evidenceStatus": metadata.get("evidenceStatus"),
                        "error": message,
                        "finishedAt": time.time(),
                    },
                )
                # A ledger write failure quarantines the row but never discards
                # a finished review. An earlier started-write failure is never
                # overwritten back to ok, and a disabled ledger stays
                # not-enabled rather than ok.
                if metadata.get("ledgerStatus") != "failed":
                    if not trace_dir:
                        metadata["ledgerStatus"] = "not-enabled"
                    else:
                        metadata["ledgerStatus"] = "ok" if ledger_ok else "failed"
                if not ledger_ok and checked and "ledger-unavailable" not in metadata["graderBoundaryFlags"]:
                    metadata["graderBoundaryFlags"] = sorted(
                        [*metadata["graderBoundaryFlags"], "ledger-unavailable"]
                    )
                return {"error": message, "latencyMs": latency_ms, "metadata": metadata}

            if not ledger_ok:
                metadata["ledgerStatus"] = "failed"
                if checked:
                    metadata["graderBoundaryFlags"] = sorted(
                        set(metadata["graderBoundaryFlags"]) | {"ledger-unavailable"}
                    )
            else:
                metadata["ledgerStatus"] = "ok" if trace_dir else "not-enabled"

            if timed_out:
                return failed(f"Muse timed out after {timeout_seconds}s")
            if result.returncode != 0:
                detail = stderr.strip() or stdout[-2000:]
                return failed(f"Muse exited {result.returncode}: {detail}")
            if completion["status"] != "completed":
                reason = completion.get("terminalReason")
                suffix = f": {reason}" if reason else ""
                return failed(
                    f"Muse run did not complete (status={completion['status']}{suffix})"
                )
            if spike_run and variant in {"current", "candidate"} and not parsed["skillObserved"]:
                return failed("Muse completed without observing the delivered project skill")
            if spike_run and not parsed["models"]:
                return failed("Muse completed without observed model telemetry")
            if spike_run and parsed["models"] != [configured_model]:
                return failed(
                    f"Muse model telemetry mismatch: expected {configured_model}, "
                    f"observed {parsed['models']}"
                )
            if head_binding != "match":
                return failed(
                    f"stale/mismatched review: reported head {reported_head!r} "
                    f"does not bind to expected head {variables.get('head_sha')!r}"
                )
            if workspace_binding == "mismatch":
                return failed("stale/mismatched review: session workspace does not bind to this attempt")

            finished_written = True
            finished_ok = _append_ledger(
                trace_dir,
                {
                    "phase": "finished",
                    "attemptId": attempt_id,
                    "scheduleKey": schedule_key,
                    "providerLabel": provider_label,
                    "case_id": ledger_case_id,
                    "repeatIndex": ledger_repeat,
                    "completionStatus": completion["status"],
                    "flags": list(metadata.get("graderBoundaryFlags") or []),
                    "evidenceStatus": metadata.get("evidenceStatus"),
                    "error": None,
                    "finishedAt": time.time(),
                },
            )
            if not finished_ok:
                metadata["ledgerStatus"] = "failed"
                if checked and "ledger-unavailable" not in metadata["graderBoundaryFlags"]:
                    metadata["graderBoundaryFlags"] = sorted(
                        [*metadata["graderBoundaryFlags"], "ledger-unavailable"]
                    )
            response = {
                "output": output,
                "latencyMs": latency_ms,
                "cached": False,
                "metadata": metadata,
            }
            compat_usage = metadata.pop("_compatTokenUsage", None)
            if trace_dir:
                if compat_usage is not None:
                    response["tokenUsage"] = compat_usage
            elif parsed["usage"]:
                response["tokenUsage"] = parsed["usage"]
            return response
    except (OSError, ProviderError, ValueError) as exc:
        message = str(exc)
        if attempt_id is not None and not finished_written:
            # The attempt launched: the finished line must still land, with
            # whatever metadata was built, so the slot never goes silent.
            try:
                prior_metadata = metadata
            except UnboundLocalError:
                prior_metadata = {}
            if not isinstance(prior_metadata, dict):
                prior_metadata = {}
            try:
                prior_completion = completion
            except UnboundLocalError:
                prior_completion = {}
            status = prior_completion.get("status") if isinstance(prior_completion, dict) else None
            finished_written = True
            try:
                wrote = _append_ledger(
                    trace_dir,
                    {
                        "phase": "finished",
                        "attemptId": attempt_id,
                        "scheduleKey": schedule_key,
                        "providerLabel": provider_label,
                        "case_id": ledger_case_id,
                        "repeatIndex": ledger_repeat,
                        "completionStatus": status,
                        "flags": list(prior_metadata.get("graderBoundaryFlags") or []),
                        "evidenceStatus": prior_metadata.get("evidenceStatus"),
                        "error": message,
                        "finishedAt": time.time(),
                    },
                )
            except OSError:
                wrote = False
            if not trace_dir:
                ledger_status = "not-enabled"
            else:
                ledger_status = "ok" if wrote else "failed"
            if prior_metadata.get("ledgerStatus") == "failed":
                ledger_status = "failed"
            metadata_out = dict(prior_metadata)
            metadata_out.setdefault("attemptId", attempt_id)
            metadata_out.setdefault("scheduleKey", schedule_key)
            metadata_out["ledgerStatus"] = ledger_status
            return {"error": message, "metadata": metadata_out}
        return {"error": message}
