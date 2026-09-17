"""Per-attempt evidence helpers for Muse review batches.

The next Muse review batch must stay auditable without relying on incomplete
traces or misleading totals. This module keeps what the reviewer wrote (its
``muse exec --json`` stream plus the retained session record), gives each
attempt private scratch, exposes tool lookups, reads real per-call usage
without double counting, refuses incomplete or mismatched output, and accounts
for every scheduled attempt.

All helpers are pure (filesystem reads only where noted) and operate on
synthetic or retained records; they never call a model. Conventions:

- ``session.jsonl`` lines are parsed JSON objects. Most carry
  ``payload_type == "runtime.session"`` with
  ``payload == {"kind", "run_id", "task_id?", "event": {...}}``. Lines with no
  ``payload_type`` (retention markers) are tolerated.
- The ``--json`` stream is JSONL with envelope keys ``stream``,
  ``payload_type`` and ``payload`` (see ``classify_completion``).
- A clean tool-lookup scan only means no exposure was observed: the pattern
  list in ``tool_lookups`` is a narrow heuristic, not a shell parser.
- Missing usage records are ``unknown``, never zero. No dollar estimate is
  ever produced.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping

SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
ATTEMPT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

DEFAULT_MAX_FILES = 2000
DEFAULT_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB

USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "reasoning_tokens",
)

# Prefixes whose binaries are treated as ordinary system tools. Anything else
# an executed or resolved path points at is outside the review boundary.
ALLOWED_SYSTEM_PREFIXES = (
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/Library/Developer/CommandLineTools/usr/bin",
)

_LOOKUP_COMMANDS = ("which", "command", "type", "whereis")


def _sessions_root_default() -> Path:
    return Path.home() / ".local" / "share" / "muse" / "sessions"


def default_sessions_root(config: Mapping[str, Any] | None = None) -> Path:
    """Resolve the session-record root for the #24 interface.

    Precedence: provider config ``muse_sessions_root``, else env
    ``MUSE_EVAL_SESSIONS_ROOT``, else ``~/.local/share/muse/sessions``.
    """

    if config:
        configured = config.get("muse_sessions_root")
        if configured:
            return Path(str(configured)).expanduser()
    env_root = os.environ.get("MUSE_EVAL_SESSIONS_ROOT")
    if env_root:
        return Path(env_root).expanduser()
    return _sessions_root_default()


def _stream_session_ids(events: Iterable[Any]) -> list[str]:
    ids: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        stream = event.get("stream")
        if not isinstance(stream, Mapping):
            continue
        if stream.get("kind") != "session":
            continue
        value = stream.get("id")
        if isinstance(value, str) and value:
            ids.append(value)
    return ids


def session_id_from_stream(events: Iterable[Any]) -> str | None:
    """Return the stream's session UUID, or None unless exactly one exists.

    The Muse ``--json`` envelope carries
    ``stream == {"kind": "session", "id": <uuid>}``. The id is returned only
    when every envelope agrees on a single distinct value and that value
    matches the UUID shape (no traversal).
    """

    distinct = set(_stream_session_ids(events))
    if len(distinct) != 1:
        return None
    (only,) = distinct
    if not SESSION_ID_RE.match(only):
        return None
    return only


def session_record_gap(events: Iterable[Any]) -> str | None:
    """Name the session-record gap for a stream, or None when it has one id.

    Returns ``"session-record-ambiguous"`` when the stream carries more than
    one distinct session id, ``"session-record-missing"`` when it carries none
    (or only an invalid one), and None when ``session_id_from_stream`` would
    succeed. The provider uses this to upgrade the retention gap.
    """

    distinct = set(_stream_session_ids(events))
    if len(distinct) == 1:
        (only,) = distinct
        return None if SESSION_ID_RE.match(only) else "session-record-missing"
    return "session-record-ambiguous" if len(distinct) > 1 else "session-record-missing"


def locate_session_dir(
    sessions_root: str | Path, session_id: str | None
) -> Path | None:
    """Locate ``<sessions_root>/<YYYY>/<MM>/<DD>/<session_id>/``.

    Matches exactly one directory via ``sessions_root.glob("*/*/*/<id>")``,
    resolves it and confirms it stays inside ``sessions_root``. Returns None
    for a missing, ambiguous, invalid or escaping id.
    """

    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        return None
    root = Path(sessions_root).expanduser()
    try:
        matches = [p for p in root.glob(f"*/*/*/{session_id}") if p.is_dir()]
    except OSError:
        return None
    if len(matches) != 1:
        return None
    try:
        resolved = matches[0].resolve()
        root_resolved = root.resolve()
    except OSError:
        return None
    if resolved != root_resolved and root_resolved not in resolved.parents:
        return None
    # The glob already pins the leaf name, but re-check it after resolution so
    # a symlink can never smuggle a different directory in.
    if resolved.name != session_id:
        return None
    return resolved


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except OSError:
        return None


def _is_regular(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode)


def _read_link_text(path: Path) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return ""


def _reminder_child_log_paths(
    session_dir: Path, sessions_root: Path, records: list[dict[str, Any]]
) -> dict[str, Path]:
    """Map reminder child id to its log file when it exists inside the root."""

    found: dict[str, Path] = {}
    try:
        root_resolved = sessions_root.resolve()
    except OSError:
        return found
    for item in records:
        event = _session_event(item)
        if not isinstance(event, Mapping) or event.get("kind") != "memory_reminder_child_session_linked":
            continue
        child_id = event.get("child_session_id")
        raw_path = event.get("child_session_log_path")
        if not isinstance(child_id, str) or not child_id:
            continue
        if not isinstance(raw_path, str) or not raw_path:
            continue
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = sessions_root / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved != root_resolved and root_resolved not in resolved.parents:
            continue
        if resolved.is_file():
            found[child_id] = resolved
    return found


def _session_event(record: Any) -> Any:
    if not isinstance(record, Mapping):
        return None
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        return None
    return payload.get("event")


def _iter_session_files(session_dir: Path) -> list[tuple[Path, str]]:
    """List retainable session files as (absolute path, relative dest path)."""

    planned: list[tuple[Path, str]] = []
    main_log = session_dir / "session.jsonl"
    if main_log.is_file() and not main_log.is_symlink():
        planned.append((main_log, "session/session.jsonl"))
    subagents = session_dir / "subagent"
    if subagents.is_dir() and not subagents.is_symlink():
        for child in sorted(subagents.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or child.is_symlink():
                continue
            log = child / "session.jsonl"
            if log.is_file() and not log.is_symlink():
                planned.append((log, f"session/subagent/{child.name}/session.jsonl"))
    outputs = session_dir / "tool-outputs"
    if outputs.is_dir() and not outputs.is_symlink():
        stack = sorted(outputs.iterdir(), key=lambda p: p.name)
        while stack:
            current = stack.pop(0)
            try:
                rel = current.relative_to(outputs)
            except ValueError:
                continue
            if current.is_symlink():
                continue
            if current.is_dir():
                if rel.parts and rel.parts[0] == ".spool":
                    continue
                stack.extend(sorted(current.iterdir(), key=lambda p: p.name))
                continue
            name = current.name
            if name.endswith(".lock") or name.endswith(".db") or name.startswith("cli-"):
                continue
            if rel.parts and rel.parts[0] == ".spool":
                continue
            planned.append((current, f"session/tool-outputs/{rel.as_posix()}"))
    return planned


def retain_attempt_evidence(
    evidence_dir: str | Path,
    attempt_id: str,
    session_dir: str | Path | None,
    scratch_dir: str | Path | None,
    limits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy one attempt's evidence into ``evidence_dir/<attempt_id>/``.

    Copies, before any cleanup, the retained session record
    (``session/session.jsonl``, per-child ``session/subagent/<id>/…``,
    ``session/tool-outputs/**`` regular files minus ``.spool/``, plus
    referenced reminder child logs inside the sessions root) and the attempt
    scratch tree (``scratch/**`` regular files; symlinks are recorded with
    their link text and never followed). Directories are created 0700, files
    written 0600 with ``O_EXCL`` and never overwritten or followed.

    Returns ``{evidenceStatus, evidenceGaps, evidenceManifestSha256}`` with
    ``evidenceStatus`` in ``retained | partial | failed | not-retained``.
    Limits (``max_files``/``max_bytes``, defaults 2000 files / 64 MiB) are
    recorded as explicit ``limit-files``/``limit-bytes`` gaps, never silently
    truncated.
    """

    gaps: list[str] = []
    manifest_files: list[dict[str, Any]] = []
    manifest_skipped: list[dict[str, Any]] = []
    if not isinstance(attempt_id, str) or not ATTEMPT_ID_RE.match(attempt_id):
        return {
            "evidenceStatus": "failed",
            "evidenceGaps": ["copy-error:ValueError"],
            "evidenceManifestSha256": None,
        }
    limits = dict(limits or {})
    try:
        max_files = int(limits.get("max_files", DEFAULT_MAX_FILES))
        max_bytes = int(limits.get("max_bytes", DEFAULT_MAX_BYTES))
    except (TypeError, ValueError):
        max_files, max_bytes = DEFAULT_MAX_FILES, DEFAULT_MAX_BYTES
    max_files = max(max_files, 0)
    max_bytes = max(max_bytes, 0)

    dest = Path(evidence_dir) / attempt_id
    try:
        dest.mkdir(mode=0o700, parents=False, exist_ok=False)
    except OSError as exc:
        gaps.append(f"copy-error:{type(exc).__name__}")
        return {
            "evidenceStatus": "failed",
            "evidenceGaps": gaps,
            "evidenceManifestSha256": None,
        }
    # mkdir honors mode only modulo the umask; enforce it explicitly.
    try:
        os.chmod(dest, 0o700)
    except OSError as exc:
        gaps.append(f"copy-error:{type(exc).__name__}")

    session_path = Path(session_dir) if session_dir else None
    if session_path is None or not session_path.is_dir():
        # A missing log and an ambiguous one both arrive here as "no usable
        # directory". The caller (which saw the stream) upgrades this gap to
        # "session-record-ambiguous" when the stream carried several ids.
        gaps.append("session-record-missing")
        session_path = None

    candidates: list[tuple[Path, str]] = []
    reminder_records: list[dict[str, Any]] = []
    if session_path is not None:
        candidates.extend(_iter_session_files(session_path))
        main_log = session_path / "session.jsonl"
        if main_log.is_file() and not main_log.is_symlink():
            try:
                reminder_records = [
                    json.loads(line)
                    for line in main_log.read_text(encoding="utf-8").splitlines()
                    if line.strip().startswith("{")
                ]
            except (OSError, ValueError):
                reminder_records = []
        try:
            sessions_root = session_path.parents[3]
        except IndexError:
            sessions_root = session_path.parent
        for child_id, log_path in sorted(
            _reminder_child_log_paths(session_path, sessions_root, reminder_records).items()
        ):
            candidates.append((log_path, f"session/reminder/{child_id}/{log_path.name}"))

    scratch_path = Path(scratch_dir) if scratch_dir else None
    scratch_entries: list[tuple[Path, str]] = []
    if scratch_path is not None and scratch_path.is_dir() and not scratch_path.is_symlink():
        stack = sorted(scratch_path.iterdir(), key=lambda p: p.name)
        while stack:
            current = stack.pop(0)
            try:
                rel = current.relative_to(scratch_path)
            except ValueError:
                continue
            info = _lstat(current)
            if info is None:
                manifest_skipped.append(
                    {"path": f"scratch/{rel.as_posix()}", "reason": "unreadable"}
                )
                continue
            if stat.S_ISLNK(info.st_mode):
                manifest_skipped.append(
                    {
                        "path": f"scratch/{rel.as_posix()}",
                        "type": "symlink",
                        "target": _read_link_text(current),
                    }
                )
                continue
            if stat.S_ISDIR(info.st_mode):
                stack.extend(sorted(current.iterdir(), key=lambda p: p.name))
                continue
            if not _is_regular(info):
                manifest_skipped.append(
                    {"path": f"scratch/{rel.as_posix()}", "reason": "not-regular-file"}
                )
                continue
            scratch_entries.append((current, f"scratch/{rel.as_posix()}"))
    candidates.extend(scratch_entries)

    files_retained = 0
    bytes_retained = 0
    truncated = False
    for source, rel in candidates:
        if files_retained >= max_files:
            gaps.append("limit-files")
            truncated = True
            break
        info = _lstat(source)
        if info is None or not _is_regular(info):
            manifest_skipped.append({"path": rel, "reason": "not-regular-file"})
            continue
        size = info.st_size
        if bytes_retained + size > max_bytes:
            gaps.append("limit-bytes")
            truncated = True
            break
        try:
            digest = _copy_file_excl(source, dest / rel, size)
        except OSError as exc:
            gaps.append(f"copy-error:{type(exc).__name__}")
            manifest_skipped.append({"path": rel, "reason": f"copy-error:{type(exc).__name__}"})
            continue
        manifest_files.append({"path": rel, "size": size, "sha256": digest})
        files_retained += 1
        bytes_retained += size

    manifest = {
        "attempt_id": attempt_id,
        "files": manifest_files,
        "skipped": manifest_skipped,
        "limits": {
            "max_files": max_files,
            "max_bytes": max_bytes,
            "files_retained": files_retained,
            "bytes_retained": bytes_retained,
            "truncated": truncated,
        },
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        _write_bytes_excl(dest / "manifest.json", manifest_bytes)
    except OSError as exc:
        gaps.append(f"copy-error:{type(exc).__name__}")
        return {
            "evidenceStatus": "failed",
            "evidenceGaps": gaps,
            "evidenceManifestSha256": None,
        }
    if gaps:
        status = "partial" if manifest_files else "not-retained"
    else:
        status = "retained"
    return {
        "evidenceStatus": status,
        "evidenceGaps": gaps,
        "evidenceManifestSha256": _sha256_bytes(manifest_bytes),
    }


def _ensure_parent_dirs(path: Path) -> None:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass


def _copy_file_excl(source: Path, dest: Path, size: int) -> str:
    _ensure_parent_dirs(dest)
    digest = hashlib.sha256()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        read_flags = os.O_RDONLY | os.O_NOFOLLOW
    else:  # pragma: no cover - platforms without O_NOFOLLOW.
        read_flags = os.O_RDONLY
    src_fd = os.open(source, read_flags)
    try:
        info = os.fstat(src_fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("source is not a regular file")
        dest_fd = os.open(dest, flags, 0o600)
        try:
            while True:
                chunk = os.read(src_fd, 1024 * 64)
                if not chunk:
                    break
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(dest_fd, view)
                    view = view[written:]
        finally:
            os.close(dest_fd)
    finally:
        os.close(src_fd)
    return digest.hexdigest()


def _write_bytes_excl(dest: Path, data: bytes) -> None:
    _ensure_parent_dirs(dest)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    finally:
        os.close(fd)


def _tagged_records(
    session_records: Iterable[Any],
) -> list[tuple[str, Any]]:
    """Normalize written-file inputs to (session tag, record) pairs.

    Accepts either bare session.jsonl records (tagged ``"parent"``) or
    ``{"session": <tag>, "record": <record>}`` wrappers. Child tags are the
    subagent ids; anything falsy becomes ``"parent"``.
    """

    tagged: list[tuple[str, Any]] = []
    for item in session_records:
        if isinstance(item, Mapping) and "record" in item and "session" in item:
            tag = item.get("session") or "parent"
            tagged.append((str(tag), item.get("record")))
        else:
            tagged.append(("parent", item))
    return tagged


def _parse_tool_args(name: str, raw_args: Any) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(raw_args, str):
        return None, False
    try:
        parsed = json.loads(raw_args)
    except (ValueError, TypeError):
        return None, False
    return parsed if isinstance(parsed, dict) else None, True


def written_file_versions(session_records: Iterable[Any]) -> list[dict[str, Any]]:
    """Index every reviewed file write from parent and child session records.

    Reads ``runtime.session`` records whose ``payload.event.kind ==
    "assistant_tool_calls_committed"`` and takes each ``tool_calls[]`` entry
    named ``write_file`` (args JSON ``{"path", "content"}``) or ``edit_file``
    (args JSON ``{"path", "find", "replace"}``), in record order. Each entry
    produces ``{sequence, session, call_id, tool, path, content_sha256 |
    find_sha256+replace_sha256, bytes, outcome}`` where ``outcome`` is the
    matching ``tool_result_batch_committed`` result text (``tool_call_id`` ==
    ``call_id``) or ``"unknown"``. Bodies stay inside the retained
    ``session.jsonl``; the index stores hashes and locations, never a second
    copy. ``tool_output_ref`` events with ``output_ref.kind == "tool_patch"``
    are listed with their retained relative path. Unparsable args produce an
    entry with ``"args": "unparsable"`` and are never dropped.
    """

    tagged = _tagged_records(session_records)
    outcomes: dict[str, str] = {}
    for _, record in tagged:
        event = _session_event(record)
        if not isinstance(event, Mapping):
            continue
        if event.get("kind") != "tool_result_batch_committed":
            continue
        results = event.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, Mapping):
                continue
            call_id = result.get("tool_call_id")
            if isinstance(call_id, str) and call_id not in outcomes:
                text = result.get("text")
                outcomes[call_id] = text if isinstance(text, str) else "unknown"

    indexed: list[dict[str, Any]] = []
    sequence = 0
    for tag, record in tagged:
        event = _session_event(record)
        if not isinstance(event, Mapping):
            continue
        kind = event.get("kind")
        if kind == "assistant_tool_calls_committed":
            calls = event.get("tool_calls")
            if not isinstance(calls, list):
                continue
            for call in calls:
                if not isinstance(call, Mapping):
                    continue
                name = call.get("name")
                if name not in ("write_file", "edit_file"):
                    continue
                call_id = call.get("call_id")
                call_key = call_id if isinstance(call_id, str) else ""
                entry: dict[str, Any] = {
                    "sequence": sequence,
                    "session": tag,
                    "call_id": call_key,
                    "tool": name,
                    "outcome": outcomes.get(call_key, "unknown"),
                }
                parsed, _ = _parse_tool_args(name, call.get("args"))
                if parsed is None:
                    entry["args"] = "unparsable"
                elif name == "write_file":
                    content = parsed.get("content")
                    path = parsed.get("path")
                    if not isinstance(content, str) or not isinstance(path, str):
                        entry["args"] = "unparsable"
                    else:
                        body = content.encode("utf-8")
                        entry["path"] = path
                        entry["content_sha256"] = _sha256_bytes(body)
                        entry["bytes"] = len(body)
                else:
                    find = parsed.get("find")
                    replace = parsed.get("replace")
                    path = parsed.get("path")
                    if (
                        not isinstance(find, str)
                        or not isinstance(replace, str)
                        or not isinstance(path, str)
                    ):
                        entry["args"] = "unparsable"
                    else:
                        find_bytes = find.encode("utf-8")
                        replace_bytes = replace.encode("utf-8")
                        entry["path"] = path
                        entry["find_sha256"] = _sha256_bytes(find_bytes)
                        entry["replace_sha256"] = _sha256_bytes(replace_bytes)
                        entry["bytes"] = len(find_bytes) + len(replace_bytes)
                indexed.append(entry)
                sequence += 1
        elif isinstance(event.get("output_ref"), Mapping):
            # tool_output_ref records (stream task.lifecycle.tool_output_ref or
            # session equivalents) carry event.output_ref; only tool_patch
            # refs point at retained patch files.
            ref = event.get("output_ref")
            if not isinstance(ref, Mapping) or ref.get("kind") != "tool_patch":
                continue
            ref_id = ref.get("id")
            raw_path = ref.get("path") if isinstance(ref.get("path"), str) else ""
            leaf = raw_path.rsplit("/", 1)[-1] if raw_path else ""
            if not leaf:
                leaf = f"{ref_id}-tool_patch.json" if ref_id else "tool_patch.json"
            bucket = str(ref_id) if isinstance(ref_id, str) and ref_id else "unknown"
            indexed.append(
                {
                    "sequence": sequence,
                    "session": tag,
                    "tool": "tool_output_ref",
                    "kind": "tool_patch",
                    "path": raw_path,
                    "retained": f"session/tool-outputs/{bucket}/{leaf}",
                    "outcome": "unknown",
                }
            )
            sequence += 1
    return indexed


def _lexical_path(value: str | Path) -> str:
    text = str(value)
    if not posixpath.isabs(text):
        raise ValueError(f"base path must be absolute: {value!r}")
    return posixpath.normpath(text)


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip("/") + "/")


