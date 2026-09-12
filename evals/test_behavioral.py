"""Offline regression tests for the behavioral provider and assertions."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from behavioral.assertions import review_contract
from behavioral.providers import codex_provider, muse_provider


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

    def test_rejects_non_sha_and_non_ancestor(self):
        with self.assertRaises(muse_provider.ProviderError):
            muse_provider._resolve_commit(self.repo, "HEAD; echo unsafe")
        with self.assertRaises(muse_provider.ProviderError):
            muse_provider._prepare_workspace(
                self.repo, Path(self.temp.name) / "wrong", self.head, self.base, "none", "example"
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


if __name__ == "__main__":
    unittest.main()
