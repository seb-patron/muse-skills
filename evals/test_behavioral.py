"""Offline regression tests for the behavioral provider and assertions."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from behavioral.assertions import review_contract
from behavioral.providers import muse_provider


class MuseProviderTests(unittest.TestCase):
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

    def test_invalid_json_and_missing_evidence_fail(self):
        context = {"vars": {"head_sha": "abcdef123456", "expected_verdict": "APPROVE"}}
        self.assertFalse(review_contract.assert_review_contract("not json", context)["pass"])
        value = json.loads(self.review("APPROVE"))
        value["findings"][0]["repro"]["output"] = ""
        value["checks"] = []
        self.assertFalse(review_contract.assert_evidence(json.dumps(value), context)["pass"])


if __name__ == "__main__":
    unittest.main()