def path_scope(
    path: str | Path, workspace: str | Path, scratch_dir: str | Path
) -> str:
    """Classify a written path as ``workspace``, ``scratch`` or ``outside``.

    Resolution is purely lexical: ``..`` segments are normalized and
    non-absolute paths resolve against the workspace. The scratch check runs
    first so a scratch tree nested near the workspace is never mislabeled.
    """

    base = _lexical_path(workspace)
    scratch = _lexical_path(scratch_dir)
    text = str(path)
    resolved = posixpath.normpath(text) if posixpath.isabs(text) else posixpath.normpath(posixpath.join(base, text))
    if _under(resolved, scratch):
        return "scratch"
    if _under(resolved, base):
        return "workspace"
    return "outside"


def _split_command_words(command: str) -> list[str]:
    return command.strip().split()


def _lookup_target(words: list[str]) -> str | None:
    """Return the looked-up tool for narrow lookup patterns, else None.

    Recognises ``which X``, ``command -v X``, ``type -p X``, ``type X`` and
    ``whereis X``. This is a narrow pattern list, not a shell parser: flags,
    chained commands and quoting tricks are deliberately not understood, so a
    clean result is not proof that no lookup happened.
    """

    if not words:
        return None
    if words[0] == "which" and len(words) == 2:
        return words[1]
    if words[0] == "command" and len(words) == 3 and words[1] == "-v":
        return words[2]
    if words[0] == "type" and len(words) in (2, 3):
        if len(words) == 3 and words[1] != "-p":
            return None
        target = words[-1]
        return target if not target.startswith("-") else None
    if words[0] == "whereis" and len(words) == 2:
        return words[1]
    return None


