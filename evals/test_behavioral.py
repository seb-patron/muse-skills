"""Offline regression tests for the behavioral provider and assertions."""

import copy
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

from behavioral.assertions import review_contract
from behavioral.providers import codex_provider, muse_provider
from behavioral import experiment, scoring


class BehavioralProviderTests(unittest.TestCase):
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

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True, check=True)

    def test_prepares_isolated_current_skill_workspace(self):
        destination = Path(self.temp.name) / "fixture"
        base, head = muse_provider._prepare_workspace(
            self.repo, destination, self.base, self.head, "current", "example"
        )
        self.assertEqual(base, self.base)
        self.assertEqual(head, self.head)
        self.assertEqual((destination / "value.txt").read_text(), "head\n")
        self.assertTrue((destination / ".agents/skills/example/SKILL.md").is_file())
        status = subprocess.run(
            ["git", "status", "--short"], cwd=destination, capture_output=True, text=True, check=True
        )
        self.assertEqual(status.stdout, "")

    def test_prepares_instruction_free_control_shadow(self):
        destination = Path(self.temp.name) / "control"
        muse_provider._prepare_workspace(
            self.repo, destination, self.base, self.head, "none", "example"
        )
        skill = (destination / ".agents/skills/example/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Evaluation control placeholder", skill)
        self.assertNotIn("example skill", skill)
        status = subprocess.run(
            ["git", "status", "--short"], cwd=destination, capture_output=True, text=True, check=True
        )
        self.assertEqual(status.stdout, "")

    def test_prepares_hashed_spike_candidate(self):
        destination = Path(self.temp.name) / "candidate"
        with mock.patch.dict(muse_provider.CANDIDATE_PATHS, {"risk-first": Path("skills/example/SKILL.md")}):
            base, head = muse_provider._prepare_workspace(
                self.repo, destination, self.base, self.head, "candidate", "example", "risk-first"
            )
        self.assertEqual((base, head), (self.base, self.head))
        self.assertEqual(
            (destination / ".agents/skills/example/SKILL.md").read_text(encoding="utf-8"),
            "example skill\n",
        )
        with mock.patch.dict(muse_provider.CANDIDATE_PATHS, {"risk-first": Path("skills/example/SKILL.md")}):
            identity = muse_provider._candidate_identity(
                self.repo, "candidate", "example", "risk-first"
            )
        self.assertEqual(identity["candidateId"], "risk-first")
        self.assertRegex(str(identity["candidateSha256"]), r"^[0-9a-f]{64}$")
        with mock.patch.dict(muse_provider.CANDIDATE_PATHS, {"risk-first": Path("skills/example/SKILL.md")}):
            with self.assertRaises(muse_provider.ProviderError):
                muse_provider._candidate_identity(
                    self.repo, "candidate", "example", "risk-first", "0" * 64
                )

    def test_delivers_exact_evidence_claims_v3_candidate(self):
        relative = muse_provider.CANDIDATE_PATHS["evidence-claims-v3"]
        source = experiment.ROOT / relative
        (self.repo / relative).parent.mkdir(parents=True)
        shutil.copyfile(source, self.repo / relative)
        destination = Path(self.temp.name) / "v3"
        muse_provider._prepare_workspace(
            self.repo, destination, self.base, self.head, "candidate", "example", "evidence-claims-v3"
        )
        delivered = (destination / ".agents/skills/example/SKILL.md").read_bytes()
        self.assertEqual(delivered, source.read_bytes())
        self.assertEqual(hashlib.sha256(delivered).hexdigest(), experiment.EVIDENCE_CLAIMS_V3_SHA256)
        identity = muse_provider._candidate_identity(
            experiment.ROOT, "candidate", "example", "evidence-claims-v3",
            experiment.EVIDENCE_CLAIMS_V3_SHA256,
        )
        self.assertEqual(
            (identity["candidateId"], identity["candidateSha256"]),
            ("evidence-claims-v3", experiment.EVIDENCE_CLAIMS_V3_SHA256),
        )
        with self.assertRaises(muse_provider.ProviderError):
            muse_provider._candidate_identity(
                experiment.ROOT, "candidate", "example", "evidence-claims-v3",
                experiment.SCREEN_V3_MANIFEST_SHA256,
            )

    def _call_screen_provider(
        self, trace_for_workspace, source_repository="gen-v-research-tools",
        workspace_parent=False, run=None,
    ):
        relative = muse_provider.CANDIDATE_PATHS["evidence-claims-v3"]
        outside = Path(self.temp.name) / "outside"
        outside.mkdir(exist_ok=True)
        self.trace_dir = Path(self.temp.name) / f"traces-{len(list(Path(self.temp.name).glob('traces-*')))}"
        self.trace_dir.mkdir()
        (self.repo / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(experiment.ROOT / relative, self.repo / relative)
        seen = {}
        # Synthetic session record the fake stream points at, so managed-run
        # evidence retention succeeds and the row is auditable.
        self.session_id = "11111111-2222-3333-4444-555555555555"
        sessions_root = Path(self.temp.name) / "sessions"
        session_dir = sessions_root / "2026" / "09" / "16" / self.session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session.jsonl").write_text("{}\n", encoding="utf-8")

        def envelop(events):
            return [
                {**event, "stream": {"kind": "session", "id": self.session_id}}
                for event in events
            ]

        def fake_run(args, workspace, timeout_seconds, env):
            seen["workspace"] = workspace
            seen["args"] = args
            seen["env"] = dict(env)
            review = json.dumps({"verdict": "APPROVE", "head_sha": self.head})
            events = envelop([
                {"payload_type": "run.started", "payload": {"model": "muse-spark-1.3-contributor"}},
                {"payload_type": "agent.skill_read.observed", "payload": {"skill_id": "example"}},
                *trace_for_workspace(workspace),
                {"payload_type": "run.terminal.completed", "payload": {"text": review}},
            ])
            return subprocess.CompletedProcess(args, 0, "\n".join(json.dumps(e) for e in events), "")

        config_extra = {"workspace_parent": str(outside)} if workspace_parent else {}
        with mock.patch.object(muse_provider, "_prepare_workspace", return_value=(self.base, self.head)), \
                mock.patch.dict(os.environ, {
                    "MUSE_EVAL_TRACE_DIR": str(self.trace_dir),
                    "MUSE_EVAL_SESSIONS_ROOT": str(sessions_root),
                }):
            with mock.patch.object(muse_provider.shutil, "which", return_value="/usr/bin/muse"):
                with mock.patch.object(muse_provider, "_run_muse", side_effect=run or fake_run):
                    response = muse_provider.call_api(
                        "review now",
                        {
                            "config": {
                                "variant": "candidate",
                                "candidate_id": "evidence-claims-v3",
                                "candidate_sha256": experiment.EVIDENCE_CLAIMS_V3_SHA256,
                                "model": "muse-spark-1.3-contributor",
                                "max_model_steps": 24,
                                "timeout_seconds": 540,
                                "repo_root": str(self.repo),
                                **config_extra,
                            }
                        },
                        {"vars": {
                            "base_sha": self.base, "head_sha": self.head, "skill_name": "example",
                            "source_repository": source_repository,
                        }, "test": {"metadata": {"case_id": "genv-pr90-evidence-claim"}}},
                    )
        return response, seen

    def test_screen_provider_records_v3_identity_and_allows_checkout_paths(self):
        response, seen = self._call_screen_provider(
            lambda workspace: [
                {"payload_type": "tool.call", "payload": {"text": f"cat {workspace}/research/note.md"}},
                {"payload_type": "session.root", "payload": {"text": str(self.repo.resolve())}},
            ]
        )
        self.assertNotIn("error", response)
        self.assertEqual(response["metadata"]["candidateId"], "evidence-claims-v3")
        self.assertEqual(response["metadata"]["candidateSha256"], experiment.EVIDENCE_CLAIMS_V3_SHA256)
        self.assertTrue(response["metadata"]["skillObserved"])
        self.assertEqual(response["metadata"]["museModels"], ["muse-spark-1.3-contributor"])
        self.assertIn("--max-model-steps", seen["args"])
        self.assertEqual(seen["args"][seen["args"].index("--max-model-steps") + 1], "24")
        self.assertNotIn("gold", seen["args"][-1])

    def test_screen_provider_quarantines_rows_that_reach_grader_only_material(self):
        for label, trace, flag in (
            ("eval repo path", lambda w: [{"payload_type": "tool.call", "payload": {
                "text": f"cat {self.repo.resolve()}/evals/behavioral/cases/x.yaml"}}], "eval-repository-path"),
            ("relative case pack", lambda w: [{"payload_type": "tool.call", "payload": {
                "text": "cat ../../evals/behavioral/cases/development-v3-cases.yaml"}}], "development-v3-cases"),
            ("gold field", lambda w: [{"payload_type": "tool.result", "payload": {
                "text": "gold_findings: |"}}], "gold_findings"),
            ("diagnosis report", lambda w: [{"payload_type": "tool.call", "payload": {
                "text": "ls reports/2026-09-15-evidence-claims-v2-miss-diagnosis.md"}}], "miss-diagnosis"),
            ("answer-key snapshot", lambda w: [{"payload_type": "tool.call", "payload": {
                "text": "cat answer-keys/evidence-claims-v3-screen-v1.yaml"}}], "evidence-claims-v3-screen"),
            ("sibling of checkout", lambda w: [{"payload_type": "tool.call", "payload": {
                "text": f"ls {w.parent.parent}/other-run"}}], "workspace-parent-path"),
        ):
            with self.subTest(label=label):
                response, _ = self._call_screen_provider(trace, workspace_parent=True)
                self.assertNotIn("error", response)
                self.assertIn(flag, response["metadata"]["graderBoundaryFlags"])
                verdict = review_contract.assert_answer_key_boundary(
                    response["output"], {"metadata": response["metadata"]}
                )
                self.assertFalse(verdict["pass"])
                self.assertIn("QUARANTINE", verdict["reason"])

    def test_screen_provider_retains_private_traces_and_uses_outside_workspace(self):
        response, seen = self._call_screen_provider(
            lambda w: [{"payload_type": "tool.call", "payload": {"text": f"cat {w}/a.txt"}}],
            workspace_parent=True,
        )
        metadata = response["metadata"]
        self.assertTrue(metadata["workspaceOutsideEvalRepo"])
        self.assertNotIn(str(self.repo.resolve()), str(seen["workspace"]))
        self.assertEqual(metadata["graderBoundaryFlags"], [])
        self.assertTrue(
            review_contract.assert_answer_key_boundary("", {"metadata": metadata})["pass"]
        )
        self.assertEqual(metadata["traceStatus"], "retained")
        stored = Path(self.trace_dir) / metadata["traceStdoutFile"]
        self.assertEqual(stat.S_IMODE(stored.stat().st_mode), 0o600)
        self.assertEqual(hashlib.sha256(stored.read_bytes()).hexdigest(), metadata["traceStdoutSha256"])
        self.assertIn("run.terminal.completed", stored.read_text(encoding="utf-8"))
        # Without an outside workspace the boundary assertion fails closed.
        response, _ = self._call_screen_provider(lambda w: [])
        self.assertFalse(
            review_contract.assert_answer_key_boundary("", {"metadata": response["metadata"]})["pass"]
        )

    def test_screen_provider_keeps_timeout_trace_and_checks_it(self):
        def timeout(args, workspace, timeout_seconds, env):
            raise subprocess.TimeoutExpired(
                args, timeout_seconds,
                output=json.dumps({"payload_type": "tool.result", "payload": {"text": "gold_findings"}}),
                stderr="late",
            )
        response, _ = self._call_screen_provider(lambda w: [], workspace_parent=True, run=timeout)
        self.assertIn("timed out", response["error"])
        self.assertEqual(response["metadata"]["termination"], "timeout")
        self.assertEqual(response["metadata"]["completionStatus"], "timeout")
        # The timed-out partial stream carries no session record, so the row
        # is quarantined as unevaluable evidence instead of being approved.
        self.assertEqual(
            response["metadata"]["graderBoundaryFlags"],
            ["evidence-unavailable", "gold_findings"],
        )
        self.assertEqual(response["metadata"]["evidenceStatus"], "not-retained")
        self.assertEqual(response["metadata"]["traceStatus"], "retained")

    def test_command_scan_matches_real_muse_tool_result_shape(self):
        # Mirrors a real `muse exec --json` probe: commands arrive as JSON text in
        # tool.result and task.lifecycle.output events.
        def chunk(command):
            return json.dumps({"chunk_id": "exec-1-1", "command": command, "exit_code": 0,
                               "terminal_status": "completed", "output": "x\n"}, indent=2)

        def trace(w):
            return [
                {"payload_type": "tool.result", "payload": {"kind": "tool_result", "text": chunk("cat README.md")}},
                {"payload_type": "task.lifecycle.output",
                 "payload": {"event": {"kind": "output", "chunk": chunk("cat ../notes.txt")}}},
                {"payload_type": "tool.result",
                 "payload": {"text": chunk("ls /Users/someone/Documents/Developer")}},
            ]

        response, _ = self._call_screen_provider(trace, workspace_parent=True)
        self.assertEqual(
            response["metadata"]["graderBoundaryFlags"],
            ["command-absolute-path", "command-traversal"],
        )

    def test_command_scan_flags_escapes_but_not_ordinary_review_commands(self):
        workspace = Path("/x/.muse-skill-eval-ab/repo")
        ordinary = [
            "git diff c90e9aa..3ecd941 --stat", "git log --oneline base..head",
            "git show HEAD~1:README.md", "PYTHONPATH=src python3 -m unittest tests.test_q1",
            "cd src && ls", "grep -rn every research/ 2>/dev/null",
            f"cat {workspace}/research/note.md", "/usr/bin/env python3 x.py", "cat a.json | jq .",
        ]
        escapes = {
            "cat ../notes.txt": "command-traversal",
            "cat /Users/u/Documents/x": "command-absolute-path",
            "cat /private/tmp/x": "command-absolute-path",
            "ls ~": "command-home-reference",
            "cat $HOME/.config": "command-home-reference",
            "cd": "command-directory-change",
            "cd ..": "command-directory-change",
            "git -C other log": "command-indirect-access",
            "git --git-dir=.git log": "command-indirect-access",
            "cat .git/objects/info/alternates": "command-indirect-access",
            "python3 -c 'import os; print(os.environ)'": "command-indirect-access",
        }
        for command in ordinary:
            with self.subTest(command=command):
                self.assertEqual(muse_provider._command_flags(command, workspace), set())
        for command, flag in escapes.items():
            with self.subTest(command=command):
                self.assertIn(flag, muse_provider._command_flags(command, workspace))
        for tool in ("/opt/homebrew/bin/rg -n x src", "/usr/local/bin/jq . a.json"):
            self.assertEqual(muse_provider._command_flags(tool, workspace), set())
        # Truncated, prefixed or fenced JSON still yields its commands.
        for text in (
            '{\n  "chunk_id": "exec-1-1",\n  "command": "cat ../x",\n  "output": "trunc',
            'Exit 0\n{"command": "cat ../x"}',
            '```json\n{"checks": [{"command": "cat ../x"}]}\n```',
        ):
            with self.subTest(text=text[:20]):
                stream = json.dumps({"payload_type": "tool.result", "payload": {"text": text}})
                self.assertIn("cat ../x", muse_provider._command_texts(stream))
        # Commands the review itself reports are scanned too.
        review = json.dumps({"checks": [{"command": "cat ../../x", "exit_code": 0}]})
        stream = json.dumps({"payload_type": "run.terminal.completed", "payload": {"text": review}})
        self.assertIn("cat ../../x", muse_provider._command_texts(stream))

    def test_trace_write_failure_keeps_the_finished_review(self):
        def read_only_traces(w):
            self.trace_dir.chmod(0o500)
            self.addCleanup(self.trace_dir.chmod, 0o700)
            return []

        response, _ = self._call_screen_provider(read_only_traces, workspace_parent=True)
        self.assertNotIn("error", response)
        self.assertEqual(response["metadata"]["traceStatus"], "failed")
        self.assertIn("PermissionError", response["metadata"]["traceError"])
        self.assertRegex(response["metadata"]["traceStdoutSha256"], r"^[0-9a-f]{64}$")
        self.assertIn("verdict", response["output"])
        # Without its complete trace the row cannot be audited, so it is quarantined.
        self.assertFalse(
            review_contract.assert_answer_key_boundary("", {"metadata": response["metadata"]})["pass"]
        )

    def test_review_env_hides_eval_harness_paths_and_names(self):
        root = self.repo.resolve()
        fake = {
            "PATH": os.pathsep.join([f"{root}/node_modules/.bin", "/usr/bin", str(root)]),
            "MUSE_EVAL_TRACE_DIR": f"{root}/evals/behavioral/results/x.traces",
            "MUSE_EVAL_WORKSPACE_PARENT": "/outside",
            "npm_lifecycle_event": "eval:evidence-claims-v3-screen",
            "INIT_CWD": str(root), "PWD": str(root), "SPIKE_PYTHON": f"{root}/.venv/bin/python",
            "PROMPTFOO_CONFIG_DIR": f"{root}/evals/behavioral/results/x.promptfoo",
            "VIRTUAL_ENV": f"{root}/.venv", "HOME": "/home/user",
        }
        with mock.patch.dict(os.environ, fake, clear=True):
            env = muse_provider._review_env(root)
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["HOME"], "/home/user")
        flat = "\n".join(f"{k}={v}" for k, v in env.items())
        self.assertNotIn(str(root), flat)
        for marker in muse_provider.GRADER_ONLY_MARKERS:
            self.assertNotIn(marker, flat)

    def test_workspace_parent_from_runner_environment(self):
        outside = Path(self.temp.name) / "from-env"
        outside.mkdir()
        with mock.patch.dict(os.environ, {"MUSE_EVAL_WORKSPACE_PARENT": str(outside)}):
            self.assertEqual(muse_provider._workspace_parent({}, self.repo.resolve()), outside.resolve())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(muse_provider._workspace_parent({}, self.repo.resolve()), self.repo.resolve())

    def test_workspace_parent_must_be_outside_eval_repo(self):
        inside = self.repo / "nested"
        inside.mkdir()
        for bad in (self.repo, inside, self.repo.parent):
            with self.subTest(parent=bad):
                with self.assertRaises(muse_provider.ProviderError):
                    muse_provider._workspace_parent({"workspace_parent": str(bad)}, self.repo.resolve())

    def test_grader_boundary_check_skips_muse_skills_history(self):
        # Historical muse-skills heads legitimately contain these field names.
        response, _ = self._call_screen_provider(
            lambda w: [{"payload_type": "tool.result", "payload": {"text": "gold_findings: |"}}],
            source_repository="muse-skills",
        )
        self.assertNotIn("error", response)

    def test_rejects_non_sha_and_non_ancestor(self):
        with self.assertRaises(muse_provider.ProviderError):
            muse_provider._resolve_commit(self.repo, "HEAD; echo unsafe")
        with self.assertRaises(muse_provider.ProviderError):
            muse_provider._prepare_workspace(
                self.repo, Path(self.temp.name) / "wrong", self.head, self.base, "none", "example"
            )

    def test_historical_workspace_excludes_later_objects_refs_and_source_recovery(self):
        (self.repo / "future-gold.txt").write_text("future answer\n", encoding="utf-8")
        self.git("add", "future-gold.txt")
        self.git("commit", "--quiet", "-m", "future")
        future = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("tag", "future-release")

        destination = Path(self.temp.name) / "bounded-history"
        base, head = muse_provider._prepare_workspace(
            self.repo, destination, self.base, self.head, "none", "example"
        )

        self.assertEqual((base, head), (self.base, self.head))
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base, head], cwd=destination, check=False
        )
        self.assertEqual(ancestry.returncode, 0)
        changed = subprocess.run(
            ["git", "diff", "--name-only", base, head],
            cwd=destination,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(changed.stdout.strip(), "value.txt")
        hidden = subprocess.run(
            ["git", "cat-file", "-e", f"{future}^{{commit}}"],
            cwd=destination,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(hidden.returncode, 0)
        refs = subprocess.run(
            ["git", "show-ref"], cwd=destination, capture_output=True, text=True, check=False
        )
        self.assertEqual(refs.stdout, "")
        remotes = subprocess.run(
            ["git", "remote"], cwd=destination, capture_output=True, text=True, check=True
        )
        self.assertEqual(remotes.stdout, "")
        self.assertFalse((destination / ".git/objects/info/alternates").exists())
        self.assertFalse((destination / ".git/FETCH_HEAD").exists())
        config = (destination / ".git/config").read_text(encoding="utf-8")
        self.assertNotIn(str(self.repo), config)

    def test_remote_case_workspace_keeps_only_exact_head_ancestry(self):
        (self.repo / "future-gold.txt").write_text("future answer\n", encoding="utf-8")
        self.git("add", "future-gold.txt")
        self.git("commit", "--quiet", "-m", "future")
        future = self.git("rev-parse", "HEAD").stdout.strip()

        destination = Path(self.temp.name) / "remote-fixture"
        base, head = muse_provider.prepare_remote_historical_workspace(
            str(self.repo), destination, self.base, self.head
        )

        self.assertEqual((base, head), (self.base, self.head))
        hidden = subprocess.run(
            ["git", "cat-file", "-e", f"{future}^{{commit}}"],
            cwd=destination,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(hidden.returncode, 0)
        self.assertEqual(
            subprocess.run(
                ["git", "remote"],
                cwd=destination,
                capture_output=True,
                text=True,
                check=True,
            ).stdout,
            "",
        )
        self.assertFalse((destination / ".git/FETCH_HEAD").exists())
        refs = subprocess.run(
            ["git", "show-ref"],
            cwd=destination,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(refs.stdout, "")
        self.assertNotIn(str(self.repo), (destination / ".git/config").read_text())

    def test_source_tree_identity_is_verified(self):
        destination = Path(self.temp.name) / "tree-identity"
        muse_provider._prepare_workspace(
            self.repo, destination, self.base, self.head, "none", "example"
        )
        base_tree = self.git("rev-parse", f"{self.base}^{{tree}}").stdout.strip()
        head_tree = self.git("rev-parse", f"{self.head}^{{tree}}").stdout.strip()
        muse_provider._verify_source_trees(
            destination, self.base, self.head, base_tree, head_tree
        )
        with self.assertRaises(muse_provider.ProviderError):
            muse_provider._verify_source_trees(
                destination, self.base, self.head, base_tree, "0" * 40
            )

    def test_parses_terminal_output_and_skill_trace(self):
        lines = [
            {
                "payload_type": "tool.result",
                "payload": {
                    "text": '<read-skill-result name="example" status="ok">body</read-skill-result>',
                    "correlation_facts": {"tool_name": "read_skill", "outcome": "success"},
                },
            },
            {"payload_type": "run.output.delta", "payload": {"text": "partial"}},
            {"payload_type": "run.terminal.completed", "payload": {"text": "final"}},
        ]
        parsed = muse_provider._parse_muse_stream("\n".join(json.dumps(x) for x in lines), "example")
        self.assertEqual(parsed["output"], "final")
        self.assertTrue(parsed["skillObserved"])
        self.assertEqual(parsed["eventCount"], 3)

    def test_parses_model_and_usage_telemetry(self):
        lines = [
            {
                "payload_type": "run.started",
                "payload": {
                    "model": "muse-spark-1.3-contributor",
                    "usage": {"input_tokens": 11, "output_tokens": 7},
                },
            },
            {"payload_type": "run.terminal.completed", "payload": {"text": "{}"}},
        ]
        parsed = muse_provider._parse_muse_stream("\n".join(json.dumps(x) for x in lines), "example")
        self.assertEqual(parsed["models"], ["muse-spark-1.3-contributor"])
        self.assertEqual(parsed["usage"], {"prompt": 11, "completion": 7, "total": 18})

    def test_muse_skill_activation_is_shared_by_current_and_candidates(self):
        current = muse_provider._activate_skill_prompt("review now", "example")
        candidate = muse_provider._activate_skill_prompt("review now", "example")
        self.assertEqual(current, candidate)
        self.assertIn("read_skill", current)

    def test_muse_timeout_kills_process_group_and_preserves_partial_output(self):
        class FakeProcess:
            pid = 4242
            returncode = -9

            def communicate(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired(["muse"], timeout, output="partial", stderr="warn")
                return "partial", "warn"

        with mock.patch.object(muse_provider.subprocess, "Popen", return_value=FakeProcess()):
            with mock.patch.object(muse_provider.os, "killpg") as killpg:
                with self.assertRaises(subprocess.TimeoutExpired) as raised:
                    muse_provider._run_muse(["muse"], self.repo, 1, {})
        killpg.assert_called_once_with(4242, muse_provider.signal.SIGKILL)
        self.assertEqual(raised.exception.output, "partial")

    def test_ordinary_muse_baseline_can_use_environment_model_override(self):
        output = json.dumps({
            "payload_type": "run.terminal.completed",
            "payload": {"text": json.dumps({"verdict": "APPROVE", "head_sha": self.head})},
        })
        with mock.patch.object(muse_provider, "_prepare_workspace", return_value=(self.base, self.head)):
            with mock.patch.object(muse_provider.shutil, "which", return_value="/usr/bin/muse"):
                with mock.patch.object(
                    muse_provider,
                    "_run_muse",
                    return_value=subprocess.CompletedProcess(["muse"], 0, output, ""),
                ) as run:
                    with mock.patch.dict(muse_provider.os.environ, {"MUSE_EVAL_MODEL": "baseline-model"}):
                        response = muse_provider.call_api(
                            "review now",
                            {"config": {"variant": "current", "timeout_seconds": 1, "repo_root": str(self.repo)}},
                            {"vars": {"base_sha": self.base, "head_sha": self.head, "skill_name": "example"}},
                        )
        self.assertNotIn("error", response)
        self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("--model") + 1], "baseline-model")

    def test_spike_skill_activation_failure_is_an_execution_error(self):
        output = json.dumps({
            "payload_type": "run.started",
            "payload": {"model": "muse-spark-1.3-contributor"},
        }) + "\n" + json.dumps({
            "payload_type": "run.terminal.completed",
            "payload": {"text": "{}"},
        })
        digest = hashlib.sha256((self.repo / "skills/example/SKILL.md").read_bytes()).hexdigest()
        with mock.patch.object(muse_provider, "_prepare_workspace", return_value=(self.base, self.head)):
            with mock.patch.object(muse_provider.shutil, "which", return_value="/usr/bin/muse"):
                with mock.patch.object(
                    muse_provider,
                    "_run_muse",
                    return_value=subprocess.CompletedProcess(["muse"], 0, output, ""),
                ):
                    response = muse_provider.call_api(
                        "review now",
                        {
                            "config": {
                                "variant": "current",
                                "candidate_id": "current",
                                "candidate_sha256": digest,
                                "model": "muse-spark-1.3-contributor",
                                "timeout_seconds": 1,
                                "repo_root": str(self.repo),
                            }
                        },
                        {"vars": {"base_sha": self.base, "head_sha": self.head, "skill_name": "example"}},
                    )
        self.assertIn("observing the delivered project skill", response["error"])

    def test_codex_prompt_withholds_or_injects_skill(self):
        control, control_delivery = codex_provider._candidate_prompt(
            self.repo, "example", "none", "review now"
        )
        treatment, treatment_delivery = codex_provider._candidate_prompt(
            self.repo, "example", "current", "review now"
        )
        self.assertEqual(control_delivery, "withheld")
        self.assertNotIn("example skill", control)
        self.assertEqual(treatment_delivery, "prompt-injected")
        self.assertIn("example skill", treatment)
        self.assertTrue(control.endswith("review now"))
        self.assertTrue(treatment.endswith("review now"))

    def test_codex_command_is_ephemeral_isolated_and_pinned(self):
        command = codex_provider._command(
            "codex", self.repo, "gpt-5.6-luna", "high", "review now"
        )
        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--strict-config", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "workspace-write")
        self.assertEqual(command[command.index("--model") + 1], "gpt-5.6-luna")
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertIn("sandbox_workspace_write.network_access=false", command)
        self.assertIn('web_search="disabled"', command)
        with self.assertRaises(muse_provider.ProviderError):
            codex_provider._command("codex", self.repo, "unapproved-model", "high", "review")

    def test_parses_codex_output_and_usage(self):
        lines = [
            {"type": "thread.started", "thread_id": "fixture"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "first"},
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "final"},
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "output_tokens": 20,
                },
            },
        ]
        parsed = codex_provider._parse_codex_stream("\n".join(json.dumps(x) for x in lines))
        self.assertEqual(parsed["output"], "final")
        self.assertEqual(parsed["usage"]["input_tokens"], 100)
        self.assertEqual(len(parsed["events"]), 4)

    def test_codex_worker_closes_inherited_stdin(self):
        completed = subprocess.CompletedProcess(["codex"], 0, "", "")
        with mock.patch.object(codex_provider.subprocess, "run", return_value=completed) as run:
            result = codex_provider._run_codex(["codex"], self.repo, 10, {"NO_COLOR": "1"})
        self.assertIs(result, completed)
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)


