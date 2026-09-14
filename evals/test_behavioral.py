"""Offline regression tests for the behavioral provider and assertions."""

import copy
import hashlib
import json
import subprocess
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
            "payload": {"text": "{}"},
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
        self.assertTrue(rows[0]["completion"])
        self.assertIsNone(rows[0]["error"])
        self.assertEqual(rows[0]["assertion_error"], "quality assertion failed")
        self.assertIsNone(rows[1]["all_gold_recall"])
        summary = scoring.aggregate(rows)
        self.assertEqual(summary["candidate_token_rows"], 1)
        self.assertEqual(summary["candidate_tokens"], 123)

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


if __name__ == "__main__":
    unittest.main()