def _is_allowed_system(path: str) -> bool:
    normalized = posixpath.normpath(path)
    for prefix in ALLOWED_SYSTEM_PREFIXES:
        if _under(normalized, prefix):
            return True
    if normalized.startswith("/opt/homebrew/opt/"):
        rest = normalized[len("/opt/homebrew/opt/") :]
        parts = rest.split("/")
        if len(parts) >= 3 and parts[1] == "bin":
            return True
    return False


def _classify_resolved(resolved: list[str], workspace: str) -> str:
    if not resolved:
        return "unresolved"
    states = []
    for path in resolved:
        normalized = posixpath.normpath(path)
        if _is_allowed_system(normalized):
            states.append("allowed")
        elif _under(normalized, workspace):
            states.append("workspace")
        else:
            states.append("outside")
    if all(state == "allowed" for state in states):
        return "allowed-system"
    if all(state == "workspace" for state in states):
        return "workspace"
    if "outside" in states:
        return "outside-boundary"
    return "outside-boundary"


def _shell_blobs(events: Iterable[Any]) -> list[dict[str, Any]]:
    """Collect shell command records from a ``--json`` stream.

    Returns ``{command, exit_code, output}`` triples from ``tool.result``
    texts and ``task.lifecycle.output`` chunks that parse as JSON with a
    ``command`` key (the same salvageable shape the provider's command scan
    reads).
    """

    blobs: list[dict[str, Any]] = []

    def harvest(text: Any) -> None:
        if not isinstance(text, str):
            return
        try:
            parsed = json.loads(text)
        except ValueError:
            return
        if isinstance(parsed, Mapping) and isinstance(parsed.get("command"), str):
            blobs.append(
                {
                    "command": parsed["command"],
                    "exit_code": parsed.get("exit_code"),
                    "output": parsed.get("output"),
                }
            )

    for event in events:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if event.get("payload_type") == "tool.result":
            harvest(payload.get("text"))
            continue
        inner = payload.get("event")
        if isinstance(inner, Mapping):
            chunk = inner.get("chunk")
            harvest(chunk)
    return blobs