class ReviewAssertionTests(unittest.TestCase):
    def review(self, verdict="NEEDS_FIXES"):
        return json.dumps(
            {
                "head_sha": "abcdef1",
                "verdict": verdict,
                "summary": "reviewed",
                "findings": [
                    {
                        "severity": "blocking",
                        "location": "file.py:7",
                        "impact": "runtime crash",
                        "repro": {"command": "python file.py", "exit_code": 1, "output": "NameError"},
                    }
                ],
                "checks": [{"command": "python file.py", "exit_code": 1, "output": "NameError"}],
                "unrun": [],
                "revision_rounds": "0/3",
            }
        )

    def test_contract_verdict_skill_and_evidence(self):
        context = {
            "vars": {"head_sha": "abcdef123456", "expected_verdict": "NEEDS_FIXES"},
            "metadata": {"variant": "current", "skillObserved": True},
        }
        self.assertTrue(review_contract.assert_review_contract(self.review(), context)["pass"])
        self.assertTrue(review_contract.assert_expected_verdict(self.review(), context)["pass"])
        self.assertTrue(review_contract.assert_skill_observation(self.review(), context)["pass"])
        self.assertTrue(review_contract.assert_evidence(self.review(), context)["pass"])

    def test_codex_skill_delivery(self):
        current = {
            "metadata": {
                "runtime": "codex-reference",
                "variant": "current",
                "skillDelivery": "prompt-injected",
            }
        }
        control = {
            "metadata": {
                "runtime": "codex-reference",
                "variant": "none",
                "skillDelivery": "withheld",
            }
        }
        self.assertTrue(review_contract.assert_skill_delivery("", current)["pass"])
        self.assertTrue(review_contract.assert_skill_delivery("", control)["pass"])
        control["metadata"]["skillDelivery"] = "prompt-injected"
        self.assertFalse(review_contract.assert_skill_delivery("", control)["pass"])

    def test_invalid_json_and_missing_evidence_fail(self):
        context = {"vars": {"head_sha": "abcdef123456", "expected_verdict": "APPROVE"}}
        self.assertFalse(review_contract.assert_review_contract("not json", context)["pass"])
        value = json.loads(self.review("APPROVE"))
        value["findings"][0]["repro"]["output"] = ""
        value["checks"] = []
        self.assertFalse(review_contract.assert_evidence(json.dumps(value), context)["pass"])

    def test_self_grading_check_fails_closed_without_model_telemetry(self):
        self.assertFalse(review_contract.assert_no_self_grading("", {"metadata": {}})["pass"])
        context = {
            "metadata": {
                "museModels": ["muse-spark-1.3-contributor"],
                "museExpectedModel": "muse-spark-1.3-contributor",
            }
        }
        self.assertTrue(review_contract.assert_no_self_grading("", context)["pass"])


