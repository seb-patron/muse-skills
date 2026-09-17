"""Offline synthetic tests for attempt evidence, completion and accounting.

Every fixture here is synthetic (built in temp dirs) and deterministic; no
real Muse data is used. Each test asserts distinguishing behaviour, not just
shape, so a deliberately broken implementation fails it.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from behavioral import experiment, scoring
from behavioral.assertions import review_contract
from behavioral.providers import attempt_evidence, muse_provider

SESSION_ID = "aaaaaaaa-1111-2222-3333-444444444444"
OTHER_SESSION_ID = "bbbbbbbb-1111-2222-3333-444444444444"
CHILD_ID = "cccccccc-1111-2222-3333-444444444444"


def stream_event(payload_type, payload, run_id=None, session_id=SESSION_ID):
    return {
        "schema_version": 1,
        "id": "evt-1",
        "stream": {"kind": "session", "id": session_id},
        "sequence": 1,
        "recorded_at": "2026-09-16T00:00:00Z",
        "record_type": "event",
        "payload_type": payload_type,
        "payload": payload,
    }


def started_event(run_id="run-1", session_id=SESSION_ID):
    return stream_event(
        "run.lifecycle.started",
        {
            "command_id": "cmd-1",
            "kind": "exec",
            "prompt": "review",
            "run_stream": {"kind": "run", "id": run_id},
        },
        session_id=session_id,
    )


def delta_event(run_id, text):
    return stream_event(
        "run.output.delta",
        {
            "command_id": "cmd-1",
            "kind": "output",
            "run_stream": {"kind": "run", "id": run_id},
            "text": text,
        },
    )


def terminal_event(run_id, terminal, text, reason=None):
    return stream_event(
        f"run.terminal.{terminal}",
        {
            "command_id": "cmd-1",
            "kind": "terminal",
            "run_stream": {"kind": "run", "id": run_id},
            "terminal": terminal,
            "text": text,
            "reason": reason,
        },
    )


def workspace_event(workspace_root, commit):
    return stream_event(
        "session.workspace_branch.observed",
        {
            "record": {
                "command_id": "cmd-1",
                "workspace_root": workspace_root,
                "reference": {"kind": "branch", "short_commit": commit[:7]},
                "vcs": "git",
                "commit": commit,
                "dirty": False,
            }
        },
    )


def session_record(event, run_id="run-9"):
    return {
        "payload_type": "runtime.session",
        "payload": {"kind": "run", "run_id": run_id, "event": event},
    }


def tool_call_event(*calls):
    return session_record(
        {"kind": "assistant_tool_calls_committed", "tool_calls": list(calls)}
    )


def write_call(call_id, path, content):
    return {
        "id": f"idx-{call_id}",
        "call_id": call_id,
        "name": "write_file",
        "args": json.dumps({"path": path, "content": content}),
    }


def edit_call(call_id, path, find, replace):
    return {
        "id": f"idx-{call_id}",
        "call_id": call_id,
        "name": "edit_file",
        "args": json.dumps({"path": path, "find": find, "replace": replace}),
    }


def result_event(*results):
    return session_record(
        {"kind": "tool_result_batch_committed", "results": list(results)}
    )


def model_completed_event(usage, run_id="run-9"):
    return session_record({"kind": "model_completed", "usage": dict(usage)}, run_id)


def attribution_event(usage_id, family, quantity, run_id="run-9"):
    return session_record(
        {
            "kind": "goal_usage_attribution",
            "record": {
                "schema_version": 1,
                "usage_id": usage_id,
                "usage_family": family,
                "quantity": dict(quantity),
                "owner": {},
                "goal_attribution": {},
            },
        },
        run_id,
    )


def shell_blob(command, exit_code=0, output=""):
    return stream_event(
        "tool.result",
        {
            "call_id": "call-1",
            "text": json.dumps(
                {
                    "chunk_id": "exec-1-1",
                    "command": command,
                    "exit_code": exit_code,
                    "terminal_status": "completed",
                    "output": output,
                }
            ),
        },
    )


class CompletionTests(unittest.TestCase):
    def test_completed_run(self):
        events = [started_event("run-1"), terminal_event("run-1", "completed", '{"a": 1}')]
        result = attempt_evidence.classify_completion(events, 0, False)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["runId"], "run-1")
        self.assertEqual(result["output"], '{"a": 1}')

    def test_timeout_with_partial_deltas_reports_hash_not_text(self):
        events = [
            started_event("run-1"),
            delta_event("run-1", "partial-1"),
            delta_event("run-1", "partial-2"),
        ]
        result = attempt_evidence.classify_completion(events, None, True)
        self.assertEqual(result["status"], "timeout")
        self.assertNotIn("output", result)
        self.assertEqual(
            result["partialTextSha256"], hashlib.sha256(b"partial-1partial-2").hexdigest()
        )
        self.assertEqual(result["partialTextBytes"], len(b"partial-1partial-2"))

    def test_nonzero_exit_is_process_error(self):
        events = [started_event("run-1"), terminal_event("run-1", "completed", "done")]
        result = attempt_evidence.classify_completion(events, 3, False)
        self.assertEqual(result["status"], "process-error")
        self.assertNotIn("output", result)

    def test_terminal_failed_carries_reason(self):
        events = [started_event("run-1"), terminal_event("run-1", "failed", "", reason="idle timeout")]
        result = attempt_evidence.classify_completion(events, 0, False)
        self.assertEqual(result["status"], "terminal-failed")
        self.assertEqual(result["terminalReason"], "idle timeout")
        self.assertNotIn("output", result)

    def test_terminal_cancelled(self):
        events = [started_event("run-1"), terminal_event("run-1", "cancelled", "", reason="user stop")]
        result = attempt_evidence.classify_completion(events, 0, False)
        self.assertEqual(result["status"], "terminal-cancelled")
        self.assertNotIn("output", result)

    def test_delta_only_without_terminal(self):
        events = [started_event("run-1"), delta_event("run-1", "half an answer")]
        result = attempt_evidence.classify_completion(events, 0, False)
        self.assertEqual(result["status"], "missing-terminal-delta-only")
        self.assertNotIn("output", result)

    def test_no_terminal_no_deltas(self):
        events = [started_event("run-1")]
        result = attempt_evidence.classify_completion(events, 0, False)
        self.assertEqual(result["status"], "missing-terminal")

    def test_no_events_is_missing_trace(self):
        result = attempt_evidence.classify_completion([], 0, False)
        self.assertEqual(result["status"], "missing-trace")
        self.assertNotIn("output", result)

    def test_empty_terminal_text(self):
        events = [started_event("run-1"), terminal_event("run-1", "completed", "")]
        result = attempt_evidence.classify_completion(events, 0, False)
        self.assertEqual(result["status"], "empty-output")
        self.assertNotIn("output", result)

    def test_two_run_ids_rejected(self):
        events = [
            started_event("run-1"),
            started_event("run-2"),
            terminal_event("run-1", "completed", "done"),
        ]
        result = attempt_evidence.classify_completion(events, 0, False)
        self.assertEqual(result["status"], "multiple-runs")
        self.assertNotIn("output", result)

    def test_scoped_event_outside_started_run_rejected(self):
        events = [
            started_event("run-1"),
            delta_event("run-2", "stray"),
            terminal_event("run-1", "completed", "done"),
        ]
        result = attempt_evidence.classify_completion(events, 0, False)
        self.assertEqual(result["status"], "multiple-runs")

    def test_two_terminals_rejected(self):
        events = [
            started_event("run-1"),
            terminal_event("run-1", "completed", "first"),
            terminal_event("run-1", "completed", "second"),
        ]
        result = attempt_evidence.classify_completion(events, 0, False)
        self.assertEqual(result["status"], "multiple-terminals")
        self.assertNotIn("output", result)

    def test_stream_without_run_start_is_never_completed(self):
        # A delta from run A plus a terminal from run B, with no
        # run.lifecycle.started anywhere, must not complete any run.
        events = [delta_event("run-a", "half"), terminal_event("run-b", "completed", "done")]
        result = attempt_evidence.classify_completion(events, 0, False)
        self.assertEqual(result["status"], "missing-run-start")
        self.assertNotIn("output", result)
        self.assertIsNone(result["runId"])

    def test_terminal_without_run_id_never_completes(self):
        started = started_event("run-1")
        terminal = dict(terminal_event("run-1", "completed", "done"))
        del terminal["payload"]["run_stream"]
        result = attempt_evidence.classify_completion([started, terminal], 0, False)
        self.assertEqual(result["status"], "multiple-runs")
        self.assertNotIn("output", result)

    def test_terminal_missing_terminal_field_is_other(self):
        # A terminal payload with a reason but no terminal string is
        # terminal-other, never inferred completed from the payload suffix.
        started = started_event("run-1")
        terminal = dict(terminal_event("run-1", "completed", "done"))
        del terminal["payload"]["terminal"]
        terminal["payload"]["reason"] = "some reason"
        result = attempt_evidence.classify_completion([started, terminal], 0, False)
        self.assertEqual(result["status"], "terminal-other")
        self.assertNotIn("output", result)

    def test_session_id_requires_exactly_one_valid_uuid(self):
        good = [stream_event("run.output.delta", {"text": "x"})]
        self.assertEqual(attempt_evidence.session_id_from_stream(good), SESSION_ID)
        mixed = [
            stream_event("run.output.delta", {"text": "x"}),
            stream_event("run.output.delta", {"text": "y"}, session_id=OTHER_SESSION_ID),
        ]
        self.assertIsNone(attempt_evidence.session_id_from_stream(mixed))
        self.assertIsNone(attempt_evidence.session_id_from_stream([]))
        bad = [stream_event("run.output.delta", {"text": "x"}, session_id="../escape")]
        self.assertIsNone(attempt_evidence.session_id_from_stream(bad))
        self.assertEqual(attempt_evidence.session_record_gap(mixed), "session-record-ambiguous")
        self.assertEqual(attempt_evidence.session_record_gap([]), "session-record-missing")
        self.assertIsNone(attempt_evidence.session_record_gap(good))


ATTEMPT_ID = "0" * 32


def make_session_dir(root, session_id=SESSION_ID, records=(), children=(),
                     tool_outputs=(), reminders=()):
    """Build a synthetic <root>/YYYY/MM/DD/<id>/ session record tree."""
    session_dir = root / "2026" / "09" / "16" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "session.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    for child_id, child_records in children:
        child_dir = session_dir / "subagent" / child_id
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "session.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in child_records),
            encoding="utf-8",
        )
    for rel, body in tool_outputs:
        target = session_dir / "tool-outputs" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    (session_dir / "tool-outputs" / ".spool").mkdir(parents=True, exist_ok=True)
    (session_dir / "tool-outputs" / ".spool" / "pending.tmp").write_text("x", encoding="utf-8")
    (session_dir / "stale.lock").write_text("lock", encoding="utf-8")
    for child_id, rel, body in reminders:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return session_dir


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sessions = self.root / "sessions"
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()

    def test_retains_session_scratch_and_manifest(self):
        records = [tool_call_event(write_call("c1", "note.md", "hello"))]
        session_dir = make_session_dir(
            self.sessions,
            records=records,
            children=[(CHILD_ID, [tool_call_event(write_call("c2", "child.md", "hi"))])],
            tool_outputs=[("uuid1/call-bash.txt", "output\n")],
        )
        (self.scratch / "work.txt").write_text("scratch body", encoding="utf-8")
        result = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, None
        )
        self.assertEqual(result["evidenceStatus"], "retained")
        self.assertEqual(result["evidenceGaps"], [])
        dest = self.evidence / ATTEMPT_ID
        for rel in (
            "session/session.jsonl",
            f"session/subagent/{CHILD_ID}/session.jsonl",
            "session/tool-outputs/uuid1/call-bash.txt",
            "scratch/work.txt",
            "manifest.json",
        ):
            self.assertTrue((dest / rel).is_file(), rel)
        # Bodies live in the retained session copy; spool and lock files stay out.
        self.assertIn("hello", (dest / "session/session.jsonl").read_text(encoding="utf-8"))
        self.assertFalse((dest / "session/tool-outputs/.spool").exists())
        self.assertFalse((dest / "session/stale.lock").exists())
        manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            result["evidenceManifestSha256"],
            hashlib.sha256((dest / "manifest.json").read_bytes()).hexdigest(),
        )
        paths = [entry["path"] for entry in manifest["files"]]
        self.assertIn("scratch/work.txt", paths)
        for entry in manifest["files"]:
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
        # Retained files are 0600 and directories 0700.
        self.assertEqual(stat.S_IMODE((dest / "session/session.jsonl").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((dest / "session").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o700)

    def test_reminder_child_log_retained_when_inside_root(self):
        log_rel = f"2026/09/16/{CHILD_ID}/session.jsonl"
        link = session_record(
            {
                "kind": "memory_reminder_child_session_linked",
                "parent_session_id": SESSION_ID,
                "child_session_id": CHILD_ID,
                "child_session_log_path": log_rel,
            }
        )
        session_dir = make_session_dir(
            self.sessions,
            records=[link],
            reminders=[(CHILD_ID, log_rel, "{}\n")],
        )
        result = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, None
        )
        self.assertEqual(result["evidenceStatus"], "retained")
        retained = self.evidence / ATTEMPT_ID / f"session/reminder/{CHILD_ID}/session.jsonl"
        self.assertTrue(retained.is_file())

    def test_traversal_child_id_never_leaves_evidence_root(self):
        link = session_record(
            {
                "kind": "memory_reminder_child_session_linked",
                "parent_session_id": SESSION_ID,
                "child_session_id": "../../../../escaped",
                "child_session_log_path": "2026/09/16/escaped/session.jsonl",
            }
        )
        (self.sessions / "2026" / "09" / "16" / "escaped").mkdir(parents=True)
        (self.sessions / "2026" / "09" / "16" / "escaped" / "session.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )
        session_dir = make_session_dir(self.sessions, records=[link])
        before = {path.name for path in self.root.iterdir()}
        result = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, None
        )
        self.assertIn("invalid-child-id", result["evidenceGaps"])
        self.assertNotEqual(result["evidenceStatus"], "retained")
        # Nothing was copied outside the evidence directory.
        self.assertEqual({path.name for path in self.root.iterdir()}, before)
        self.assertFalse((self.root / "escaped").exists())

    def test_missing_session_jsonl_is_record_missing_gap(self):
        session_dir = make_session_dir(self.sessions, records=[])
        (session_dir / "session.jsonl").unlink()
        result = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, None
        )
        self.assertIn("session-record-missing", result["evidenceGaps"])
        self.assertNotEqual(result["evidenceStatus"], "retained")

    def test_symlinked_session_jsonl_is_record_missing_gap(self):
        session_dir = make_session_dir(self.sessions, records=[])
        real = session_dir / "session.jsonl"
        body = real.read_bytes()
        real.unlink()
        (self.root / "real-session.jsonl").write_bytes(body)
        real.symlink_to(self.root / "real-session.jsonl")
        result = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, None
        )
        self.assertIn("session-record-missing", result["evidenceGaps"])
        self.assertNotEqual(result["evidenceStatus"], "retained")
        manifest = json.loads(
            (self.evidence / ATTEMPT_ID / "manifest.json").read_text(encoding="utf-8")
        )
        links = [entry for entry in manifest["skipped"] if entry.get("type") == "symlink"]
        self.assertTrue(
            any(entry["path"] == "session/session.jsonl" for entry in links)
        )

    def test_unreadable_subdir_records_walk_error_and_manifest(self):
        (self.scratch / "readable.txt").write_text("ok", encoding="utf-8")
        locked = self.scratch / "locked"
        locked.mkdir()
        (locked / "secret.txt").write_text("secret", encoding="utf-8")
        locked.chmod(0o000)
        self.addCleanup(locked.chmod, 0o700)
        session_dir = make_session_dir(self.sessions, records=[])
        result = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, None
        )
        self.assertIn("walk-error:PermissionError", result["evidenceGaps"])
        self.assertNotEqual(result["evidenceStatus"], "retained")
        # The manifest still lands, naming the skipped path.
        manifest = json.loads(
            (self.evidence / ATTEMPT_ID / "manifest.json").read_text(encoding="utf-8")
        )
        reasons = [entry.get("reason") for entry in manifest["skipped"]]
        self.assertIn("walk-error:PermissionError", reasons)

    def test_evidence_refuses_to_overwrite_existing_file(self):
        dest = self.evidence / ATTEMPT_ID / "session" / "session.jsonl"
        dest.parent.mkdir(parents=True)
        dest.write_text("sentinel", encoding="utf-8")
        session_dir = make_session_dir(self.sessions, records=[])
        result = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, None
        )
        self.assertIn("copy-error:FileExistsError", result["evidenceGaps"])
        # The pre-existing file is preserved, never overwritten.
        self.assertEqual(dest.read_text(encoding="utf-8"), "sentinel")

    def test_symlink_in_scratch_recorded_never_followed(self):
        target = self.root / "outside-target.txt"
        target.write_text("secret", encoding="utf-8")
        (self.scratch / "link.txt").symlink_to(target)
        (self.scratch / "real.txt").write_text("real", encoding="utf-8")
        session_dir = make_session_dir(self.sessions)
        result = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, None
        )
        self.assertEqual(result["evidenceStatus"], "retained")
        dest = self.evidence / ATTEMPT_ID
        self.assertFalse((dest / "scratch/link.txt").exists())
        self.assertTrue((dest / "scratch/real.txt").is_file())
        manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        links = [entry for entry in manifest["skipped"] if entry.get("type") == "symlink"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["path"], "scratch/link.txt")
        self.assertEqual(links[0]["target"], str(target))

    def test_missing_session_dir_is_explicit_gap(self):
        result = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, None, self.scratch, None
        )
        self.assertEqual(result["evidenceStatus"], "not-retained")
        self.assertEqual(result["evidenceGaps"], ["session-record-missing"])

    def test_ambiguous_session_dir_matches_nothing(self):
        for day in ("16", "17"):
            other = self.sessions / "2026" / "09" / day / SESSION_ID
            other.mkdir(parents=True, exist_ok=True)
            (other / "session.jsonl").write_text("{}\n", encoding="utf-8")
        found = attempt_evidence.locate_session_dir(self.sessions, SESSION_ID)
        self.assertIsNone(found)

    def test_located_session_dir_must_stay_inside_root(self):
        session_dir = make_session_dir(self.sessions)
        found = attempt_evidence.locate_session_dir(self.sessions, SESSION_ID)
        self.assertEqual(found, session_dir.resolve())
        self.assertIsNone(attempt_evidence.locate_session_dir(self.sessions, "../escape"))
        self.assertIsNone(
            attempt_evidence.locate_session_dir(self.sessions / "2026" / "09" / "16", SESSION_ID)
        )

    def test_file_limit_is_an_explicit_gap_never_silent(self):
        session_dir = make_session_dir(self.sessions)
        for name in ("a.txt", "b.txt", "c.txt"):
            (self.scratch / name).write_text(name, encoding="utf-8")
        result = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, {"max_files": 2}
        )
        self.assertEqual(result["evidenceStatus"], "partial")
        self.assertIn("limit-files", result["evidenceGaps"])
        manifest = json.loads(
            (self.evidence / ATTEMPT_ID / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(manifest["files"]), 2)
        self.assertTrue(manifest["limits"]["truncated"])

    def test_byte_limit_is_an_explicit_gap(self):
        session_dir = make_session_dir(self.sessions)
        (self.scratch / "big.txt").write_text("0123456789", encoding="utf-8")
        result = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, {"max_bytes": 5}
        )
        self.assertEqual(result["evidenceStatus"], "partial")
        self.assertIn("limit-bytes", result["evidenceGaps"])

    def test_no_overwrite_second_retain_fails(self):
        session_dir = make_session_dir(self.sessions)
        first = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, None
        )
        self.assertEqual(first["evidenceStatus"], "retained")
        manifest_before = (self.evidence / ATTEMPT_ID / "manifest.json").read_bytes()
        second = attempt_evidence.retain_attempt_evidence(
            self.evidence, ATTEMPT_ID, session_dir, self.scratch, None
        )
        self.assertEqual(second["evidenceStatus"], "failed")
        self.assertTrue(
            any(gap.startswith("copy-error:") for gap in second["evidenceGaps"])
        )
        self.assertEqual(
            (self.evidence / ATTEMPT_ID / "manifest.json").read_bytes(), manifest_before
        )


class WrittenFileTests(unittest.TestCase):
    def test_write_edit_write_versions_indexed_with_hashes(self):
        records = [
            tool_call_event(write_call("c1", "scratch/a.txt", "version one")),
            result_event({"tool_call_index": 0, "tool_call_id": "c1", "text": "wrote 11 bytes"}),
            tool_call_event(edit_call("c2", "scratch/a.txt", "one", "two")),
            result_event({"tool_call_index": 0, "tool_call_id": "c2", "text": "edited 1 hunk"}),
            tool_call_event(write_call("c3", "scratch/a.txt", "version three")),
        ]
        indexed = attempt_evidence.written_file_versions(records)
        self.assertEqual([entry["sequence"] for entry in indexed], [0, 1, 2])
        self.assertEqual([entry["tool"] for entry in indexed], ["write_file", "edit_file", "write_file"])
        self.assertTrue(all(entry["session"] == "parent" for entry in indexed))
        self.assertEqual(
            indexed[0]["content_sha256"], hashlib.sha256(b"version one").hexdigest()
        )
        self.assertEqual(indexed[2]["content_sha256"], hashlib.sha256(b"version three").hexdigest())
        self.assertNotEqual(indexed[0]["content_sha256"], indexed[2]["content_sha256"])
        self.assertEqual(indexed[1]["find_sha256"], hashlib.sha256(b"one").hexdigest())
        self.assertEqual(indexed[1]["replace_sha256"], hashlib.sha256(b"two").hexdigest())
        self.assertEqual(indexed[0]["outcome"], "wrote 11 bytes")
        self.assertEqual(indexed[1]["outcome"], "edited 1 hunk")
        # No matching result batch: outcome is unknown, never dropped.
        self.assertEqual(indexed[2]["outcome"], "unknown")
        # Bodies are not duplicated into the index.
        self.assertNotIn("version one", json.dumps(indexed))

    def test_child_writes_tagged_and_unparsable_args_kept(self):
        parent = tool_call_event(write_call("c1", "a.txt", "parent body"))
        child = {
            "session": CHILD_ID,
            "record": tool_call_event(
                {
                    "id": "idx-x",
                    "call_id": "x",
                    "name": "write_file",
                    "args": "{not json",
                }
            ),
        }
        indexed = attempt_evidence.written_file_versions([parent, child])
        self.assertEqual(indexed[0]["session"], "parent")
        self.assertEqual(indexed[1]["session"], CHILD_ID)
        self.assertEqual(indexed[1]["args"], "unparsable")
        self.assertEqual(indexed[1]["outcome"], "unknown")

    def test_tool_patch_ref_lists_retained_path(self):
        record = session_record(
            {
                "kind": "output",
                "output_ref": {
                    "id": "uuid-9",
                    "kind": "tool_patch",
                    "path": "call-3-tool_patch.json",
                },
            }
        )
        indexed = attempt_evidence.written_file_versions([record])
        self.assertEqual(len(indexed), 1)
        self.assertEqual(indexed[0]["tool"], "tool_output_ref")
        self.assertEqual(indexed[0]["retained"], "session/tool-outputs/uuid-9/call-3-tool_patch.json")

    def test_path_scope_resolves_lexically(self):
        workspace, scratch = "/tmp/attempt/repo", "/tmp/attempt/scratch"
        self.assertEqual(attempt_evidence.path_scope("/tmp/attempt/repo/a.py", workspace, scratch), "workspace")
        self.assertEqual(attempt_evidence.path_scope("a.py", workspace, scratch), "workspace")
        self.assertEqual(attempt_evidence.path_scope("/tmp/attempt/scratch/note", workspace, scratch), "scratch")
        self.assertEqual(attempt_evidence.path_scope("/tmp/attempt/repo/../scratch/note", workspace, scratch), "scratch")
        self.assertEqual(attempt_evidence.path_scope("/tmp/attempt/repo/../../etc/passwd", workspace, scratch), "outside")
        self.assertEqual(attempt_evidence.path_scope("/Users/x/bin/tool", workspace, scratch), "outside")


class ToolLookupTests(unittest.TestCase):
    WORKSPACE = "/tmp/attempt/repo"

    def stream(self, *blobs):
        return "\n".join(json.dumps(blob) for blob in blobs)

    def test_allowed_system_lookup_not_flagged(self):
        text = self.stream(shell_blob("which python3", 0, "/opt/homebrew/bin/python3\n"))
        (record,) = attempt_evidence.tool_lookups(text, self.WORKSPACE)
        self.assertEqual(record["tool"], "python3")
        self.assertEqual(record["resolved"], ["/opt/homebrew/bin/python3"])
        self.assertEqual(record["classification"], "allowed-system")
        self.assertEqual(record["flags"], [])

    def test_failed_lookup_recorded_never_flagged(self):
        text = self.stream(shell_blob("command -v foo", 1, ""))
        (record,) = attempt_evidence.tool_lookups(text, self.WORKSPACE)
        self.assertEqual(record["classification"], "unresolved")
        self.assertEqual(record["flags"], [])

    def test_home_venv_resolution_flagged(self):
        text = self.stream(shell_blob("which pytest", 0, "/Users/x/venv/bin/pytest\n"))
        (record,) = attempt_evidence.tool_lookups(text, self.WORKSPACE)
        self.assertEqual(record["classification"], "outside-boundary")
        self.assertEqual(record["flags"], ["tool-resolution-outside-boundary"])

    def test_executed_outside_path_flagged(self):
        text = self.stream(shell_blob("/Users/x/bin/tool --check", 0, "ok\n"))
        (record,) = attempt_evidence.tool_lookups(text, self.WORKSPACE)
        self.assertIn("tool-exec-outside-boundary", record["flags"])

    def test_env_launcher_not_flagged(self):
        text = self.stream(shell_blob("/usr/bin/env python3 check.py", 0, "Python 3.14.7\n"))
        (record,) = attempt_evidence.tool_lookups(text, self.WORKSPACE)
        self.assertEqual(record["flags"], [])

    def test_workspace_tool_not_flagged(self):
        text = self.stream(
            shell_blob("type -p review", 0, f"{self.WORKSPACE}/bin/review\n")
        )
        (record,) = attempt_evidence.tool_lookups(text, self.WORKSPACE)
        self.assertEqual(record["classification"], "workspace")
        self.assertEqual(record["flags"], [])

    def test_outside_workdir_flagged_with_scratch_scope(self):
        records = [
            tool_call_event(
                {
                    "id": "idx-b",
                    "call_id": "b",
                    "name": "bash",
                    "args": json.dumps(
                        {"command": "git diff", "workdir": "/tmp/elsewhere"}
                    ),
                }
            )
        ]
        found = attempt_evidence.tool_lookups(
            "", self.WORKSPACE, records, "/tmp/attempt/scratch"
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["flags"], ["command-workdir-outside"])

    def test_inside_workdir_not_flagged(self):
        records = [
            tool_call_event(
                {
                    "id": "idx-b",
                    "call_id": "b",
                    "name": "bash",
                    "args": json.dumps(
                        {"command": "git diff", "workdir": f"{self.WORKSPACE}/sub"}
                    ),
                }
            )
        ]
        found = attempt_evidence.tool_lookups(
            "", self.WORKSPACE, records, "/tmp/attempt/scratch"
        )
        self.assertEqual(found, [])


class UsageTests(unittest.TestCase):
    def usage(self, input_tokens, output_tokens, **extra):
        base = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "reasoning_tokens": 0,
        }
        base.update(extra)
        return base

    def test_matching_attributions_never_double_count(self):
        parent = [
            model_completed_event(self.usage(10, 5)),
            attribution_event(
                "u1", "provider",
                {"unit": "tokens", "reported": True, "input_tokens": 10,
                 "output_tokens": 5, "cached_tokens": 0, "reasoning_tokens": 0,
                 "main_llm_steps": 1},
            ),
            model_completed_event(self.usage(20, 7)),
            attribution_event(
                "u2", "provider",
                {"unit": "tokens", "reported": True, "input_tokens": 20,
                 "output_tokens": 7, "cached_tokens": 0, "reasoning_tokens": 0,
                 "main_llm_steps": 1},
            ),
            attribution_event(
                "u3", "tool",
                {"unit": "tokens", "reported": False, "input_tokens": 0,
                 "output_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0,
                 "main_llm_steps": 0},
            ),
        ]
        summary = attempt_evidence.usage_summary(parent, {}, [])
        self.assertEqual(summary["source"], "muse-session-record")
        self.assertEqual(summary["parent"]["input_tokens"], 30)
        self.assertEqual(summary["parent"]["output_tokens"], 12)
        self.assertEqual(summary["parent"]["calls"], 2)
        self.assertEqual(summary["parent"]["usageConsistency"], "match")
        self.assertEqual(summary["coverage"], "complete")
        self.assertEqual(summary["total"]["input_tokens"], 30)
        self.assertNotIn("partialTotal", summary)
        self.assertNotIn("cost", json.dumps(summary).lower())

    def test_attribution_mismatch_records_both_numbers(self):
        parent = [
            model_completed_event(self.usage(10, 5)),
            attribution_event(
                "u1", "provider",
                {"unit": "tokens", "reported": True, "input_tokens": 999,
                 "output_tokens": 5, "cached_tokens": 0, "reasoning_tokens": 0,
                 "main_llm_steps": 1},
            ),
        ]
        summary = attempt_evidence.usage_summary(parent, {}, [])
        self.assertEqual(summary["parent"]["usageConsistency"], "mismatch")
        self.assertEqual(summary["parent"]["model_completed_calls"], 1)
        self.assertEqual(summary["parent"]["provider_reported_attributions"], 1)
        self.assertEqual(summary["parent"]["model_completed_input_tokens"], 10)
        self.assertEqual(summary["parent"]["attributed_input_tokens"], 999)
        # The incremental source still sums once; the restatement adds nothing.
        self.assertEqual(summary["total"]["input_tokens"], 10)

    def test_child_usage_summed_separately(self):
        parent = [model_completed_event(self.usage(10, 5))]
        children = {CHILD_ID: [model_completed_event(self.usage(4, 2))]}
        summary = attempt_evidence.usage_summary(parent, children, [])
        (child,) = summary["children"]
        self.assertEqual(child["id"], CHILD_ID)
        self.assertEqual(child["kind"], "subagent")
        self.assertEqual(child["status"], "observed")
        self.assertEqual(summary["total"]["input_tokens"], 14)
        self.assertEqual(summary["total"]["calls"], 2)
        self.assertEqual(summary["coverage"], "complete")

    def test_missing_child_log_is_partial_with_null_total(self):
        parent = [model_completed_event(self.usage(10, 5))]
        summary = attempt_evidence.usage_summary(
            parent, {}, [{"id": "gone-child", "kind": "reminder"}]
        )
        self.assertEqual(summary["coverage"], "partial")
        self.assertIsNone(summary["total"])
        self.assertEqual(summary["partialTotal"]["input_tokens"], 10)
        self.assertEqual(summary["uncoveredChildren"], ["gone-child"])
        self.assertEqual(summary["children"][0]["status"], "missing-log")
        self.assertNotIn("input_tokens", summary["children"][0])

    def test_cumulative_and_resource_records_never_summed(self):
        parent = [
            model_completed_event(self.usage(10, 5)),
            session_record({"kind": "model_completed", "usage": {**self.usage(100, 100), "cumulative": True}}),
            {"payload_type": "session.end", "payload": {"record": {"resource_usage": {"cpu_ms": 9}}}},
            {"payload_type": "resource_usage_sample", "payload": {"record": {"cpu": 1}}},
        ]
        summary = attempt_evidence.usage_summary(parent, {}, [])
        self.assertEqual(summary["total"]["input_tokens"], 10)
        self.assertEqual(summary["total"]["calls"], 1)
        self.assertTrue(summary["parent"].get("resource_usage_present"))

    def test_no_usage_is_unknown_never_zero(self):
        summary = attempt_evidence.usage_summary([], {}, [])
        self.assertEqual(summary["coverage"], "unknown")
        self.assertIsNone(summary["total"])
        self.assertNotIn("partialTotal", summary)

    def test_model_completed_without_usage_is_partial_never_zero(self):
        parent = [
            model_completed_event(self.usage(10, 5)),
            session_record({"kind": "model_completed", "duration_ms": 3}),
        ]
        summary = attempt_evidence.usage_summary(parent, {}, [])
        # The call without a usage object is not covered usage: partial
        # coverage with a null total, never zeros for the missing call.
        self.assertEqual(summary["coverage"], "partial")
        self.assertIsNone(summary["total"])
        self.assertEqual(summary["partialTotal"]["input_tokens"], 10)
        self.assertEqual(summary["partialTotal"]["calls"], 1)


class ProviderAttemptTests(unittest.TestCase):
    MODEL = "muse-spark-1.3-contributor"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "source"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.email", "eval@example.invalid")
        self.git("config", "user.name", "Eval Fixture")
        (self.repo / "skills/example").mkdir(parents=True)
        (self.repo / "skills/example/SKILL.md").write_text("example skill\n", encoding="utf-8")
        (self.repo / "value.txt").write_text("base\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        (self.repo / "value.txt").write_text("head\n", encoding="utf-8")
        self.git("commit", "--quiet", "-am", "head")
        self.head = self.git("rev-parse", "HEAD").stdout.strip()
        self.trace_dir = Path(self.temp.name) / "traces"
        self.trace_dir.mkdir()
        self.sessions = Path(self.temp.name) / "sessions"

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True, check=True
        )

    def completed_stream(self, workspace=None, commit=None, head=None, extra=(),
                         run_id="run-1", session_id=SESSION_ID, verdict="APPROVE"):
        review = json.dumps({"verdict": verdict, "head_sha": self.head if head is None else head})
        events = [
            started_event(run_id, session_id),
            {
                "payload_type": "run.started",
                "payload": {"model": self.MODEL},
                "stream": {"kind": "session", "id": session_id},
            },
            {
                "payload_type": "agent.skill_read.observed",
                "payload": {"skill_id": "example"},
                "stream": {"kind": "session", "id": session_id},
            },
            *extra,
            dict(
                terminal_event(run_id, "completed", review),
                stream={"kind": "session", "id": session_id},
            ),
        ]
        if workspace is not None:
            events.insert(
                -1,
                dict(
                    workspace_event(workspace, commit if commit is not None else self.head),
                    stream={"kind": "session", "id": session_id},
                ),
            )
        return "\n".join(json.dumps(event) for event in events)

    def make_session(self, records=(), children=()):
        self.sessions.mkdir(parents=True, exist_ok=True)
        return make_session_dir(self.sessions, records=records, children=children)

    def call(self, stdout, exit_code=0, timeout=None, variant="none",
             source="muse-skills", session_records=(), extra_config=None,
             provider_label="synthetic-provider", repeat_index=0, no_trace=False):
        if session_records:
            self.make_session(records=session_records)
        seen = {}

        def fake_run(args, workspace, timeout_seconds, env):
            seen["workspace"] = workspace
            seen["env"] = dict(env)
            if timeout is not None:
                raise timeout
            return subprocess.CompletedProcess(args, exit_code, stdout, "")

        config = {
            "variant": variant,
            "timeout_seconds": 60,
            "repo_root": str(self.repo),
            **(extra_config or {}),
        }
        if variant != "none":
            config.update(
                {
                    "candidate_id": "current",
                    "model": self.MODEL,
                    "max_model_steps": 24,
                }
            )
        # Mirror the real Promptfoo 0.123.0 Python-provider call shape: the
        # provider label travels in options (alongside config) and the context
        # carries vars/test/repeatIndex/testIdx with no context["provider"].
        options = {"config": config}
        if provider_label is not None:
            options["label"] = provider_label
        context = {
            "vars": {
                "base_sha": self.base,
                "head_sha": self.head,
                "skill_name": "example",
                "source_repository": source,
            },
            "test": {"metadata": {"case_id": "synthetic-case"}},
            "repeatIndex": repeat_index,
            "testIdx": 0,
        }
        env = {"MUSE_EVAL_SESSIONS_ROOT": str(self.sessions)}
        if not no_trace:
            env["MUSE_EVAL_TRACE_DIR"] = str(self.trace_dir)
        with mock.patch.object(
            muse_provider, "_prepare_workspace", return_value=(self.base, self.head)
        ), mock.patch.dict(
            os.environ, env,
        ), mock.patch.object(
            muse_provider.shutil, "which", return_value="/usr/bin/muse"
        ), mock.patch.object(
            muse_provider, "_run_muse", side_effect=fake_run
        ):
            if no_trace:
                os.environ.pop("MUSE_EVAL_TRACE_DIR", None)
            response = muse_provider.call_api("review now", options, context)
        return response, seen

    def test_completed_attempt_envelope_and_ledger(self):
        stdout = self.completed_stream()
        self.make_session()
        response, seen = self.call(stdout)
        self.assertNotIn("error", response)
        metadata = response["metadata"]
        self.assertRegex(metadata["attemptId"], r"^[0-9a-f]{32}$")
        self.assertEqual(
            metadata["scheduleKey"], "synthetic-provider|synthetic-case|0"
        )
        self.assertEqual(metadata["museSessionId"], SESSION_ID)
        self.assertEqual(metadata["museRunId"], "run-1")
        self.assertEqual(metadata["completionStatus"], "completed")
        self.assertEqual(metadata["headBinding"], "match")
        self.assertEqual(metadata["reviewedHeadReported"], self.head)
        self.assertEqual(metadata["sessionWorkspaceBinding"], "unknown")
        self.assertEqual(metadata["evidenceStatus"], "retained")
        self.assertEqual(metadata["ledgerStatus"], "ok")
        self.assertEqual(metadata["scratchIsolation"], "per-attempt-tmpdir")
        self.assertEqual(metadata["scratchIsolationProof"], "none")
        # Per-attempt scratch reached the review env and survived only as evidence.
        scratch = Path(seen["env"]["TMPDIR"])
        self.assertEqual(seen["env"]["TMP"], str(scratch))
        self.assertEqual(seen["env"]["TEMP"], str(scratch))
        self.assertFalse(scratch.exists())
        self.assertFalse(seen["workspace"].parent.exists())
        lines = (self.trace_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
        phases = [json.loads(line)["phase"] for line in lines]
        self.assertEqual(phases, ["started", "finished"])
        finished = json.loads(lines[-1])
        self.assertEqual(finished["completionStatus"], "completed")
        self.assertIsNone(finished["error"])
        self.assertEqual(finished["attemptId"], metadata["attemptId"])

    def test_schedule_key_uses_options_label_and_context_repeat(self):
        stdout = self.completed_stream()
        self.make_session()
        response, _ = self.call(
            stdout, provider_label="promptfoo-label", repeat_index=2
        )
        self.assertNotIn("error", response)
        metadata = response["metadata"]
        self.assertEqual(
            metadata["scheduleKey"], "promptfoo-label|synthetic-case|2"
        )
        started = json.loads(
            (self.trace_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        self.assertEqual(started["phase"], "started")
        self.assertEqual(started["providerLabel"], "promptfoo-label")
        self.assertEqual(started["case_id"], "synthetic-case")
        self.assertEqual(started["repeatIndex"], 2)
        self.assertEqual(started["gaps"], [])
        # A stale context["provider"] label never leaks into the key: the
        # label comes only from options.
        key, label, _, _ = muse_provider._schedule_identity(
            {"label": "options-label", "config": {}},
            {"provider": {"label": "context-label"},
             "vars": {"case_id": "c"}, "test": {"metadata": {}},
             "repeatIndex": 1},
        )
        self.assertEqual(label, "options-label")
        self.assertEqual(key, "options-label|c|1")

    def test_missing_options_label_gives_null_key_and_gap(self):
        stdout = self.completed_stream()
        self.make_session()
        response, _ = self.call(stdout, provider_label=None)
        self.assertNotIn("error", response)
        self.assertIsNone(response["metadata"]["scheduleKey"])
        started = json.loads(
            (self.trace_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        self.assertEqual(started["gaps"], ["schedule-key-missing"])

    def test_missing_head_binding_is_an_error_never_output(self):
        review = json.dumps({"verdict": "APPROVE"})
        events = [started_event("run-1"), terminal_event("run-1", "completed", review)]
        stdout = "\n".join(json.dumps(event) for event in events)
        self.make_session()
        response, _ = self.call(stdout)
        self.assertIn("error", response)
        self.assertNotIn("output", response)
        self.assertEqual(response["metadata"]["headBinding"], "missing")
        self.assertEqual(response["metadata"]["completionStatus"], "completed")

    def test_partial_evidence_omits_token_usage(self):
        stdout = self.completed_stream()
        records = [
            model_completed_event(
                {"input_tokens": 10, "output_tokens": 5, "cached_tokens": 0,
                 "cache_write_tokens": 0, "cache_read_tokens": 0,
                 "reasoning_tokens": 0}
            ),
            session_record({"kind": "model_completed", "duration_ms": 3}),
        ]
        response, _ = self.call(stdout, session_records=records)
        self.assertNotIn("error", response)
        metadata = response["metadata"]
        self.assertEqual(metadata["museUsage"]["coverage"], "partial")
        self.assertIsNone(metadata["museUsage"]["total"])
        self.assertNotIn("tokenUsage", response)
        self.assertEqual(metadata["candidateTokenStatus"], "partial")

    def test_no_trace_dir_reports_ledger_not_enabled(self):
        stdout = self.completed_stream()
        self.make_session()
        response, _ = self.call(stdout, no_trace=True)
        self.assertNotIn("error", response)
        self.assertEqual(response["metadata"]["ledgerStatus"], "not-enabled")
        self.assertFalse((self.trace_dir / "attempts.jsonl").exists())

    def test_scratch_snapshot_retained_before_cleanup(self):
        self.make_session()

        def writing_run(args, workspace, timeout_seconds, env):
            Path(env["TMPDIR"], "probe.txt").write_text("probe body", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, self.completed_stream(), "")

        with mock.patch.object(
            muse_provider, "_prepare_workspace", return_value=(self.base, self.head)
        ), mock.patch.dict(
            os.environ,
            {
                "MUSE_EVAL_TRACE_DIR": str(self.trace_dir),
                "MUSE_EVAL_SESSIONS_ROOT": str(self.sessions),
            },
        ), mock.patch.object(
            muse_provider.shutil, "which", return_value="/usr/bin/muse"
        ), mock.patch.object(
            muse_provider, "_run_muse", side_effect=writing_run
        ):
            response = muse_provider.call_api(
                "review now",
                {"config": {"variant": "none", "timeout_seconds": 60,
                            "repo_root": str(self.repo)}},
                {"vars": {"base_sha": self.base, "head_sha": self.head,
                           "skill_name": "example"}},
            )
        self.assertNotIn("error", response)
        attempt_id = response["metadata"]["attemptId"]
        retained = self.trace_dir / "evidence" / attempt_id / "scratch" / "probe.txt"
        self.assertTrue(retained.is_file())
        self.assertEqual(retained.read_text(encoding="utf-8"), "probe body")

    def test_completion_failures_are_errors_never_output(self):
        matrix = {
            "process-error": (self.completed_stream(), 3, None),
            "terminal-failed": (
                "\n".join(
                    json.dumps(event)
                    for event in [
                        started_event(),
                        terminal_event("run-1", "failed", "", reason="idle timeout"),
                    ]
                ),
                0,
                None,
            ),
            "terminal-cancelled": (
                "\n".join(
                    json.dumps(event)
                    for event in [
                        started_event(),
                        terminal_event("run-1", "cancelled", "", reason="stop"),
                    ]
                ),
                0,
                None,
            ),
            "missing-terminal-delta-only": (
                "\n".join(
                    json.dumps(event)
                    for event in [started_event(), delta_event("run-1", "half")]
                ),
                0,
                None,
            ),
            "missing-trace": ("", 0, None),
            "empty-output": (
                "\n".join(
                    json.dumps(event)
                    for event in [started_event(), terminal_event("run-1", "completed", "")]
                ),
                0,
                None,
            ),
            "multiple-runs": (
                "\n".join(
                    json.dumps(event)
                    for event in [
                        started_event("run-1"),
                        started_event("run-2"),
                        terminal_event("run-1", "completed", "done"),
                    ]
                ),
                0,
                None,
            ),
            "multiple-terminals": (
                "\n".join(
                    json.dumps(event)
                    for event in [
                        started_event(),
                        terminal_event("run-1", "completed", "one"),
                        terminal_event("run-1", "completed", "two"),
                    ]
                ),
                0,
                None,
            ),
        }
        for label, (stdout, exit_code, _) in matrix.items():
            with self.subTest(label=label):
                response, _ = self.call(stdout, exit_code=exit_code)
                self.assertIn("error", response, label)
                self.assertNotIn("output", response, label)
                self.assertEqual(response["metadata"]["completionStatus"], label, label)
        with self.subTest(label="timeout"):
            exc = subprocess.TimeoutExpired(
                ["muse"], 60,
                output="\n".join(
                    json.dumps(event)
                    for event in [started_event(), delta_event("run-1", "half")]
                ),
                stderr="late",
            )
            response, _ = self.call("", timeout=exc)
            self.assertIn("timed out", response["error"])
            self.assertNotIn("output", response)
            self.assertEqual(response["metadata"]["completionStatus"], "timeout")

    def test_wrong_head_is_stale_mismatched_review(self):
        stdout = self.completed_stream(head="deadbee1234567")
        response, _ = self.call(stdout)
        self.assertIn("error", response)
        self.assertIn("stale/mismatched", response["error"])
        self.assertNotIn("output", response)
        self.assertEqual(response["metadata"]["headBinding"], "mismatch")

    def test_workspace_commit_mismatch_is_stale(self):
        def run_with_workspace(args, workspace, timeout_seconds, env):
            stdout = self.completed_stream(
                workspace="/elsewhere/checkout", commit=self.head
            )
            return subprocess.CompletedProcess(args, 0, stdout, "")

        with mock.patch.object(
            muse_provider, "_prepare_workspace", return_value=(self.base, self.head)
        ), mock.patch.dict(os.environ, {}), mock.patch.object(
            muse_provider.shutil, "which", return_value="/usr/bin/muse"
        ), mock.patch.object(
            muse_provider, "_run_muse", side_effect=run_with_workspace
        ):
            response = muse_provider.call_api(
                "review now",
                {"config": {"variant": "none", "timeout_seconds": 60,
                            "repo_root": str(self.repo)}},
                {"vars": {"base_sha": self.base, "head_sha": self.head,
                           "skill_name": "example"}},
            )
        self.assertIn("stale/mismatched", response["error"])
        self.assertEqual(response["metadata"]["sessionWorkspaceBinding"], "mismatch")

    def test_workspace_match_binds(self):
        def run_with_workspace(args, workspace, timeout_seconds, env):
            stdout = self.completed_stream(workspace=str(workspace), commit=self.head)
            return subprocess.CompletedProcess(args, 0, stdout, "")

        with mock.patch.object(
            muse_provider, "_prepare_workspace", return_value=(self.base, self.head)
        ), mock.patch.dict(os.environ, {}), mock.patch.object(
            muse_provider.shutil, "which", return_value="/usr/bin/muse"
        ), mock.patch.object(
            muse_provider, "_run_muse", side_effect=run_with_workspace
        ):
            response = muse_provider.call_api(
                "review now",
                {"config": {"variant": "none", "timeout_seconds": 60,
                            "repo_root": str(self.repo)}},
                {"vars": {"base_sha": self.base, "head_sha": self.head,
                           "skill_name": "example"}},
            )
        self.assertNotIn("error", response)
        self.assertEqual(response["metadata"]["sessionWorkspaceBinding"], "match")

    def test_write_outside_scratch_flagged_and_quarantined(self):
        stdout = self.completed_stream()
        records = [tool_call_event(write_call("c1", "/tmp/evil.txt", "x"))]
        response, _ = self.call(
            stdout, variant="current", source="gen-v-research-tools",
            session_records=records,
        )
        self.assertNotIn("error", response)
        self.assertIn(
            "write-outside-attempt-scratch", response["metadata"]["graderBoundaryFlags"]
        )
        verdict = review_contract.assert_answer_key_boundary(
            response["output"], {"metadata": response["metadata"]}
        )
        self.assertFalse(verdict["pass"])

    def test_missing_evidence_quarantines_checked_row(self):
        # A completed run whose session record was never retained: the stream
        # carries a full completed run, but no session dir exists, so evidence
        # is not-retained and the checked row is quarantined.
        stdout = self.completed_stream()
        response, _ = self.call(stdout, variant="current", source="gen-v-research-tools")
        self.assertNotIn("error", response)
        self.assertEqual(response["metadata"]["evidenceStatus"], "not-retained")
        self.assertIn("evidence-unavailable", response["metadata"]["graderBoundaryFlags"])
        self.assertFalse(
            review_contract.assert_answer_key_boundary(
                response["output"], {"metadata": response["metadata"]}
            )["pass"]
        )

    def test_ambiguous_session_ids_flagged(self):
        events = [
            dict(started_event("run-1", SESSION_ID)),
            dict(started_event("run-1", OTHER_SESSION_ID)),
            dict(
                terminal_event("run-1", "completed",
                               json.dumps({"verdict": "APPROVE", "head_sha": self.head})),
                stream={"kind": "session", "id": OTHER_SESSION_ID},
            ),
        ]
        stdout = "\n".join(json.dumps(event) for event in events)
        response, _ = self.call(stdout, variant="current", source="gen-v-research-tools")
        self.assertIn("session-record-ambiguous", response["metadata"]["evidenceGaps"])

    def test_two_session_dirs_record_ambiguous_gap(self):
        self.make_session()
        other_day = self.sessions / "2026" / "09" / "17" / SESSION_ID
        other_day.mkdir(parents=True, exist_ok=True)
        (other_day / "session.jsonl").write_text("{}\n", encoding="utf-8")
        stdout = self.completed_stream()
        response, _ = self.call(stdout, variant="current", source="gen-v-research-tools")
        self.assertNotIn("error", response)
        self.assertIn("session-record-ambiguous", response["metadata"]["evidenceGaps"])
        self.assertNotIn("session-record-missing", response["metadata"]["evidenceGaps"])

    def test_ledger_failure_keeps_review_but_quarantines(self):
        self.trace_dir.chmod(0o500)
        self.addCleanup(self.trace_dir.chmod, 0o700)
        stdout = self.completed_stream()
        response, _ = self.call(stdout, variant="current", source="gen-v-research-tools")
        self.assertNotIn("error", response)
        self.assertEqual(response["metadata"]["ledgerStatus"], "failed")
        self.assertIn("ledger-unavailable", response["metadata"]["graderBoundaryFlags"])
        self.assertIn("output", response)

    def test_attempt_integrity_assertion(self):
        good = {
            "metadata": {
                "completionStatus": "completed",
                "headBinding": "match",
                "sessionWorkspaceBinding": "match",
                "evidenceStatus": "retained",
                "ledgerStatus": "ok",
            }
        }
        self.assertTrue(review_contract.assert_attempt_integrity("", good)["pass"])
        for key, bad in (
            ("completionStatus", "timeout"),
            ("headBinding", "mismatch"),
            ("sessionWorkspaceBinding", "unknown"),
            ("evidenceStatus", "partial"),
            ("ledgerStatus", "failed"),
        ):
            with self.subTest(key=key):
                context = {"metadata": {**good["metadata"], key: bad}}
                self.assertFalse(review_contract.assert_attempt_integrity("", context)["pass"])
        # The future integrity gate is not wired into any shipped config.
        config_text = (experiment.ROOT / experiment.SCREEN_V3_CONFIG).read_text(
            encoding="utf-8"
        )
        self.assertNotIn("assert_attempt_integrity", config_text)

    def test_error_rows_never_approve_in_scoring(self):
        stdout = self.completed_stream(head="deadbee1234567")
        response, _ = self.call(stdout)
        self.assertIn("error", response)
        raw = {
            "provider": {"label": "synthetic-provider"},
            "metadata": {"case_id": "synthetic-case", "split": "development"},
            "vars": {
                "expected_verdict": "NEEDS_FIXES",
                "gold_findings": "1. defect (blocking): concrete failure",
            },
            "namedScores": {name: 1 for name in scoring.DETERMINISTIC_NAMED_SCORES},
            "response": {"error": response["error"], "metadata": response["metadata"]},
        }
        (row,) = scoring.promptfoo_rows({"results": {"results": [raw]}}, "deterministic")
        self.assertFalse(row["completion"])
        self.assertIsNone(row["actual_verdict"])
        scored = scoring.score_row(row)
        self.assertFalse(scored["false_approval"])
        self.assertIsNone(scored["verdict_accuracy"])


def deterministic_row(provider, case, verdict, expected="NEEDS_FIXES", flags=(),
                      boundary=1, attempt=None, completion_status=None, error=None):
    metadata = {"candidateId": provider, "graderBoundaryFlags": list(flags)}
    if attempt is not None:
        metadata["attemptId"] = attempt
    if completion_status is not None:
        metadata["completionStatus"] = completion_status
    if error is not None:
        return {
            "provider": {"label": provider},
            "metadata": {"case_id": case, "split": "development"},
            "vars": {"expected_verdict": expected,
                     "gold_findings": "1. x (should-fix): y"},
            "error": error,
            "response": {"error": error, "metadata": metadata},
        }
    scores = {name: 1 for name in scoring.DETERMINISTIC_NAMED_SCORES}
    scores["answer_key_boundary"] = boundary
    return {
        "provider": {"label": provider},
        "metadata": {"case_id": case, "split": "development"},
        "vars": {"expected_verdict": expected,
                 "gold_findings": "1. x (should-fix): y"},
        "namedScores": scores,
        "response": {
            "output": json.dumps({"verdict": verdict}),
            "metadata": metadata,
        },
    }


class AccountingTests(unittest.TestCase):
    SCHEDULE = {"stage": "synthetic", "providers": ["p1", "p2"],
                "cases": ["c1", "c2"], "repeat": 1}

    def test_two_by_two_accounts_every_slot(self):
        rows = [
            deterministic_row("p1", "c1", None, error="Muse timed out after 60s",
                              attempt="attempt-e1", completion_status="timeout"),
            deterministic_row("p1", "c2", "NEEDS_FIXES", flags=["gold_findings"],
                              boundary=0, attempt="attempt-q1"),
            deterministic_row("p3", "c1", "APPROVE", attempt="attempt-x1"),
        ]
        ledger = [
            {"phase": "started", "scheduleKey": "p1|c1|null", "attemptId": "attempt-e1"},
            {"phase": "finished", "scheduleKey": "p1|c1|null", "attemptId": "attempt-e1",
             "completionStatus": "timeout", "error": "Muse timed out after 60s"},
            {"phase": "started", "scheduleKey": "p1|c2|null", "attemptId": "attempt-q1"},
            {"phase": "finished", "scheduleKey": "p1|c2|null", "attemptId": "attempt-q1",
             "completionStatus": "completed", "error": None},
            {"phase": "started", "scheduleKey": "p2|c2|null", "attemptId": "attempt-s1"},
        ]
        result = scoring.account_attempts(
            self.SCHEDULE, ledger, {"results": {"results": rows}}
        )
        accounting = result["accounting"]
        self.assertEqual(accounting["id"], "muse-attempt-accounting-v1")
        by_slot = {(slot["provider"], slot["case"]): slot for slot in accounting["slots"]}
        self.assertEqual(len(accounting["slots"]), 4)
        self.assertTrue(by_slot["p1", "c1"]["status"].startswith("error:"))
        self.assertIn("timeout", by_slot["p1", "c1"]["status"])
        self.assertEqual(by_slot["p1", "c1"]["attemptId"], "attempt-e1")
        self.assertEqual(by_slot["p1", "c2"]["status"], "quarantined")
        # Original deterministic flags travel verbatim.
        self.assertEqual(by_slot["p1", "c2"]["flags"], ["gold_findings"])
        self.assertTrue(by_slot["p1", "c2"]["deterministic"]["quarantined"])
        self.assertEqual(by_slot["p2", "c1"]["status"], "missing")
        self.assertIsNone(by_slot["p2", "c1"]["attemptId"])
        self.assertEqual(by_slot["p2", "c2"]["status"], "started-not-finished")
        self.assertEqual(by_slot["p2", "c2"]["attemptId"], "attempt-s1")
        self.assertEqual(len(accounting["extras"]), 1)
        self.assertEqual(accounting["extras"][0]["status"], "unscheduled-extra")
        self.assertEqual(accounting["extras"][0]["provider"], "p3")
        totals = accounting["totals"]
        self.assertEqual(totals["quarantined"], 1)
        self.assertEqual(totals["missing"], 1)
        self.assertEqual(totals["started-not-finished"], 1)
        self.assertEqual(totals["unscheduled-extra"], 1)
        self.assertEqual(sum(totals.values()), 5)

    def test_missing_output_accounts_from_ledger_alone(self):
        ledger = [
            {"phase": "started", "scheduleKey": "p1|c1|0", "attemptId": "attempt-s1"},
        ]
        result = scoring.account_attempts(self.SCHEDULE, ledger, None)
        statuses = [slot["status"] for slot in result["accounting"]["slots"]]
        self.assertEqual(statuses.count("missing"), 3)
        self.assertEqual(statuses.count("started-not-finished"), 1)

    def test_repeat_two_slots_never_share_rows(self):
        schedule = {"stage": "synthetic", "providers": ["p1"], "cases": ["c1"],
                    "repeat": 2}
        rows = [
            deterministic_row("p1", "c1", "APPROVE", attempt="attempt-a"),
            deterministic_row("p1", "c1", "NEEDS_FIXES", attempt="attempt-b"),
        ]
        ledger = [
            {"phase": "started", "attemptId": "attempt-a", "scheduleKey": "p1|c1|0",
             "providerLabel": "p1", "case_id": "c1", "repeatIndex": 0},
            {"phase": "finished", "attemptId": "attempt-a", "scheduleKey": "p1|c1|0",
             "providerLabel": "p1", "case_id": "c1", "repeatIndex": 0,
             "completionStatus": "completed", "error": None},
            {"phase": "started", "attemptId": "attempt-b", "scheduleKey": "p1|c1|1",
             "providerLabel": "p1", "case_id": "c1", "repeatIndex": 1},
            {"phase": "finished", "attemptId": "attempt-b", "scheduleKey": "p1|c1|1",
             "providerLabel": "p1", "case_id": "c1", "repeatIndex": 1,
             "completionStatus": "completed", "error": None},
        ]
        result = scoring.account_attempts(
            schedule, ledger, {"results": {"results": rows}}
        )
        slots = result["accounting"]["slots"]
        self.assertEqual(len(slots), 2)
        by_repeat = {slot["repeat"]: slot for slot in slots}
        # Each repeat keeps its own attempt: the first row is never reused.
        self.assertEqual(by_repeat[0]["attemptId"], "attempt-a")
        self.assertEqual(by_repeat[1]["attemptId"], "attempt-b")
        self.assertEqual(by_repeat[0]["status"], "completed")
        self.assertEqual(by_repeat[1]["status"], "completed")
        self.assertEqual(result["accounting"]["duplicateSlots"], 0)
        self.assertEqual(result["accounting"]["extras"], [])

    def test_repeat_two_missing_and_unfinished_slots(self):
        schedule = {"stage": "synthetic", "providers": ["p1"], "cases": ["c1"],
                    "repeat": 2}
        rows = [deterministic_row("p1", "c1", "APPROVE", attempt="attempt-a")]
        ledger = [
            {"phase": "started", "attemptId": "attempt-a", "scheduleKey": "p1|c1|0",
             "providerLabel": "p1", "case_id": "c1", "repeatIndex": 0},
            {"phase": "finished", "attemptId": "attempt-a", "scheduleKey": "p1|c1|0",
             "providerLabel": "p1", "case_id": "c1", "repeatIndex": 0,
             "completionStatus": "completed", "error": None},
            {"phase": "started", "attemptId": "attempt-b", "scheduleKey": "p1|c1|1",
             "providerLabel": "p1", "case_id": "c1", "repeatIndex": 1},
        ]
        result = scoring.account_attempts(
            schedule, ledger, {"results": {"results": rows}}
        )
        by_repeat = {slot["repeat"]: slot for slot in result["accounting"]["slots"]}
        self.assertEqual(by_repeat[0]["status"], "completed")
        self.assertEqual(by_repeat[0]["attemptId"], "attempt-a")
        # The unfinished repeat keeps its own ledger attempt id instead of
        # reusing the finished repeat's row.
        self.assertEqual(by_repeat[1]["status"], "started-not-finished")
        self.assertEqual(by_repeat[1]["attemptId"], "attempt-b")

    def test_repeat_two_duplicate_attempts_reported_not_merged(self):
        schedule = {"stage": "synthetic", "providers": ["p1"], "cases": ["c1"],
                    "repeat": 1}
        rows = [
            deterministic_row("p1", "c1", "APPROVE", attempt="attempt-a"),
            deterministic_row("p1", "c1", "APPROVE", attempt="attempt-c"),
        ]
        ledger = [
            {"phase": "started", "attemptId": "attempt-a", "scheduleKey": "p1|c1|0",
             "providerLabel": "p1", "case_id": "c1", "repeatIndex": 0},
            {"phase": "finished", "attemptId": "attempt-a", "scheduleKey": "p1|c1|0",
             "providerLabel": "p1", "case_id": "c1", "repeatIndex": 0,
             "completionStatus": "completed", "error": None},
            {"phase": "started", "attemptId": "attempt-c", "scheduleKey": "p1|c1|0",
             "providerLabel": "p1", "case_id": "c1", "repeatIndex": 0},
            {"phase": "finished", "attemptId": "attempt-c", "scheduleKey": "p1|c1|0",
             "providerLabel": "p1", "case_id": "c1", "repeatIndex": 0,
             "completionStatus": "completed", "error": None},
        ]
        result = scoring.account_attempts(
            schedule, ledger, {"results": {"results": rows}}
        )
        (slot,) = result["accounting"]["slots"]
        self.assertEqual(slot["status"], "completed")
        self.assertEqual(slot["attemptId"], "attempt-a")
        self.assertEqual(slot["duplicateAttemptIds"], ["attempt-c"])
        self.assertEqual(slot["duplicateRows"], 1)
        self.assertEqual(result["accounting"]["duplicateSlots"], 1)
        self.assertEqual(result["accounting"]["extras"], [])

    def test_error_names_message_not_completed_status(self):
        rows = [
            deterministic_row(
                "p1", "c1", None,
                error="stale/mismatched review: reported head 'x' is stale",
                attempt="attempt-e1", completion_status="completed",
            ),
        ]
        ledger = [
            {"phase": "started", "scheduleKey": "p1|c1|null", "attemptId": "attempt-e1"},
            {"phase": "finished", "scheduleKey": "p1|c1|null", "attemptId": "attempt-e1",
             "completionStatus": "completed", "error": "stale/mismatched review"},
        ]
        result = scoring.account_attempts(
            self.SCHEDULE, ledger, {"results": {"results": rows}}
        )
        by_slot = {(slot["provider"], slot["case"]): slot
                   for slot in result["accounting"]["slots"]}
        status = by_slot["p1", "c1"]["status"]
        self.assertTrue(status.startswith("error:"))
        self.assertNotEqual(status, "error:completed")
        self.assertIn("stale/mismatched", status)

    def test_accounting_cli_writes_file(self):
        with tempfile.TemporaryDirectory() as temp:
            schedule_path = Path(temp) / "schedule.json"
            schedule_path.write_text(json.dumps(self.SCHEDULE), encoding="utf-8")
            out_path = Path(temp) / "accounting.json"
            result = subprocess.run(
                [sys.executable, "evals/behavioral/scoring.py",
                 "--schedule", str(schedule_path),
                 "--accounting-output", str(out_path)],
                cwd=experiment.ROOT,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["accounting"]["id"], "muse-attempt-accounting-v1")
            self.assertEqual(len(payload["accounting"]["slots"]), 4)

    def test_accounting_cli_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            schedule_path = Path(temp) / "schedule.json"
            schedule_path.write_text(json.dumps(self.SCHEDULE), encoding="utf-8")
            out_path = Path(temp) / "accounting.json"
            out_path.write_text("preserve me\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "evals/behavioral/scoring.py",
                 "--schedule", str(schedule_path),
                 "--accounting-output", str(out_path)],
                cwd=experiment.ROOT,
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(out_path.read_text(encoding="utf-8"), "preserve me\n")


def readout_slot(provider, case, status, attempt, actual, quarantined=False,
                 flags=None, error=None):
    return {
        "provider": provider,
        "case": case,
        "repeat": 0,
        "status": status,
        "attemptId": attempt,
        "ledgerPhase": "finished" if status != "missing" else None,
        "deterministic": {
            "actual_verdict": actual,
            "completion": status in {"completed", "quarantined"},
            "quarantined": quarantined,
            "grader_boundary_flags": list(flags or []),
            "error": error,
        },
        "flags": list(flags or []),
        "error": error,
        "completion": status in {"completed", "quarantined"},
    }


READOUT_KEY_MAP = {
    "key_id": "synthetic-key",
    "key_sha256": "0" * 64,
    "cases": {
        "c1": {
            "expected_verdict": "NEEDS_FIXES",
            "findings": [
                {"id": "k1", "severity": "blocking"},
                {"id": "k2", "severity": "should-fix"},
                {"id": "k3", "severity": "low"},
            ],
        },
        "c2": {
            "expected_verdict": "NEEDS_FIXES",
            "findings": [{"id": "k4", "severity": "blocking"}],
        },
        "c3": {
            "expected_verdict": "APPROVE",
            "findings": [{"id": "k5", "severity": "low"}],
        },
        "c4": {
            "expected_verdict": "APPROVE",
            "findings": [{"id": "k6", "severity": "should-fix"}],
        },
        "c5": {
            "expected_verdict": "NEEDS_FIXES",
            "findings": [{"id": "k7", "severity": "blocking"}],
        },
    },
}


def readout_accounting():
    return {
        "accounting": {
            "id": "muse-attempt-accounting-v1",
            "stage": "synthetic",
            "schedule": {"providers": ["p1", "p2"]},
            "slots": [
                readout_slot("p1", "c1", "quarantined", "A1", "NEEDS_FIXES",
                             quarantined=True, flags=["evidence-unavailable"]),
                readout_slot("p1", "c2", "completed", "A2", "APPROVE"),
                readout_slot("p2", "c1", "error:timeout", "A3", None,
                             error="Muse timed out after 60s"),
                readout_slot("p2", "c3", "completed", "A4", "APPROVE"),
                readout_slot("p1", "c4", "completed", "A5", "NEEDS_FIXES"),
                readout_slot("p2", "c4", "completed", "A6", "NEEDS_FIXES"),
                readout_slot("p2", "c5", "completed", "A7", "APPROVE"),
            ],
        }
    }


def readout_adjudication():
    return {
        "adjudication_id": "owner-1",
        "decided_by": "owner",
        "attempts": {
            "A1": {
                "quarantine": "cleared",
                "rationale": "evidence re-attached",
                "findings": [
                    {"review_index": 0, "status": "matched", "key_id": "k1",
                     "severity": "blocking"},
                    {"review_index": 1, "status": "matched", "key_id": "k2",
                     "severity": "should-fix"},
                    {"review_index": 2, "status": "matched", "key_id": "k3",
                     "severity": "low"},
                ],
            },
            "A2": {"quarantine": "confirmed-exposure", "rationale": "leak",
                   "findings": []},
            "A4": {
                "quarantine": None,
                "rationale": "",
                "findings": [
                    {"review_index": 0, "status": "matched", "key_id": "k5",
                     "severity": "low"},
                ],
            },
            "A5": {
                "quarantine": None,
                "rationale": "",
                "findings": [
                    {"review_index": 0, "status": "novel-valid", "key_id": None,
                     "severity": "should-fix"},
                ],
            },
            "A6": {"quarantine": None, "rationale": "", "findings": []},
            "A7": {"quarantine": None, "rationale": "", "findings": []},
        },
    }


class ReadoutTests(unittest.TestCase):
    def result(self):
        return scoring.adjudicated_readout(
            readout_accounting(), READOUT_KEY_MAP, readout_adjudication()
        )["readout"]

    def by_attempt(self, readout):
        return {entry["attemptId"]: entry for entry in readout["attempts"]}

    def test_cleared_quarantine_keeps_original_flag_and_score(self):
        entry = self.by_attempt(self.result())["A1"]
        self.assertTrue(entry["eligible"])
        # The original deterministic flag and verdict travel verbatim.
        self.assertTrue(entry["deterministic"]["quarantined"])
        self.assertEqual(
            entry["deterministic"]["grader_boundary_flags"], ["evidence-unavailable"]
        )
        self.assertEqual(entry["deterministic"]["actual_verdict"], "NEEDS_FIXES")
        self.assertEqual(entry["adjudicated"]["quarantine"], "cleared")
        self.assertEqual(entry["adjudicated"]["consequential_recall"], 1.0)
        # Optional low matches never enter recall either side.
        self.assertEqual(entry["adjudicated"]["optional_low_matched"], 1)
        self.assertFalse(entry["adjudicated"]["false_approval"])

    def test_confirmed_exposure_excluded_and_timeout_ineligible(self):
        entries = self.by_attempt(self.result())
        self.assertFalse(entries["A2"]["eligible"])
        self.assertEqual(entries["A2"]["ineligible_reason"], "confirmed-exposure")
        self.assertIsNone(entries["A2"]["adjudicated"]["consequential_recall"])
        self.assertFalse(entries["A3"]["eligible"])
        self.assertEqual(entries["A3"]["ineligible_reason"], "incomplete")
        # Incomplete attempts are no useful delivery, never approval.
        self.assertFalse(entries["A3"]["adjudicated"]["false_approval"])
        self.assertFalse(entries["A3"]["adjudicated"]["false_block"])

    def test_low_only_case_recall_is_na(self):
        entry = self.by_attempt(self.result())["A4"]
        self.assertTrue(entry["eligible"])
        self.assertIsNone(entry["adjudicated"]["consequential_recall"])
        self.assertEqual(entry["adjudicated"]["optional_low_matched"], 1)

    def test_novel_valid_consequential_blocks_false_block(self):
        entries = self.by_attempt(self.result())
        self.assertEqual(entries["A5"]["adjudicated"]["novel_valid"], 1)
        self.assertFalse(entries["A5"]["adjudicated"]["false_block"])
        self.assertTrue(entries["A6"]["adjudicated"]["false_block"])
        # A NEEDS_FIXES-expected APPROVE without matches is a false approval.
        self.assertTrue(entries["A7"]["adjudicated"]["false_approval"])
        self.assertEqual(entries["A7"]["adjudicated"]["consequential_recall"], 0.0)

    def test_provider_aggregates(self):
        providers = self.result()["providers"]
        p1 = providers["p1"]
        self.assertEqual(p1["eligible_attempts"], 2)
        self.assertEqual(p1["scheduled_attempts"], 3)
        self.assertAlmostEqual(p1["completed_review_recall"], 0.5)
        self.assertAlmostEqual(p1["useful_delivery"], 0.5)
        self.assertEqual(p1["novel_valid"], 1)
        self.assertEqual(p1["exclusions"], 1)
        self.assertEqual(
            p1["quarantines_by_decision"], {"cleared": 1, "confirmed-exposure": 1}
        )
        p2 = providers["p2"]
        self.assertEqual(p2["eligible_attempts"], 3)
        self.assertEqual(p2["scheduled_attempts"], 4)
        # The timeout counts in the delivery denominator, never in recall.
        self.assertAlmostEqual(p2["completed_review_recall"], 0.0)
        self.assertAlmostEqual(p2["useful_delivery"], 0.0)
        self.assertEqual(p2["false_approvals"], 1)
        self.assertEqual(p2["false_blocks"], 1)

    def test_recall_dedupes_and_intersects_key_ids(self):
        accounting = {
            "accounting": {
                "id": "muse-attempt-accounting-v1",
                "stage": "synthetic",
                "schedule": {"providers": ["p9"]},
                "slots": [readout_slot("p9", "c1", "completed", "B1", "NEEDS_FIXES")],
            }
        }
        adjudication = {
            "adjudication_id": "owner-9",
            "decided_by": "owner",
            "attempts": {
                "B1": {
                    "quarantine": None,
                    "rationale": "",
                    "findings": [
                        {"review_index": 0, "status": "matched", "key_id": "k1",
                         "severity": "blocking"},
                        {"review_index": 1, "status": "matched", "key_id": "k1",
                         "severity": "blocking"},
                        {"review_index": 2, "status": "matched", "key_id": "unknown-kx",
                         "severity": "blocking"},
                        {"review_index": 3, "status": "matched", "key_id": "k3",
                         "severity": "low"},
                    ],
                },
            },
        }
        (entry,) = scoring.adjudicated_readout(
            accounting, READOUT_KEY_MAP, adjudication
        )["readout"]["attempts"]
        # Duplicates collapse and non-key matches never inflate recall: only
        # distinct matched key ids intersected with the consequential set.
        self.assertEqual(entry["adjudicated"]["matched_consequential"], 1)
        self.assertAlmostEqual(entry["adjudicated"]["consequential_recall"], 0.5)
        self.assertEqual(entry["adjudicated"]["optional_low_matched"], 1)

    def test_missing_adjudication_is_null_and_counted(self):
        accounting = {
            "accounting": {
                "id": "muse-attempt-accounting-v1",
                "stage": "synthetic",
                "schedule": {"providers": ["p9"]},
                "slots": [
                    readout_slot("p9", "c1", "completed", "B1", "NEEDS_FIXES"),
                    readout_slot("p9", "c1", "completed", "B2", "NEEDS_FIXES"),
                    readout_slot("p9", "c1", "quarantined", "B3", "NEEDS_FIXES",
                                 quarantined=True),
                ],
            }
        }
        adjudication = {
            "adjudication_id": "owner-9",
            "decided_by": "owner",
            "attempts": {
                "B1": {
                    "quarantine": None,
                    "rationale": "",
                    "findings": [
                        {"review_index": 0, "status": "matched", "key_id": "k1",
                         "severity": "blocking"},
                    ],
                },
                "B3": {
                    "quarantine": "confirmed-exposure",
                    "rationale": "leak",
                    "findings": [
                        {"review_index": 0, "status": "matched", "key_id": "k1",
                         "severity": "blocking"},
                        {"review_index": 1, "status": "matched", "key_id": "k2",
                         "severity": "should-fix"},
                    ],
                },
            },
        }
        readout = scoring.adjudicated_readout(
            accounting, READOUT_KEY_MAP, adjudication
        )["readout"]
        by_attempt = {entry["attemptId"]: entry for entry in readout["attempts"]}
        # An eligible attempt with no adjudication record is missing with a
        # null recall, never a miss.
        self.assertEqual(by_attempt["B2"]["adjudicated"]["adjudication"], "missing")
        self.assertIsNone(by_attempt["B2"]["adjudicated"]["consequential_recall"])
        # The excluded attempt's matches contribute nothing to delivery.
        self.assertFalse(by_attempt["B3"]["eligible"])
        self.assertEqual(by_attempt["B3"]["adjudicated"]["matched_consequential"], 0)
        provider = readout["providers"]["p9"]
        self.assertEqual(provider["not_adjudicated"], 1)
        self.assertAlmostEqual(provider["completed_review_recall"], 0.5)
        self.assertAlmostEqual(provider["useful_delivery"], 1 / 6)

    def test_readout_refuses_to_overwrite_output(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            accounting = temp_path / "accounting.json"
            key_map = temp_path / "key.json"
            adjudication = temp_path / "adjudication.json"
            output = temp_path / "readout.json"
            accounting.write_text(json.dumps(readout_accounting()), encoding="utf-8")
            key_map.write_text(json.dumps(READOUT_KEY_MAP), encoding="utf-8")
            adjudication.write_text(json.dumps(readout_adjudication()), encoding="utf-8")
            output.write_text("preserve me\n", encoding="utf-8")
            code = scoring.readout_main(
                ["--accounting", str(accounting), "--key-map", str(key_map),
                 "--adjudication", str(adjudication), "--output", str(output)]
            )
            self.assertNotEqual(code, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "preserve me\n")
            output.unlink()
            code = scoring.readout_main(
                ["--accounting", str(accounting), "--key-map", str(key_map),
                 "--adjudication", str(adjudication), "--output", str(output)]
            )
            self.assertEqual(code, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["readout"]["id"], "muse-adjudicated-readout-v1")
            # Readout inputs are never rewritten.
            self.assertEqual(
                json.loads(accounting.read_text(encoding="utf-8")), readout_accounting()
            )


class RunnerAccountingTests(unittest.TestCase):
    def test_runner_writes_accounting_before_nonzero_exit(self):
        output = experiment.ROOT / "evals/behavioral/results/evidence-claims-v3-screen.json"
        artifacts = [output, *(Path(f"{output}{suffix}") for suffix in (
            ".metrics-v2.json", ".reservation.json", ".promptfoo", ".traces",
            ".schedule.json", ".accounting-v1.json"))]
        for artifact in artifacts:
            self.assertFalse(artifact.exists(), f"test refuses existing user artifact {artifact}")
        rows = []
        errored = False
        for label in ("screen-evidence-claims-v2", "screen-evidence-claims-v3"):
            for case in experiment._load(experiment.DEVELOPMENT_V3_CASES):
                if not errored:
                    rows.append(deterministic_row(
                        label, case["metadata"]["case_id"], None,
                        error="Muse timed out after 540s",
                        completion_status="timeout",
                    ))
                    errored = True
                    continue
                verdict = case["vars"]["expected_verdict"]
                rows.append(deterministic_row(label, case["metadata"]["case_id"], verdict))
        with tempfile.TemporaryDirectory() as temp:
            canned = Path(temp) / "canned.json"
            canned.write_text(json.dumps({"results": {"results": rows}}), encoding="utf-8")
            fake_bin = Path(temp) / "bin"
            fake_bin.mkdir()
            promptfoo = fake_bin / "promptfoo"
            promptfoo.write_text(
                '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
                'cp "$FAKE_CANNED" "$out"\nexit 100\n',
                encoding="utf-8",
            )
            promptfoo.chmod(0o700)
            try:
                result = subprocess.run(
                    ["node", "evals/behavioral/run.mjs", "evidence-claims-v3-screen"],
                    cwd=experiment.ROOT, capture_output=True, text=True, check=False,
                    env={
                        **os.environ,
                        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                        "SPIKE_PYTHON": sys.executable,
                        "FAKE_CANNED": str(canned),
                        "MUSE_EVAL_WORKSPACE_BASE": str(Path(temp) / "outside"),
                    },
                )
                # The error slot fails the stage, but only after accounting lands.
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                schedule = json.loads(
                    Path(f"{output}.schedule.json").read_text(encoding="utf-8")
                )
                self.assertEqual(schedule["accounting"], "muse-attempt-accounting-v1")
                self.assertEqual(schedule["expectedRows"], 6)
                accounting = json.loads(
                    Path(f"{output}.accounting-v1.json").read_text(encoding="utf-8")
                )
                self.assertEqual(accounting["accounting"]["id"], "muse-attempt-accounting-v1")
                statuses = [slot["status"] for slot in accounting["accounting"]["slots"]]
                self.assertEqual(len(statuses), 6)
                self.assertTrue(any(status.startswith("error:") for status in statuses))
                self.assertIn("error:timeout", statuses)
            finally:
                for artifact in artifacts:
                    if artifact.is_dir():
                        shutil.rmtree(artifact, ignore_errors=True)
                    else:
                        artifact.unlink(missing_ok=True)


    def test_runner_writes_accounting_on_promptfoo_exit_1(self):
        output = experiment.ROOT / "evals/behavioral/results/evidence-claims-v3-screen.json"
        artifacts = [output, *(Path(f"{output}{suffix}") for suffix in (
            ".metrics-v2.json", ".reservation.json", ".promptfoo", ".traces",
            ".schedule.json", ".accounting-v1.json"))]
        for artifact in artifacts:
            self.assertFalse(artifact.exists(), f"test refuses existing user artifact {artifact}")
        rows = []
        for label in ("screen-evidence-claims-v2", "screen-evidence-claims-v3"):
            for case in experiment._load(experiment.DEVELOPMENT_V3_CASES):
                verdict = case["vars"]["expected_verdict"]
                rows.append(deterministic_row(label, case["metadata"]["case_id"], verdict))
        with tempfile.TemporaryDirectory() as temp:
            canned = Path(temp) / "canned.json"
            canned.write_text(json.dumps({"results": {"results": rows}}), encoding="utf-8")
            fake_bin = Path(temp) / "bin"
            fake_bin.mkdir()
            promptfoo = fake_bin / "promptfoo"
            promptfoo.write_text(
                '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
                'cp "$FAKE_CANNED" "$out"\nexit 1\n',
                encoding="utf-8",
            )
            promptfoo.chmod(0o700)
            try:
                result = subprocess.run(
                    ["node", "evals/behavioral/run.mjs", "evidence-claims-v3-screen"],
                    cwd=experiment.ROOT, capture_output=True, text=True, check=False,
                    env={
                        **os.environ,
                        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                        "SPIKE_PYTHON": sys.executable,
                        "FAKE_CANNED": str(canned),
                        "MUSE_EVAL_WORKSPACE_BASE": str(Path(temp) / "outside"),
                    },
                )
                # A non-completed Promptfoo exit still lands accounting first.
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                accounting = json.loads(
                    Path(f"{output}.accounting-v1.json").read_text(encoding="utf-8")
                )
                self.assertEqual(accounting["accounting"]["id"], "muse-attempt-accounting-v1")
                self.assertEqual(len(accounting["accounting"]["slots"]), 6)
            finally:
                for artifact in artifacts:
                    if artifact.is_dir():
                        shutil.rmtree(artifact, ignore_errors=True)
                    else:
                        artifact.unlink(missing_ok=True)

    def test_runner_writes_accounting_when_promptfoo_missing(self):
        output = experiment.ROOT / "evals/behavioral/results/evidence-claims-v3-screen.json"
        artifacts = [output, *(Path(f"{output}{suffix}") for suffix in (
            ".metrics-v2.json", ".reservation.json", ".promptfoo", ".traces",
            ".schedule.json", ".accounting-v1.json"))]
        for artifact in artifacts:
            self.assertFalse(artifact.exists(), f"test refuses existing user artifact {artifact}")
        with tempfile.TemporaryDirectory() as temp:
            # A PATH with only the directories needed for node/python/git:
            # no promptfoo binary, so the spawn fails with ENOENT.
            dirs = {shutil.which("node"), sys.executable, shutil.which("git")}
            path = os.pathsep.join(
                sorted({str(Path(tool).parent) for tool in dirs if tool})
            )
            self.assertNotIn("promptfoo", path)
            try:
                result = subprocess.run(
                    ["node", "evals/behavioral/run.mjs", "evidence-claims-v3-screen"],
                    cwd=experiment.ROOT, capture_output=True, text=True, check=False,
                    env={
                        **os.environ,
                        "PATH": path,
                        "SPIKE_PYTHON": sys.executable,
                        "MUSE_EVAL_WORKSPACE_BASE": str(Path(temp) / "outside"),
                    },
                )
                # The missing binary fails the stage, but only after the
                # accounting file is written from the schedule alone.
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                accounting = json.loads(
                    Path(f"{output}.accounting-v1.json").read_text(encoding="utf-8")
                )
                slots = accounting["accounting"]["slots"]
                self.assertEqual(len(slots), 6)
                self.assertTrue(all(slot["status"] == "missing" for slot in slots))
            finally:
                for artifact in artifacts:
                    if artifact.is_dir():
                        shutil.rmtree(artifact, ignore_errors=True)
                    else:
                        artifact.unlink(missing_ok=True)

    def test_has_complete_accounting_checks_slot_statuses(self):
        script = (
            "import { hasCompleteAccounting } from './evals/behavioral/selection.mjs';\n"
            "const ok = { accounting: { id: 'muse-attempt-accounting-v1', slots: [\n"
            "  { status: 'completed' }, { status: 'quarantined' } ] } };\n"
            "const bad = { accounting: { id: 'muse-attempt-accounting-v1', slots: [\n"
            "  { status: 'completed' }, { status: 'missing' } ] } };\n"
            "const wrongId = { accounting: { id: 'other', slots: [\n"
            "  { status: 'completed' } ] } };\n"
            "if (!hasCompleteAccounting(ok, 2)) { console.error('completed rejected'); process.exit(1); }\n"
            "if (hasCompleteAccounting(bad, 2)) { console.error('missing accepted'); process.exit(1); }\n"
            "if (hasCompleteAccounting(wrongId, 1)) { console.error('wrong id accepted'); process.exit(1); }\n"
            "if (hasCompleteAccounting(ok, 3)) { console.error('wrong row count accepted'); process.exit(1); }\n"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=experiment.ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ProtectedIdentityTests(unittest.TestCase):
    PROTECTED_SHA256 = {
        "evals/behavioral/candidates/evidence-claims-v2.md":
            "feea0437ae4f5218a6795d11fd2f31404cc445308159f8946c38b897d87629f6",
        "evals/behavioral/candidates/evidence-claims-v3.md":
            "d357350e83f01429f97aa5903404ffe9342677bb16e39242024878958dfc4477",
        "evals/behavioral/evidence-claims-v3-screen-promptfooconfig.yaml":
            "132bd9b66cf1aab82154931d9b0ba7f397eb01424176f468b3c347293fc75e59",
    }
    BASE = "8ecb7c2b89d297bfa2fe028b40fd5c75279baa9f"
    PROTECTED_PATHS = [
        "skills",
        "evals/behavioral/candidates",
        "evals/behavioral/cases",
        "evals/behavioral/answer-keys",
        "evals/behavioral/reports",
        "evals/behavioral/evidence-claims-v3-screen-promptfooconfig.yaml",
        "evals/behavioral/experiment.py",
    ]

    def test_historical_bytes_are_identical(self):
        for rel, expected in self.PROTECTED_SHA256.items():
            digest = hashlib.sha256(
                (experiment.ROOT / rel).read_bytes()
            ).hexdigest()
            self.assertEqual(digest, expected, rel)

    def test_production_skills_match_base(self):
        for skill in ("adversarial-review", "fix-verification"):
            rel = f"skills/{skill}/SKILL.md"
            base = subprocess.run(
                ["git", "show", f"{self.BASE}:{rel}"],
                cwd=experiment.ROOT, capture_output=True, check=True,
            ).stdout
            worktree = (experiment.ROOT / rel).read_bytes()
            self.assertEqual(worktree, base, rel)

    def test_protected_paths_have_no_diff_from_base(self):
        result = subprocess.run(
            ["git", "diff", "--name-only", self.BASE, "--", *self.PROTECTED_PATHS],
            cwd=experiment.ROOT, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