def _bash_workdirs(session_records: Iterable[Any]) -> dict[str, str]:
    """Map shell command text to its ``workdir`` argument from tool calls."""

    workdirs: dict[str, str] = {}
    for _, record in _tagged_records(session_records):
        event = _session_event(record)
        if not isinstance(event, Mapping):
            continue
        if event.get("kind") != "assistant_tool_calls_committed":
            continue
        calls = event.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, Mapping) or call.get("name") != "bash":
                continue
            parsed, _ = _parse_tool_args("bash", call.get("args"))
            if not isinstance(parsed, dict):
                continue
            command = parsed.get("command")
            workdir = parsed.get("workdir")
            if isinstance(command, str) and isinstance(workdir, str) and workdir:
                workdirs.setdefault(command, workdir)
    return workdirs


def tool_lookups(
    stream_text: str,
    workspace: str | Path,
    session_records: Iterable[Any] | None = None,
    scratch_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Expose tool lookups from shell command records.

    Recognises ``which X``, ``command -v X``, ``type -p X``, ``type X``,
    ``whereis X`` and commands whose first word is an absolute path. Each
    record is ``{command, tool, resolved, classification, workdir?,
    workdir_scope?, flags}`` where ``resolved`` holds output lines that are
    absolute paths and ``classification`` is ``allowed-system``,
    ``workspace``, ``unresolved`` (lookup exit != 0 or no path; recorded but
    never flagged) or ``outside-boundary``. Flags: ``tool-exec-outside-
    boundary`` when an executed absolute path is outside the boundary,
    ``tool-resolution-outside-boundary`` when a lookup resolves outside it,
    and ``command-workdir-outside`` when a shell call's ``workdir`` argument
    is outside workspace/scratch. ``session_records`` (parent/child session
    records, same shape as ``written_file_versions``) supplies ``workdir``
    arguments; ``scratch_dir`` makes the workdir scope scratch-aware and
    defaults to workspace-only scope when omitted. This is a narrow pattern
    list, not a shell parser: a clean result is not proof.
    """

    try:
        base = _lexical_path(workspace)
    except ValueError:
        base = str(workspace)
    if scratch_dir is None:
        scratch = posixpath.join(base, ".attempt-scratch-unset")
    else:
        try:
            scratch = _lexical_path(scratch_dir)
        except ValueError:
            scratch = posixpath.join(base, ".attempt-scratch-unset")
    parsed_events: list[Any] = []
    for line in str(stream_text).splitlines():
        if not line.strip().startswith("{"):
            continue
        try:
            parsed_events.append(json.loads(line))
        except ValueError:
            continue
    parsed_events = [event for event in parsed_events if isinstance(event, Mapping)]
    workdirs = _bash_workdirs(session_records or [])
    records: dict[str, dict[str, Any]] = {}
    for blob in _shell_blobs(parsed_events):
        command = blob["command"]
        words = _split_command_words(command)
        if not words:
            continue
        target = _lookup_target(words)
        first = words[0]
        executed_absolute = target is None and posixpath.isabs(first)
        if target is None and not executed_absolute:
            continue
        tool = target if target is not None else first
        output = blob["output"] if isinstance(blob.get("output"), str) else ""
        resolved = [
            line.strip()
            for line in output.splitlines()
            if line.strip().startswith("/")
        ]
        exit_code = blob.get("exit_code")
        if (isinstance(exit_code, int) and exit_code != 0) or not resolved:
            classification = "unresolved"
            resolved = []
        else:
            classification = _classify_resolved(resolved, base)
        flags: list[str] = []
        if executed_absolute:
            normalized = posixpath.normpath(first)
            if not _is_allowed_system(normalized) and not _under(normalized, base):
                flags.append("tool-exec-outside-boundary")
        elif classification == "outside-boundary":
            flags.append("tool-resolution-outside-boundary")
        key = command
        if key not in records:
            records[key] = {
                "command": command,
                "tool": tool,
                "resolved": resolved,
                "classification": classification,
                "flags": flags,
            }
    for command, workdir in workdirs.items():
        try:
            scope = path_scope(workdir, base, scratch)
        except ValueError:
            scope = "outside"
        if scope == "outside":
            if command in records:
                entry = records[command]
                if "command-workdir-outside" not in entry["flags"]:
                    entry["flags"].append("command-workdir-outside")
                    entry["workdir"] = workdir
                    entry["workdir_scope"] = scope
            else:
                words = _split_command_words(command)
                records[command] = {
                    "command": command,
                    "tool": words[0] if words else "",
                    "resolved": [],
                    "classification": "unresolved",
                    "workdir": workdir,
                    "workdir_scope": scope,
                    "flags": ["command-workdir-outside"],
                }
    return [records[key] for key in sorted(records)]


def _usage_value(usage: Any, key: str) -> int:
    if not isinstance(usage, Mapping):
        return 0
    value = usage.get(key)
    if isinstance(value, bool):
        return 0
    return value if isinstance(value, int) and value >= 0 else 0


def _zero_usage() -> dict[str, int]:
    return {key: 0 for key in USAGE_KEYS}


def _has_truthy_cumulative(record: Any) -> bool:
    if not isinstance(record, Mapping):
        return False
    payload = record.get("payload")
    candidates = [record, payload] if isinstance(payload, Mapping) else [record]
    for candidate in candidates:
        for key in ("usage", "token_usage", "tokenUsage", "quantity", "record"):
            nested = candidate.get(key)
            if isinstance(nested, Mapping) and nested.get("cumulative"):
                return True
    event = _session_event(record)
    if isinstance(event, Mapping):
        for key in ("usage", "quantity"):
            nested = event.get(key)
            if isinstance(nested, Mapping) and nested.get("cumulative"):
                return True
    return False


def _is_cumulative_record(record: Any) -> bool:
    if not isinstance(record, Mapping):
        return False
    payload_type = record.get("payload_type")
    if isinstance(payload_type, str) and (
        payload_type == "session.end" or payload_type.startswith("resource_usage")
    ):
        return True
    event = _session_event(record)
    if isinstance(event, Mapping) and event.get("kind") in {
        "session.end",
        "resource_usage",
        "resource_usage_sample",
    }:
        return True
    return _has_truthy_cumulative(record)


def _summarize_usage_records(
    records: Iterable[Any],
) -> tuple[dict[str, int], dict[str, Any]]:
    """Sum incremental ``model_completed`` usage; cross-check attributions."""

    sums = _zero_usage()
    calls = 0
    provider_attributions = 0
    attributed_input = 0
    attributed_output = 0
    resource_usage_present = False
    for record in records:
        if not isinstance(record, Mapping):
            continue
        payload_type = record.get("payload_type")
        if isinstance(payload_type, str) and payload_type.startswith("resource_usage"):
            resource_usage_present = True
        payload = record.get("payload")
        if isinstance(payload, Mapping):
            top_record = payload.get("record")
            if isinstance(top_record, Mapping) and "resource_usage" in top_record:
                resource_usage_present = True
        event = _session_event(record)
        if isinstance(event, Mapping):
            kind = event.get("kind")
            record_block = event.get("record")
            if isinstance(record_block, Mapping) and "resource_usage" in record_block:
                resource_usage_present = True
            if kind == "goal_usage_attribution":
                inner = record_block if isinstance(record_block, Mapping) else {}
                family = inner.get("usage_family")
                quantity = inner.get("quantity")
                if family == "provider" and isinstance(quantity, Mapping) and quantity.get("reported"):
                    provider_attributions += 1
                    attributed_input += _usage_value(quantity, "input_tokens")
                    attributed_output += _usage_value(quantity, "output_tokens")
                continue
            if kind != "model_completed":
                continue
            if _is_cumulative_record(record):
                continue
            usage = event.get("usage")
            for key in USAGE_KEYS:
                sums[key] += _usage_value(usage, key)
            calls += 1
    detail: dict[str, Any] = {
        **sums,
        "calls": calls,
        "usageConsistency": (
            "match"
            if (
                provider_attributions == calls
                and attributed_input == sums["input_tokens"]
                and attributed_output == sums["output_tokens"]
            )
            or (provider_attributions == 0 and calls == 0)
            else "mismatch"
        ),
        "model_completed_calls": calls,
        "provider_reported_attributions": provider_attributions,
        "model_completed_input_tokens": sums["input_tokens"],
        "attributed_input_tokens": attributed_input,
        "model_completed_output_tokens": sums["output_tokens"],
        "attributed_output_tokens": attributed_output,
    }
    if resource_usage_present:
        # resource_usage is CPU/memory, not tokens: presence only, never summed.
        detail["resource_usage_present"] = True
    return sums, detail


def _normalize_child_links(linked: Iterable[Any] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in linked or []:
        if isinstance(item, Mapping) and isinstance(item.get("id"), str) and item["id"]:
            normalized.append(
                {"id": item["id"], "kind": str(item.get("kind") or "reminder")}
            )
        elif isinstance(item, str) and item:
            normalized.append({"id": item, "kind": "reminder"})
    return normalized


def usage_summary(
    parent_records: Iterable[Any] | None,
    child_records_by_id: Mapping[str, Any] | None = None,
    linked_child_ids: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Summarize per-call token usage without double counting.

    The incremental source is ``runtime.session`` events with
    ``payload.event.kind == "model_completed"`` (``payload.event.usage``).
    ``goal_usage_attribution`` events restate the same calls and are only a
    cross-check (provider+reported count vs ``model_completed`` count, plus
    input/output equality); a mismatch sets ``usageConsistency`` to
    ``"mismatch"`` with both numbers recorded. Cumulative records (any
    usage-like object with a truthy ``cumulative`` key, ``session.end`` or
    ``resource_usage*``) are never summed. Each subagent session and each
    linked reminder child is summarized separately, then totals equal parent
    plus children; child usage is never also counted inside the parent.

    Returns ``{source, parent, children, total | null, coverage}`` where each
    child is ``{id, kind, status: observed | no-usage-records | missing-log,
    ...}``. ``total`` is set only when coverage is ``complete``; partial sums
    go under ``partialTotal`` with the uncovered children listed. Missing
    records are ``unknown``, never zero; no dollar estimate is produced.
    """

    parent_list = list(parent_records or [])
    _, parent = _summarize_usage_records(parent_list)
    children_specs: dict[str, str] = {}
    for child_id, records in (child_records_by_id or {}).items():
        if isinstance(child_id, str) and child_id:
            children_specs[child_id] = "subagent"
    for link in _normalize_child_links(linked_child_ids):
        children_specs.setdefault(link["id"], link["kind"])

    children: list[dict[str, Any]] = []
    total_sums = _zero_usage()
    total_calls = 0
    for key in USAGE_KEYS:
        total_sums[key] += parent.get(key, 0)
    total_calls += int(parent.get("calls", 0))
    uncovered: list[str] = []
    for child_id in sorted(children_specs):
        kind = children_specs[child_id]
        raw = (child_records_by_id or {}).get(child_id)
        if raw is None:
            children.append({"id": child_id, "kind": kind, "status": "missing-log"})
            uncovered.append(child_id)
            continue
        sums, detail = _summarize_usage_records(list(raw))
        status = "observed" if int(detail.get("calls", 0)) > 0 else "no-usage-records"
        entry: dict[str, Any] = {"id": child_id, "kind": kind, "status": status}
        entry.update(detail)
        children.append(entry)
        for key in USAGE_KEYS:
            total_sums[key] += sums[key]
        total_calls += int(detail.get("calls", 0))

    total = {**total_sums, "calls": total_calls} if total_calls else None
    if uncovered:
        coverage = "partial"
        result: dict[str, Any] = {
            "source": "muse-session-record",
            "parent": parent,
            "children": children,
            "total": None,
            "partialTotal": {**total_sums, "calls": total_calls},
            "uncoveredChildren": uncovered,
            "coverage": coverage,
        }
    elif total is None:
        result = {
            "source": "muse-session-record",
            "parent": parent,
            "children": children,
            "total": None,
            "coverage": "unknown",
        }
    else:
        result = {
            "source": "muse-session-record",
            "parent": parent,
            "children": children,
            "total": total,
            "coverage": "complete",
        }
    return result


def _run_stream_id(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    run_stream = payload.get("run_stream")
    if isinstance(run_stream, Mapping):
        value = run_stream.get("id")
        return value if isinstance(value, str) and value else None
    if isinstance(run_stream, str) and run_stream:
        return run_stream
    return None


def classify_completion(
    events: Iterable[Any], exit_code: int | None, timed_out: bool
) -> dict[str, Any]:
    """Classify one ``muse exec --json`` run from its stream envelopes.

    Completed requires all of: not timed out; exit code 0; exactly one
    ``run.lifecycle.started`` run id with every run-scoped event using it;
    exactly one terminal event (``payload_type`` starting with
    ``run.terminal.``) for that run; ``payload.terminal == "completed"``; and
    a non-empty string ``payload.text``. (Runtime contract on retained
    records: every completed Muse run emits a terminal event whose
    ``terminal`` is ``completed`` and whose ``text`` is the final answer;
    ``failed``/``cancelled`` terminals carry a ``reason`` string.)

    Returns ``{status, runId, terminalReason, output?, partialTextSha256?,
    partialTextBytes?}`` with ``output`` only when completed. Delta text is
    never returned as output. Envelope ``stream`` objects without a session
    shape and legacy terminal events without an explicit ``terminal`` field
    (inferred from the ``run.terminal.<name>`` suffix) are tolerated so older
    harnesses keep classifying.
    """

    parsed = [event for event in events if isinstance(event, Mapping)]
    if not parsed:
        return {
            "status": "missing-trace",
            "runId": None,
            "terminalReason": None,
            "partialTextSha256": None,
            "partialTextBytes": 0,
        }
    started_ids: list[str] = []
    scoped_ids: set[str] = set()
    terminals: list[dict[str, Any]] = []
    deltas: list[str] = []
    for event in parsed:
        payload_type = event.get("payload_type")
        payload = event.get("payload")
        payload_dict = payload if isinstance(payload, Mapping) else {}
        if payload_type == "run.lifecycle.started":
            run_id = _run_stream_id(payload)
            if isinstance(run_id, str) and run_id:
                started_ids.append(run_id)
            elif isinstance(payload_dict.get("run_id"), str) and payload_dict["run_id"]:
                started_ids.append(payload_dict["run_id"])
        scoped = _run_stream_id(payload)
        if scoped:
            scoped_ids.add(scoped)
        if isinstance(payload_type, str) and payload_type.startswith("run.terminal."):
            terminals.append(event)
        if payload_type == "run.output.delta" and isinstance(payload_dict.get("text"), str):
            deltas.append(payload_dict["text"])

    distinct_started = set(started_ids)
    run_id: str | None = next(iter(distinct_started)) if len(distinct_started) == 1 else None
    if run_id is None and not distinct_started and len(scoped_ids) == 1:
        (run_id,) = scoped_ids
    partial = "".join(deltas)
    partial_blob = partial.encode("utf-8")
    partial_info: dict[str, Any] = {}
    if deltas:
        partial_info = {
            "partialTextSha256": _sha256_bytes(partial_blob),
            "partialTextBytes": len(partial_blob),
        }

    def result(status: str, reason: Any = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": status,
            "runId": run_id,
            "terminalReason": reason if isinstance(reason, str) else None,
        }
        out.update(partial_info)
        if "partialTextSha256" not in out:
            out["partialTextSha256"] = None
            out["partialTextBytes"] = 0
        return out

    if timed_out:
        return result("timeout")
    if exit_code != 0:
        return result("process-error")
    if len(distinct_started) > 1 or (distinct_started and not scoped_ids <= distinct_started):
        return result("multiple-runs")
    if len(terminals) > 1:
        return result("multiple-terminals")
    if not terminals:
        if deltas:
            return result("missing-terminal-delta-only")
        return result("missing-terminal")
    terminal_event = terminals[0]
    terminal_payload = terminal_event.get("payload")
    terminal_payload = terminal_payload if isinstance(terminal_payload, Mapping) else {}
    payload_type = str(terminal_event.get("payload_type") or "")
    terminal = terminal_payload.get("terminal")
    if not isinstance(terminal, str) or not terminal:
        terminal = payload_type[len("run.terminal.") :] or "unknown"
    reason = terminal_payload.get("reason")
    if terminal == "completed":
        text = terminal_payload.get("text")
        if not isinstance(text, str) or not text:
            empty = result("empty-output", reason)
            return empty
        done = result("completed", reason)
        done["output"] = text
        return done
    if terminal == "failed":
        return result("terminal-failed", reason)
    if terminal == "cancelled":
        return result("terminal-cancelled", reason)
    return result("terminal-other", reason)