class SpikeValidationTests(unittest.TestCase):
    def test_frozen_spike_configuration(self):
        self.assertEqual(experiment.validate_manifest(), [])
        self.assertEqual(experiment.validate_cases(), [])
        self.assertEqual(experiment.validate_config(), [])

    def test_separate_development_v2_configuration(self):
        self.assertEqual(experiment.validate_development_manifest(), [])
        self.assertEqual(experiment.validate_development_cases(), [])
        self.assertEqual(experiment.validate_development_config(), [])
        cases = experiment._load(experiment.DEVELOPMENT_CASES)
        self.assertTrue(all(case["metadata"]["split"] == "development" for case in cases))
        self.assertTrue(all(case["metadata"]["human_adjudication"] is False for case in cases))
        self.assertEqual(
            sum(case["vars"]["gold_findings"] == "NONE" for case in cases), 1
        )

    def test_versioned_development_v3_calibration(self):
        self.assertEqual(experiment.validate_development_v3_manifest(), [])
        self.assertEqual(experiment.validate_development_v3_cases(), [])
        self.assertEqual(experiment.validate_development_v3_config(), [])
        cases = experiment._load(experiment.DEVELOPMENT_V3_CASES)
        control = next(
            case for case in cases
            if case["metadata"]["case_id"] == "genv-pr90-synchronized-clean"
        )
        self.assertEqual(control["vars"]["expected_verdict"], "APPROVE")
        self.assertIn("(low)", control["vars"]["gold_findings"])
        self.assertNotIn(
            "tracked finalization metadata are synchronized",
            control["vars"]["resolved_findings"],
        )

    def test_evidence_claims_v3_screen_configuration(self):
        self.assertEqual(experiment.validate_screen_v3_manifest(), [])
        self.assertEqual(experiment.validate_screen_v3_config(), [])
        config = experiment._load(experiment.SCREEN_V3_CONFIG)
        profile = experiment._load(experiment.DEVELOPMENT_V3_CONFIG)
        self.assertEqual(config["tests"], profile["tests"])
        self.assertEqual(config["evaluateOptions"], profile["evaluateOptions"])
        # Review-only: the profile's deterministic checks plus quarantine, no LLM grader.
        self.assertEqual(config["defaultTest"]["options"], {"disableVarExpansion": True})
        python_checks = [a for a in profile["defaultTest"]["assert"] if a["type"] == "python"]
        self.assertEqual(config["defaultTest"]["assert"][:-1], python_checks)
        self.assertEqual(config["defaultTest"]["assert"][-1]["metric"], "answer_key_boundary")
        self.assertFalse(any(a["type"] == "llm-rubric" for a in config["defaultTest"]["assert"]))
        keys = experiment._load(experiment.SCREEN_V3_ANSWER_KEYS)
        severity = keys["cases"]["genv-pr87-first-repair-type-boundary"]["findings"][0]["severity"]
        self.assertEqual(severity, "should-fix")
        self.assertEqual(
            [(p["label"], p["config"]["candidate_id"]) for p in config["providers"]],
            [("screen-evidence-claims-v2", "evidence-claims-v2"),
             ("screen-evidence-claims-v3", "evidence-claims-v3")],
        )
        baseline = {k: v for k, v in profile["providers"][1]["config"].items()}
        for provider in config["providers"]:
            shared = {k: v for k, v in provider["config"].items() if k not in {"candidate_id", "candidate_sha256"}}
            self.assertEqual(shared, {k: v for k, v in baseline.items() if k not in {"candidate_id", "candidate_sha256"}})

    def test_evidence_claims_v3_screen_rejects_candidate_and_profile_drift(self):
        config = experiment._load(experiment.SCREEN_V3_CONFIG)
        manifest = experiment._load(experiment.SCREEN_V3_MANIFEST)
        mutations = {
            "v3 replaced by v2": lambda c: c["providers"][1]["config"].update(
                candidate_id="evidence-claims-v2",
                candidate_sha256=manifest["candidates"][0]["sha256"],
            ),
            "step budget raised": lambda c: c["providers"][1]["config"].update(max_model_steps=48),
            "wall clock raised": lambda c: c["providers"][0]["config"].update(timeout_seconds=900),
            "copied case pack": lambda c: c.update(tests="file://cases/screen-cases.yaml"),
            "third provider": lambda c: c["providers"].append(copy.deepcopy(c["providers"][0])),
            "gold exposed to candidate": lambda c: c.update(prompts=["file://prompts/gold.txt"]),
            "llm grader added": lambda c: c["defaultTest"]["options"].update(
                provider={"id": "anthropic:claude-agent-sdk"}
            ),
            "quarantine removed": lambda c: c["defaultTest"]["assert"].pop(),
            "rubric added": lambda c: c["defaultTest"]["assert"].append(
                {"type": "llm-rubric", "metric": "gold_recall", "threshold": 0.75, "value": "{{gold_findings}}"}
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                changed = copy.deepcopy(config)
                mutate(changed)
                with mock.patch.object(experiment, "_load", side_effect=[changed, manifest]):
                    self.assertNotEqual(experiment.validate_screen_v3_config(), [])
        profile = experiment._load(experiment.DEVELOPMENT_V3_MANIFEST)
        keys = experiment._load(experiment.SCREEN_V3_ANSWER_KEYS)
        cases = experiment._load(experiment.DEVELOPMENT_V3_CASES)
        for label, mutate in {
            "v3 hash edited": lambda m, k: m["candidates"][1].update(sha256="0" * 64),
            "baseline swapped": lambda m, k: m["candidates"].reverse(),
            "runtime changed": lambda m, k: m["tested_runtime"].update(model="other"),
            "llm grader declared": lambda m, k: m.update(grader={"model": "gpt-5.6-terra"}),
            "answer-key verdict drift": lambda m, k: k["cases"]["genv-pr90-synchronized-clean"].update(
                expected_verdict="NEEDS_FIXES"
            ),
        }.items():
            with self.subTest(label=label):
                changed, changed_keys = copy.deepcopy(manifest), copy.deepcopy(keys)
                mutate(changed, changed_keys)
                with mock.patch.object(
                    experiment, "_load", side_effect=[changed, profile, changed_keys, cases]
                ):
                    self.assertNotEqual(experiment.validate_screen_v3_manifest(), [])

    def test_development_v3_rejects_truth_and_rubric_regressions(self):
        cases = experiment._load(experiment.DEVELOPMENT_V3_CASES)
        bad_cases = copy.deepcopy(cases)
        control = next(
            case for case in bad_cases
            if case["metadata"]["case_id"] == "genv-pr90-synchronized-clean"
        )
        control["vars"]["gold_findings"] = "NONE"
        with mock.patch.object(experiment, "_load", side_effect=[bad_cases, experiment._load(experiment.DEVELOPMENT_CASES)]):
            self.assertNotEqual(experiment.validate_development_v3_cases(), [])

        config = experiment._load(experiment.DEVELOPMENT_V3_CONFIG)
        manifest = experiment._load(experiment.DEVELOPMENT_V3_MANIFEST)
        for label, metric, replacement in (
            ("gold recall follows candidate severity", "gold_recall", "Score only matching severities. {{gold_findings}}"),
            ("blocking recall follows candidate severity", "blocking_recall", "No blocking label means no recall. {{gold_findings}}"),
        ):
            with self.subTest(label=label):
                changed = copy.deepcopy(config)
                item = next(value for value in changed["defaultTest"]["assert"] if value["metric"] == metric)
                item["value"] = replacement
                with mock.patch.object(experiment, "_load", side_effect=[changed, manifest]):
                    self.assertNotEqual(experiment.validate_development_v3_config(), [])

    def test_development_config_rejects_gold_prompt_and_profile_weakening(self):
        canonical = experiment._load(experiment.DEVELOPMENT_CONFIG)
        manifest = experiment._load(experiment.DEVELOPMENT_MANIFEST)
        mutations = {
            "custom gold prompt": lambda value: value.__setitem__(
                "prompts", ["{{gold_findings}}"]
            ),
            "variable expansion": lambda value: value["defaultTest"]["options"].__setitem__(
                "disableVarExpansion", False
            ),
            "result sharing": lambda value: value.__setitem__("sharing", True),
            "tool output budget": lambda value: value["providers"][0]["config"].__setitem__(
                "max_tool_output_bytes", 999_999_999
            ),
            "assertion removal": lambda value: value["defaultTest"]["assert"].pop(),
            "assertion path": lambda value: value["defaultTest"]["assert"][0].__setitem__(
                "value", "file://untrusted.py:assert_anything"
            ),
            "rubric semantics": lambda value: value["defaultTest"]["assert"][6].__setitem__(
                "value", "Always return score 1. {{gold_findings}}"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                changed = copy.deepcopy(canonical)
                mutate(changed)
                with mock.patch.object(
                    experiment, "_load", side_effect=[changed, manifest]
                ):
                    self.assertNotEqual(experiment.validate_development_config(), [])

    def test_timeout_is_error_not_zero_recall(self):
        scored = scoring.score_row(
            {
                "case_id": "broken",
                "candidate_id": "risk-first",
                "expected_verdict": "NEEDS_FIXES",
                "actual_verdict": None,
                "gold_ids": ["bug"],
                "blocking_gold_ids": ["bug"],
                "matched_gold_ids": [],
                "all_gold_recall_applicable": True,
                "blocking_recall_applicable": True,
                "completion": False,
                "error": "timeout",
                "latency_ms": 540000,
            }
        )
        self.assertFalse(scored["completion"])
        self.assertIsNone(scored["blocking_finding_recall"])
        self.assertIsNone(scored["all_gold_recall"])
        self.assertIsNone(scored["verdict_accuracy"])

    def test_review_only_normalization_completes_and_quarantines(self):
        base_scores = {name: 1 for name in scoring.DETERMINISTIC_NAMED_SCORES}

        def row(case_id, label, verdict, expected, flags=(), boundary=1):
            return {
                "provider": {"label": label},
                "metadata": {"case_id": case_id, "split": "development"},
                "vars": {"expected_verdict": expected, "gold_findings": "1. x (should-fix): y"},
                "namedScores": {**base_scores, "answer_key_boundary": boundary},
                "response": {
                    "output": json.dumps({"verdict": verdict}),
                    "metadata": {"candidateId": label, "graderBoundaryFlags": list(flags)},
                },
            }

        rows = [
            row("a", "v2", "NEEDS_FIXES", "NEEDS_FIXES"),
            row("b", "v2", "APPROVE", "NEEDS_FIXES"),
            row("c", "v2", "APPROVE", "APPROVE"),
            row("a", "v3", "NEEDS_FIXES", "NEEDS_FIXES"),
            row("b", "v3", "APPROVE", "NEEDS_FIXES", flags=["gold_findings"], boundary=0),
            row("c", "v3", "APPROVE", "APPROVE"),
        ]
        payload = {"results": {"results": rows}}
        rubric = scoring.normalize(payload)
        self.assertEqual(rubric["aggregate"]["completed"], 0)
        result = scoring.normalize(payload, "deterministic")
        aggregate = result["aggregate"]
        self.assertEqual((aggregate["rows"], aggregate["completed"], aggregate["errors"]), (6, 6, 0))
        self.assertEqual(aggregate["quarantined"], 1)
        # The quarantined false approval is counted but kept out of quality totals.
        self.assertEqual(aggregate["false_approvals"], 1)
        self.assertAlmostEqual(aggregate["verdict_accuracy"], 4 / 5)
        self.assertIsNone(aggregate["all_gold_recall"])
        self.assertTrue(result["rows"][4]["quarantined"])
        self.assertEqual(result["rows"][4]["grader_boundary_flags"], ["gold_findings"])
        self.assertEqual(result["normalization"]["grading"], "deterministic")
        # An error row whose trace hit a marker is still reported as quarantined.
        errored = row("a", "v3", "", "NEEDS_FIXES", flags=["gold_findings"])
        errored["response"] = {"error": "Muse timed out", "output": "",
                               "metadata": {"graderBoundaryFlags": ["gold_findings"]}}
        scored = scoring.normalize({"results": {"results": [errored]}}, "deterministic")
        self.assertEqual(scored["aggregate"]["errors"], 1)
        self.assertEqual(scored["aggregate"]["quarantined"], 1)
        # A completed review whose trace was not kept is unevaluable.
        untraced = row("a", "v3", "NEEDS_FIXES", "NEEDS_FIXES")
        untraced["response"]["metadata"]["traceStatus"] = "failed"
        scored = scoring.normalize({"results": {"results": [untraced]}}, "deterministic")
        self.assertEqual(scored["aggregate"]["quarantined"], 1)
        self.assertIsNone(scored["aggregate"]["verdict_accuracy"])
        # Older rubric stages never promised traces, so their metrics are unchanged.
        untraced["response"]["metadata"]["traceStatus"] = "not-retained"
        untraced["namedScores"].pop("answer_key_boundary")
        rubric_scored = scoring.promptfoo_rows({"results": {"results": [untraced]}})
        self.assertFalse(rubric_scored[0]["quarantined"])

    def test_empty_gold_recall_is_not_applicable_but_verdict_still_scores(self):
        scored = scoring.score_row(
            {
                "case_id": "clean",
                "candidate_id": "minimal",
                "expected_verdict": "APPROVE",
                "actual_verdict": "APPROVE",
                "gold_ids": [],
                "blocking_gold_ids": [],
                "matched_gold_ids": [],
                "all_gold_recall_applicable": False,
                "blocking_recall_applicable": False,
                "actionable_findings": 0,
                "supported_findings": 0,
                "completion": True,
            }
        )
        self.assertIsNone(scored["all_gold_recall"])
        self.assertIsNone(scored["blocking_finding_recall"])
        self.assertFalse(scored["all_gold_recall_applicable"])
        self.assertIsNone(scored["supported_precision"])
        self.assertTrue(scored["verdict_accuracy"])

    def test_promptfoo_converter_keeps_quality_and_cost_facts_separate(self):
        payload = {
            "results": {
                "results": [
                    {
                        "provider": {"label": "spike-risk-first"},
                        "metadata": {"case_id": "broken", "split": "train"},
                        "vars": {
                            "expected_verdict": "NEEDS_FIXES",
                            "gold_findings": "1. bug (blocking): breaks behavior",
                        },
                        "namedScores": {
                            "review_contract": 1,
                            "verdict_accuracy": 1,
                            "skill_observation": 1,
                            "candidate_integrity": 1,
                            "independent_grading": 1,
                            "evidence": 1,
                            "gold_recall": 0.8,
                            "blocking_recall": 0.5,
                            "supported_precision": 0.75,
                        },
                        "error": "quality assertion failed",
                        "response": {
                            "output": 'Review result:\n```json\n{"verdict":"NEEDS_FIXES"}\n```',
                            "tokenUsage": {"total": 123},
                            "metadata": {"candidateId": "risk-first"},
                        },
                        "latencyMs": 400,
                    },
                    {
                        "provider": {"label": "spike-risk-first"},
                        "metadata": {"case_id": "clean", "split": "train"},
                        "vars": {"expected_verdict": "APPROVE"},
                        "error": "timeout",
                        "response": {},
                    },
                ]
            }
        }
        rows = scoring.promptfoo_rows(payload)
        self.assertEqual(rows[0]["blocking_finding_recall"], 0.5)
        self.assertEqual(rows[0]["all_gold_recall"], 0.8)
        self.assertEqual(rows[0]["actual_verdict"], "NEEDS_FIXES")
        self.assertEqual(rows[0]["candidate_tokens"], 123)
        self.assertEqual(rows[0]["candidate_token_status"], "unavailable")
        self.assertTrue(rows[0]["completion"])
        self.assertIsNone(rows[0]["error"])
        self.assertEqual(rows[0]["assertion_error"], "quality assertion failed")
        self.assertIsNone(rows[1]["all_gold_recall"])
        summary = scoring.aggregate(rows)
        self.assertEqual(summary["candidate_token_rows"], 1)
        self.assertEqual(summary["candidate_tokens"], 123)

    def test_v2_normalizer_preserves_development_source_and_usage_identity(self):
        raw = {
            "provider": {"label": "development-v2-evidence-claims"},
            "metadata": {
                "case_id": "genv-pr90-evidence-claim",
                "split": "development",
                "data_role": "development",
                "family": "genv-pr90",
            },
            "vars": {
                "expected_verdict": "NEEDS_FIXES",
                "gold_findings": "1. claim (should-fix): contradicted",
            },
            "namedScores": {
                "review_contract": 1,
                "verdict_accuracy": 1,
                "skill_observation": 1,
                "candidate_integrity": 1,
                "independent_grading": 1,
                "evidence": 1,
                "gold_recall": 1,
                "blocking_recall": 1,
                "supported_precision": 1,
            },
            "response": {
                "output": '{"head_sha":"3ecd941","verdict":"NEEDS_FIXES"}',
                "metadata": {
                    "candidateId": "evidence-claims-v2",
                    "candidateSha256": "f" * 64,
                    "sourceRepository": "gen-v-research-tools",
                    "baseSha": "c" * 40,
                    "headSha": "3" * 40,
                    "candidateTokenStatus": "unavailable",
                },
            },
        }
        row = scoring.normalize({"results": {"results": [raw]}})["rows"][0]
        self.assertEqual(row["data_role"], "development")
        self.assertEqual(row["family"], "genv-pr90")
        self.assertEqual(row["source_repository"], "gen-v-research-tools")
        self.assertEqual(row["candidate_token_status"], "unavailable")
        self.assertIsNone(row["candidate_tokens"])

    def test_promptfoo_recall_requires_explicit_case_applicability(self):
        common = {
            "provider": {"label": "spike-current"},
            "metadata": {"case_id": "case", "split": "train"},
            "namedScores": {
                "review_contract": 1,
                "verdict_accuracy": 1,
                "skill_observation": 1,
                "candidate_integrity": 1,
                "independent_grading": 1,
                "evidence": 1,
                "gold_recall": 1,
                "blocking_recall": 1,
                "supported_precision": 1,
            },
            "response": {
                "output": '{"head_sha":"abcdef1","verdict":"APPROVE","summary":"ok","findings":[],"checks":[],"unrun":[],"revision_rounds":"0/3"}',
                "metadata": {"candidateId": "current"},
            },
        }
        unknown = copy.deepcopy(common)
        unknown["vars"] = {"expected_verdict": "APPROVE"}
        clean = copy.deepcopy(common)
        clean["vars"] = {"expected_verdict": "APPROVE", "gold_findings": "NONE"}
        defect = copy.deepcopy(common)
        defect["vars"] = {
            "expected_verdict": "NEEDS_FIXES",
            "gold_findings": "1. defect (blocking): concrete failure",
        }

        rows = scoring.promptfoo_rows({"results": {"results": [unknown, clean, defect]}})
        scored = [scoring.score_row(row) for row in rows]
        self.assertIsNone(scored[0]["all_gold_recall"])
        self.assertIsNone(scored[1]["all_gold_recall"])
        self.assertEqual(scored[2]["all_gold_recall"], 1.0)
        self.assertEqual(scored[2]["blocking_finding_recall"], 1.0)
        summary = scoring.aggregate(rows)
        self.assertEqual(summary["all_gold_recall_applicable_rows"], 1)
        self.assertEqual(summary["all_gold_recall_scored_rows"], 1)
        self.assertEqual(summary["blocking_recall_applicable_rows"], 1)
        self.assertEqual(summary["blocking_recall_scored_rows"], 1)
        normalized = scoring.normalize({"results": {"results": [defect]}})
        self.assertEqual(normalized["normalization"]["id"], "muse-review-metrics-v2")
        self.assertEqual(normalized["aggregate"]["all_gold_recall_scored_rows"], 1)

    def test_calibrated_recall_is_separate_from_candidate_severity_label(self):
        common = {
            "provider": {"label": "development-v3-evidence-claims"},
            "metadata": {"case_id": "calibration", "split": "development"},
            "vars": {
                "expected_verdict": "NEEDS_FIXES",
                "gold_findings": "1. exact-boundary (blocking): accepts a type alias",
            },
            "response": {"metadata": {"candidateId": "evidence-claims-v2"}},
        }
        detected = copy.deepcopy(common)
        detected["namedScores"] = {
            metric: 1 for metric in scoring.REQUIRED_NAMED_SCORES
        }
        detected["response"]["output"] = (
            '{"verdict":"NEEDS_FIXES","findings":'
            '[{"severity":"should-fix","title":"exact boundary accepts a type alias"}]}'
        )
        missed = copy.deepcopy(common)
        missed["namedScores"] = {
            metric: 1 for metric in scoring.REQUIRED_NAMED_SCORES
        }
        missed["namedScores"].update({"gold_recall": 0, "blocking_recall": 0})
        missed["response"]["output"] = '{"verdict":"APPROVE","findings":[]}'

        rows = scoring.normalize({"results": {"results": [detected, missed]}})["rows"]
        self.assertEqual(rows[0]["all_gold_recall"], 1)
        self.assertEqual(rows[0]["blocking_finding_recall"], 1)
        self.assertEqual(rows[1]["all_gold_recall"], 0)
        self.assertEqual(rows[1]["blocking_finding_recall"], 0)

    def test_explicit_unknown_gold_cannot_fall_back_to_provider_facts(self):
        common = {
            "provider": {"label": "spike-current"},
            "metadata": {"case_id": "unknown", "split": "heldout"},
            "namedScores": {
                "review_contract": 1,
                "verdict_accuracy": 1,
                "skill_observation": 1,
                "candidate_integrity": 1,
                "independent_grading": 1,
                "evidence": 1,
                "gold_recall": 1,
                "blocking_recall": 1,
                "supported_precision": 1,
            },
            "response": {
                "output": '{"head_sha":"abcdef1","verdict":"APPROVE","summary":"ok","findings":[],"checks":[],"unrun":[],"revision_rounds":"0/3"}',
                "metadata": {
                    "candidateId": "current",
                    "evaluationFacts": {
                        "gold_ids": ["secret"],
                        "blocking_gold_ids": ["secret"],
                        "matched_gold_ids": ["secret"],
                        "matched_blocking_gold_ids": ["secret"],
                    },
                },
            },
        }
        pending = copy.deepcopy(common)
        pending["vars"] = {"gold_findings": "PENDING_HUMAN_ADJUDICATION"}
        blank = copy.deepcopy(common)
        blank["vars"] = {"gold_findings": "   "}
        malformed = copy.deepcopy(common)
        malformed["vars"] = {"gold_findings": "a finding without a severity contract"}

        rows = scoring.promptfoo_rows(
            {"results": {"results": [pending, blank, malformed]}}
        )
        scored = [scoring.score_row(row) for row in rows]
        self.assertTrue(all(row["all_gold_recall"] is None for row in scored))
        self.assertTrue(all(row["blocking_finding_recall"] is None for row in scored))
        self.assertTrue(all(row["all_gold_recall_applicable"] is None for row in scored))
        self.assertTrue(all(row["blocking_recall_applicable"] is None for row in scored))
        summary = scoring.aggregate(rows)
        self.assertEqual(summary["all_gold_recall_applicable_rows"], 0)
        self.assertEqual(summary["all_gold_recall_scored_rows"], 0)
        self.assertEqual(summary["blocking_recall_applicable_rows"], 0)
        self.assertEqual(summary["blocking_recall_scored_rows"], 0)

    def test_finalist_selection_is_exactly_two_provider_labels(self):
        script = """
          import { parseFinalists, selectProviderLabels } from './evals/behavioral/selection.mjs';
          const parsed = parseFinalists('spike-current, spike-minimal');
          if (parsed.filter !== '^(?:spike-current|spike-minimal)$') process.exit(1);
          if (selectProviderLabels(['spike-current','spike-minimal','spike-risk-first'], parsed.filter).length !== 2) process.exit(2);
          const module = await import('./evals/behavioral/selection.mjs');
          if (!module.isCompletedPromptfooExit(0) || !module.isCompletedPromptfooExit(100)) process.exit(4);
          if (module.isCompletedPromptfooExit(1) || module.isCompletedPromptfooExit(130)) process.exit(5);
          const identity = {normalization:{id:module.NORMALIZATION_ID}};
          if (!module.hasCompleteNormalizedRows({...identity,aggregate:{rows:1,completed:1,errors:0}}, 1)) process.exit(6);
          if (module.hasCompleteNormalizedRows({aggregate:{rows:1,completed:1,errors:0}}, 1)) process.exit(7);
          if (module.hasCompleteNormalizedRows({...identity,aggregate:{rows:1,completed:0,errors:1}}, 1)) process.exit(8);
          let rejected = false;
          try { parseFinalists('spike-current,spike-current'); } catch { rejected = true; }
          if (!rejected) process.exit(3);
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=experiment.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_spike_config_rejects_duplicate_provider_definition(self):
        config = copy.deepcopy(experiment._load(experiment.CONFIG))
        manifest = experiment._load(experiment.MANIFEST)
        config["providers"].append(copy.deepcopy(config["providers"][0]))
        with mock.patch.object(experiment, "_load", side_effect=[config, manifest]):
            errors = experiment.validate_config()
        self.assertTrue(any("exactly one provider" in error for error in errors))

    def test_spike_config_rejects_wrong_provider_implementation(self):
        config = copy.deepcopy(experiment._load(experiment.CONFIG))
        manifest = experiment._load(experiment.MANIFEST)
        config["providers"][0]["id"] = "file://providers/codex_provider.py"
        with mock.patch.object(experiment, "_load", side_effect=[config, manifest]):
            errors = experiment.validate_config()
        self.assertTrue(any("provider variant/candidate mapping" in error for error in errors))

    def test_spike_config_requires_timeout_that_disables_deferred_grading(self):
        config = copy.deepcopy(experiment._load(experiment.CONFIG))
        manifest = experiment._load(experiment.MANIFEST)
        config["evaluateOptions"]["timeoutMs"] = 540_000
        with mock.patch.object(experiment, "_load", side_effect=[config, manifest]):
            errors = experiment.validate_config()
        self.assertTrue(any("per-row timeout" in error for error in errors))
        self.assertTrue(any("exceed every Muse provider" in error for error in errors))

    def test_spike_config_keeps_target_provider_concurrency_at_one(self):
        config = copy.deepcopy(experiment._load(experiment.CONFIG))
        manifest = experiment._load(experiment.MANIFEST)
        config["evaluateOptions"]["maxConcurrency"] = 2
        with mock.patch.object(experiment, "_load", side_effect=[config, manifest]):
            errors = experiment.validate_config()
        self.assertTrue(any("target-provider concurrency" in error for error in errors))

    def test_manifest_rejects_duplicate_candidate_entry(self):
        manifest = copy.deepcopy(experiment._load(experiment.MANIFEST))
        manifest["candidates"].append(copy.deepcopy(manifest["candidates"][0]))
        with mock.patch.object(experiment, "_load", return_value=manifest):
            errors = experiment.validate_manifest()
        self.assertTrue(any("exactly the four immutable candidates" in error for error in errors))

    def test_heldout_selection_requires_frozen_validation_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = Path(temp_dir) / "FINALIST.yaml"
            record_path.write_text(
                "status: pending-validation\n"
                "validation_finalists: [spike-current, spike-minimal]\n"
                "selected_label: null\n"
                "selected_sha256: null\n",
                encoding="utf-8",
            )
            with mock.patch.object(experiment, "FINALIST_RECORD", record_path):
                errors = experiment.validate_heldout(
                    "spike-risk-first",
                    "bad857e4298bf73efa4bcbfac114e67dfa03a9ff19545c5a2594d9de1016d51f",
                )
        self.assertTrue(any("not a frozen validation selection" in error for error in errors))

    def test_frozen_finalist_record_rejects_duplicate_validation_finalists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = Path(temp_dir) / "FINALIST.yaml"
            record_path.write_text(
                "status: frozen\n"
                "validation_finalists: [spike-risk-first, spike-risk-first]\n"
                "selected_label: spike-risk-first\n"
                "selected_sha256: bad857e4298bf73efa4bcbfac114e67dfa03a9ff19545c5a2594d9de1016d51f\n",
                encoding="utf-8",
            )
            with mock.patch.object(experiment, "FINALIST_RECORD", record_path):
                errors = experiment.validate_heldout(
                    "spike-risk-first",
                    "bad857e4298bf73efa4bcbfac114e67dfa03a9ff19545c5a2594d9de1016d51f",
                )
        self.assertTrue(any("not one of the two frozen validation finalists" in error for error in errors))

    def test_runner_rejects_spike_overrides_and_cardinality_is_exact(self):
        script = """
          import { hasExactRowCardinality } from './evals/behavioral/selection.mjs';
          const providers = ['spike-current', 'spike-minimal'];
          const cases = ['a', 'b'];
          const rows = providers.flatMap((provider) => cases.map((case_id) => ({ provider: { label: provider }, metadata: { case_id } })));
          if (!hasExactRowCardinality(rows, providers, cases, 1)) process.exit(1);
          const unbalanced = [...Array(11).fill(rows[0]), rows[3]];
          if (hasExactRowCardinality(unbalanced, providers, cases, 1)) process.exit(2);
        """
        selection = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=experiment.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(selection.returncode, 0, selection.stderr)
        runner = subprocess.run(
            ["node", "evals/behavioral/run.mjs", "spike-validate", "--grader", "override"],
            cwd=experiment.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(runner.returncode, 2)
        development = subprocess.run(
            [
                "node",
                "evals/behavioral/run.mjs",
                "development-v2-validate",
                "--grader",
                "override",
            ],
            cwd=experiment.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(development.returncode, 2)
        development_v3 = subprocess.run(
            [
                "node",
                "evals/behavioral/run.mjs",
                "development-v3-validate",
                "--grader",
                "override",
            ],
            cwd=experiment.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(development_v3.returncode, 2)

    def test_development_runner_refuses_to_overwrite_raw_metrics_or_reservation(self):
        for mode in ("development-v2", "development-v3", "evidence-claims-v3-screen"):
            output = experiment.ROOT / f"evals/behavioral/results/{mode}.json"
            artifacts = [
                output,
                Path(f"{output}.metrics-v2.json"),
                Path(f"{output}.reservation.json"),
                Path(f"{output}.promptfoo"),
                Path(f"{output}.traces"),
                Path(f"{output}.schedule.json"),
                Path(f"{output}.accounting-v1.json"),
            ]
            output.parent.mkdir(parents=True, exist_ok=True)
            for artifact in artifacts:
                with self.subTest(mode=mode, artifact=artifact.name):
                    self.assertFalse(artifact.exists(), f"test refuses existing user artifact {artifact}")
                    if artifact.suffix in {".promptfoo", ".traces"}:
                        artifact.mkdir()
                    else:
                        artifact.write_text("preserve me\n", encoding="utf-8")
                    try:
                        result = subprocess.run(
                            ["node", "evals/behavioral/run.mjs", mode],
                            cwd=experiment.ROOT,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertEqual(result.returncode, 2)
                        self.assertIn("refuses to overwrite", result.stderr)
                        if artifact.is_file():
                            self.assertEqual(artifact.read_text(encoding="utf-8"), "preserve me\n")
                    finally:
                        if artifact.is_dir():
                            artifact.rmdir()
                        else:
                            artifact.unlink(missing_ok=True)

    def test_development_runner_applies_private_creation_policy(self):
        output = experiment.ROOT / "evals/behavioral/results/development-v2.json"
        metrics = Path(f"{output}.metrics-v2.json")
        reservation = Path(f"{output}.reservation.json")
        cache = Path(f"{output}.promptfoo")
        with tempfile.TemporaryDirectory() as temp:
            fake_bin = Path(temp) / "bin"
            fake_bin.mkdir()
            child_output = Path(temp) / "child-output"
            promptfoo = fake_bin / "promptfoo"
            promptfoo.write_text(
                '#!/bin/sh\n: > "$FAKE_CHILD_OUTPUT"\nexit 1\n', encoding="utf-8"
            )
            promptfoo.chmod(0o700)
            for artifact in (output, metrics, reservation, cache):
                self.assertFalse(artifact.exists(), f"test refuses existing user artifact {artifact}")
            try:
                result = subprocess.run(
                    ["node", "evals/behavioral/run.mjs", "development-v2"],
                    cwd=experiment.ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                    env={
                        **os.environ,
                        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                        "SPIKE_PYTHON": sys.executable,
                        "FAKE_CHILD_OUTPUT": str(child_output),
                    },
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(stat.S_IMODE(reservation.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(child_output.stat().st_mode), 0o600)
            finally:
                output.unlink(missing_ok=True)
                metrics.unlink(missing_ok=True)
                reservation.unlink(missing_ok=True)
                Path(f"{output}.schedule.json").unlink(missing_ok=True)
                Path(f"{output}.accounting-v1.json").unlink(missing_ok=True)
                shutil.rmtree(cache, ignore_errors=True)
                shutil.rmtree(Path(f"{output}.traces"), ignore_errors=True)

    def test_screen_runner_sets_private_traces_and_outside_workspaces(self):
        output = experiment.ROOT / "evals/behavioral/results/evidence-claims-v3-screen.json"
        artifacts = [output, *(Path(f"{output}{suffix}") for suffix in (
            ".metrics-v2.json", ".reservation.json", ".promptfoo", ".traces",
            ".schedule.json", ".accounting-v1.json"))]
        for artifact in artifacts:
            self.assertFalse(artifact.exists(), f"test refuses existing user artifact {artifact}")
        with tempfile.TemporaryDirectory() as temp:
            fake_bin = Path(temp) / "bin"
            fake_bin.mkdir()
            seen = Path(temp) / "seen"
            promptfoo = fake_bin / "promptfoo"
            promptfoo.write_text(
                '#!/bin/sh\nprintf "%s\\n%s\\n%s\\n" "$MUSE_EVAL_TRACE_DIR" '
                '"$MUSE_EVAL_WORKSPACE_PARENT" "$*" > "$FAKE_SEEN"\nexit 1\n',
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
                        "FAKE_SEEN": str(seen),
                        "MUSE_EVAL_WORKSPACE_BASE": str(Path(temp) / "outside"),
                    },
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                traces, parent, args = seen.read_text(encoding="utf-8").splitlines()
                self.assertEqual(Path(traces), Path(f"{output}.traces"))
                self.assertEqual(stat.S_IMODE(Path(traces).stat().st_mode), 0o700)
                self.assertTrue(parent.startswith(str(Path(temp) / "outside")))
                self.assertNotIn(str(experiment.ROOT), parent)
                for marker in muse_provider.GRADER_ONLY_MARKERS:
                    self.assertNotIn(marker, parent)
                self.assertEqual(stat.S_IMODE(Path(parent).stat().st_mode), 0o700)
                self.assertIn("--no-cache --repeat 1", args)
                self.assertIn("evidence-claims-v3-screen-promptfooconfig.yaml", args)
            finally:
                for artifact in artifacts:
                    if artifact.is_dir():
                        shutil.rmtree(artifact, ignore_errors=True)
                    else:
                        artifact.unlink(missing_ok=True)


    def test_screen_runner_accepts_a_complete_review_only_run(self):
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
                rows.append({
                    "provider": {"label": label},
                    "metadata": dict(case["metadata"]),
                    "vars": dict(case["vars"]),
                    "namedScores": {name: 1 for name in scoring.DETERMINISTIC_NAMED_SCORES},
                    "response": {"output": json.dumps({"verdict": verdict}),
                                 "metadata": {"graderBoundaryFlags": []}},
                })
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
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("verified 6 rows", result.stdout)
                metrics = json.loads(Path(f"{output}.metrics-v2.json").read_text(encoding="utf-8"))
                self.assertEqual(metrics["aggregate"]["completed"], 6)
                self.assertEqual(metrics["aggregate"]["verdict_accuracy"], 1)
            finally:
                for artifact in artifacts:
                    if artifact.is_dir():
                        shutil.rmtree(artifact, ignore_errors=True)
                    else:
                        artifact.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
