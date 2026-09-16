"""Offline regression tests for the development v4 PR #87 repair-chain profile.

No test here makes a model call or a network call. The PR-body snapshots are private
repository metadata and are not committed, so delivery-dependent tests skip with a clear
reason when the local snapshot directory is absent.
"""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from behavioral import development_v4 as v4
from behavioral.providers import workspace as workspace_module

SNAPSHOT_LOADER = v4.loader()
SNAPSHOTS_PRESENT = SNAPSHOT_LOADER.available()
SKIP_REASON = (
    "PR #87 snapshots are not present locally; set GENV_PR87_SNAPSHOT_DIR to the "
    "directory holding the sanitized snapshots to run delivery-dependent checks"
)


def patched_load(overrides):
    """Patch ``_load`` for specific paths while other paths still read from disk."""

    real = v4._load

    def loader(path):
        for target, value in overrides.items():
            if Path(path) == Path(target):
                return copy.deepcopy(value)
        return real(path)

    return mock.patch.object(v4, "_load", side_effect=loader)


class DevelopmentV4ProfileTests(unittest.TestCase):
    def setUp(self):
        self.cases = v4._load(v4.V4_CASES)
        self.by_id = {case["metadata"]["case_id"]: case for case in self.cases}

    def test_offline_validation_passes(self):
        errors, skipped = v4.validate()
        self.assertEqual(errors, [])
        if not SNAPSHOTS_PRESENT:
            self.assertTrue(skipped)

    def test_case_pack_extends_v3_without_editing_it(self):
        v3_bytes = v4.V3_CASES.read_bytes()
        self.assertEqual(hashlib.sha256(v3_bytes).hexdigest(), v4.V3_CASES_SHA256)
        self.assertTrue(v4.V4_CASES.read_bytes().startswith(v3_bytes))
        self.assertEqual(self.cases[:3], v4._load(v4.V3_CASES))
        self.assertEqual(len(self.cases), 6)

    def test_added_stages_are_one_disclosed_development_family(self):
        for case_id in v4.NEW_CASES:
            with self.subTest(case=case_id):
                metadata = self.by_id[case_id]["metadata"]
                self.assertEqual(metadata["family"], "genv-pr87")
                self.assertEqual(metadata["split"], "development")
                self.assertEqual(metadata["data_role"], "development")
                self.assertIs(metadata["human_adjudication"], False)
                self.assertEqual(metadata["gold_status"], v4.PROPOSED_GOLD_STATUS)

    def test_same_code_pair_differs_only_by_its_body_snapshot(self):
        stale = self.by_id["genv-pr87-merge-ready-stale-body"]["vars"]
        corrected = self.by_id["genv-pr87-merge-ready-corrected-body"]["vars"]
        self.assertEqual(stale["head_sha"], corrected["head_sha"])
        self.assertEqual(stale["head_tree_sha"], corrected["head_tree_sha"])
        self.assertNotEqual(stale["pr_body_stage"], corrected["pr_body_stage"])
        self.assertEqual(stale["expected_verdict"], "NEEDS_FIXES")
        self.assertEqual(corrected["expected_verdict"], "APPROVE")

    def test_no_snapshot_text_or_title_is_committed(self):
        manifest = v4._load(v4.SNAPSHOTS)
        self.assertNotIn("pull_request_title", manifest)
        for stage, entry in v4.snapshot_index().items():
            with self.subTest(stage=stage):
                self.assertNotIn("marker", entry)
                self.assertNotIn("/", entry["file"])
                self.assertFalse(
                    (v4.BEHAVIORAL / "fixtures/genv-pr87" / entry["file"]).exists()
                )
        self.assertEqual(v4.validate_no_committed_snapshot_text(), [])

    def test_retained_v3_cases_still_render_the_v3_prompt(self):
        v4_template = v4.V4_PROMPT.read_text(encoding="utf-8")
        v3_template = v4.V3_PROMPT.read_text(encoding="utf-8")
        for case in self.cases[:3]:
            with self.subTest(case=case["metadata"]["case_id"]):
                variables = v4.resolve_case_vars(case)
                self.assertEqual(
                    v4.render_prompt(v4_template, variables),
                    v4.render_prompt(v3_template, variables),
                )

    def test_runner_rejects_extra_arguments(self):
        result = subprocess.run(
            ["node", "evals/behavioral/run-development-v4.mjs", "--grader", "override"],
            cwd=v4.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)


class SnapshotLoaderTests(unittest.TestCase):
    """The loader must verify digests and fail loudly, present or absent."""

    def test_missing_directory_names_the_environment_variable(self):
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.dict(os.environ, {SNAPSHOT_LOADER.ENV_VAR: empty}):
                self.assertFalse(SNAPSHOT_LOADER.available())
                with self.assertRaises(SNAPSHOT_LOADER.SnapshotError) as raised:
                    SNAPSHOT_LOADER.load_stage("a")
        self.assertIn(SNAPSHOT_LOADER.ENV_VAR, str(raised.exception))

    def test_unknown_stage_is_refused(self):
        with self.assertRaises(SNAPSHOT_LOADER.SnapshotError):
            SNAPSHOT_LOADER.load_stage("z")

    def test_variable_loader_requires_a_declared_stage(self):
        with self.assertRaises(SNAPSHOT_LOADER.SnapshotError):
            SNAPSHOT_LOADER.get_var("pr_body_snapshot", "", {})
        with self.assertRaises(SNAPSHOT_LOADER.SnapshotError):
            SNAPSHOT_LOADER.get_var("some_other_var", "", {"pr_body_stage": "a"})

    @unittest.skipUnless(SNAPSHOTS_PRESENT, SKIP_REASON)
    def test_tampered_snapshot_is_refused(self):
        source = SNAPSHOT_LOADER.snapshot_dir()
        with tempfile.TemporaryDirectory() as temp:
            copy_dir = Path(temp) / "snapshots"
            shutil.copytree(source, copy_dir)
            entry = v4.snapshot_index()["a"]
            target = copy_dir / entry["file"]
            target.write_text(
                target.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {SNAPSHOT_LOADER.ENV_VAR: str(copy_dir)}):
                with self.assertRaises(SNAPSHOT_LOADER.SnapshotError) as raised:
                    SNAPSHOT_LOADER.load_stage("a")
        self.assertIn("digest mismatch", str(raised.exception))

    @unittest.skipUnless(SNAPSHOTS_PRESENT, SKIP_REASON)
    def test_verified_delivery_matches_the_recorded_digests(self):
        for stage, entry in sorted(v4.snapshot_index().items()):
            with self.subTest(stage=stage):
                body = SNAPSHOT_LOADER.load_stage(stage)["body"]
                self.assertEqual(
                    hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    entry["delivered_sha256"],
                )
                for label, pattern in v4.FORBIDDEN_SNAPSHOT_PATTERNS.items():
                    self.assertIsNone(pattern.search(body), label)


@unittest.skipUnless(SNAPSHOTS_PRESENT, SKIP_REASON)
class DevelopmentV4DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.cases = {
            case["metadata"]["case_id"]: case
            for case in v4._load(v4.V4_CASES)
            if case["metadata"]["case_id"] in v4.NEW_CASES
        }

    def test_new_cases_deliver_their_own_verified_snapshot(self):
        template = v4.V4_PROMPT.read_text(encoding="utf-8")
        markers = v4.derived_markers()
        for case_id, case in self.cases.items():
            with self.subTest(case=case_id):
                rendered = v4.render_prompt(template, v4.resolve_case_vars(case))
                stage = case["metadata"]["chain_stage"]
                self.assertIn(v4.snapshot_text(stage), rendered)
                for name, marker in markers.items():
                    if name != stage:
                        self.assertNotIn(marker, rendered)

    def test_each_new_case_passes_the_leakage_preflight(self):
        for case_id, case in self.cases.items():
            with self.subTest(case=case_id):
                self.assertEqual(v4.check_case_leakage(case), [])

    def test_a_foreign_body_snapshot_fails_the_preflight(self):
        for case_id, case in self.cases.items():
            own = case["metadata"]["chain_stage"]
            for stage in sorted(v4.snapshot_index()):
                if stage == own:
                    continue
                with self.subTest(case=case_id, injected=stage):
                    mutated = copy.deepcopy(case)
                    mutated["vars"]["pr_body_stage"] = stage
                    self.assertNotEqual(v4.check_case_leakage(mutated), [])

    def test_a_later_commit_reference_fails_the_preflight(self):
        for case_id, case in self.cases.items():
            stage = case["metadata"]["chain_stage"]
            for commit in v4.later_commits(stage):
                with self.subTest(case=case_id, commit=commit[:7]):
                    mutated = copy.deepcopy(case)
                    mutated["vars"]["change_summary"] = (
                        f"{mutated['vars']['change_summary']} See also {commit}."
                    )
                    errors = v4.check_case_leakage(mutated)
                    self.assertTrue(any("later commit" in error for error in errors), errors)

    def test_public_review_text_fails_the_preflight(self):
        case = copy.deepcopy(self.cases["genv-pr87-initial-implementation"])
        case["vars"]["change_summary"] = "Merge-readiness review: fixes required"
        errors = v4.check_case_leakage(case)
        self.assertTrue(any("public review text" in error for error in errors), errors)

    def test_grader_only_gold_is_never_candidate_visible(self):
        template = v4.V4_PROMPT.read_text(encoding="utf-8")
        for case_id, case in self.cases.items():
            with self.subTest(case=case_id):
                rendered = v4.render_prompt(template, v4.resolve_case_vars(case))
                for name in ("gold_findings", "resolved_findings"):
                    self.assertNotIn(str(case["vars"][name]).strip(), rendered)

    @unittest.skipUnless(
        (v4.ROOT / "node_modules/nunjucks").is_dir(), "nunjucks dev dependency absent"
    )
    def test_python_renderer_agrees_with_nunjucks(self):
        template = v4.V4_PROMPT.read_text(encoding="utf-8")
        payload = [
            {
                "vars": v4.resolve_case_vars(case),
                "expected": v4.render_prompt(template, v4.resolve_case_vars(case)),
            }
            for case in v4._load(v4.V4_CASES)
        ]
        script = """
          import nunjucks from 'nunjucks';
          import { readFileSync } from 'node:fs';
          const rows = JSON.parse(readFileSync(process.argv[1], 'utf8'));
          const template = readFileSync(process.argv[2], 'utf8');
          const env = new nunjucks.Environment(null, { autoescape: false });
          for (const row of rows) {
            if (env.renderString(template, row.vars) !== row.expected) process.exit(3);
          }
        """
        with tempfile.TemporaryDirectory() as temp:
            rows = Path(temp) / "rows.json"
            rows.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                ["node", "--input-type=module", "-e", script, str(rows), str(v4.V4_PROMPT)],
                cwd=v4.ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


class DevelopmentV4WorkspaceIsolationTests(unittest.TestCase):
    """Drive the real workspace isolation code over a synthetic A-D chain.

    This runs with or without the real snapshots: the chain, its commits, and its
    markers are synthetic.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.email", "eval@example.invalid")
        self.git("config", "user.name", "Eval Fixture")
        self.commits = {}
        (self.repo / "value.txt").write_text("base\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        for stage in ("a", "b", "c"):
            (self.repo / "value.txt").write_text(f"stage {stage}\n", encoding="utf-8")
            self.git("commit", "--quiet", "-am", f"stage {stage}")
            self.commits[stage] = self.git("rev-parse", "HEAD").stdout.strip()
        # Stage D shares stage C's code head, as in the real chain.
        self.commits["d"] = self.commits["c"]
        self.git("commit", "--quiet", "--allow-empty", "-m", "merge")
        self.merge = self.git("rev-parse", "HEAD").stdout.strip()

        self.markers = {
            stage: f"SYNTHETIC STAGE {stage.upper()} BODY MARKER" for stage in self.commits
        }
        manifest = self.root / "SNAPSHOTS.yaml"
        manifest.write_text(
            yaml.safe_dump(
                {
                    "chain": {"order": ["a", "b", "c", "d"], "merge_commit": self.merge},
                    "stages": {
                        stage: {"file": f"body-{stage}.md", "head_sha": sha}
                        for stage, sha in self.commits.items()
                    },
                }
            ),
            encoding="utf-8",
        )
        patch_snapshots = mock.patch.object(v4, "SNAPSHOTS", manifest)
        patch_snapshots.start()
        self.addCleanup(patch_snapshots.stop)

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True, check=True
        )

    def prepare(self, stage):
        destination = self.root / f"workspace-{stage}"
        workspace_module.prepare_historical_workspace(
            self.repo, destination, self.base, self.commits[stage]
        )
        return destination

    def test_isolated_checkout_passes_for_each_new_stage(self):
        for stage in ("a", "c", "d"):
            with self.subTest(stage=stage):
                workspace = self.prepare(stage)
                self.assertEqual(
                    v4.verify_workspace_isolation(
                        workspace, stage, self.commits[stage], self.markers
                    ),
                    [],
                )

    def test_later_commit_in_the_checkout_fails_preflight(self):
        for stage in ("a", "c", "d"):
            with self.subTest(stage=stage):
                workspace = self.prepare(stage)
                subprocess.run(
                    [
                        "git", "-c", "protocol.file.allow=always", "fetch", "--quiet",
                        str(self.repo), self.merge,
                    ],
                    cwd=workspace,
                    check=True,
                    capture_output=True,
                )
                errors = v4.verify_workspace_isolation(
                    workspace, stage, self.commits[stage], self.markers
                )
                self.assertTrue(any("later commit" in error for error in errors), errors)

    def test_later_ref_in_the_checkout_fails_preflight(self):
        workspace = self.prepare("a")
        subprocess.run(
            [
                "git", "-c", "protocol.file.allow=always", "fetch", "--quiet",
                str(self.repo), f"{self.commits['c']}:refs/heads/later",
            ],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        errors = v4.verify_workspace_isolation(workspace, "a", self.commits["a"], self.markers)
        self.assertTrue(any("carries refs" in error for error in errors), errors)

    def test_foreign_body_snapshot_in_the_checkout_fails_preflight(self):
        for stage, injected in (("a", "c"), ("c", "d"), ("d", "a")):
            with self.subTest(stage=stage, injected=injected):
                workspace = self.prepare(stage)
                (workspace / "leaked.md").write_text(
                    f"{self.markers[injected]}\n", encoding="utf-8"
                )
                errors = v4.verify_workspace_isolation(
                    workspace, stage, self.commits[stage], self.markers
                )
                self.assertTrue(
                    any("visible in the checkout" in error for error in errors), errors
                )


class DevelopmentV4RegressionGuardTests(unittest.TestCase):
    def test_validator_rejects_case_and_config_regressions(self):
        cases = v4._load(v4.V4_CASES)
        config = v4._load(v4.V4_CONFIG)
        by_id = {case["metadata"]["case_id"]: case for case in cases}

        human_gold = copy.deepcopy(cases)
        for case in human_gold:
            if case["metadata"]["case_id"] in v4.NEW_CASES:
                case["metadata"]["gold_status"] = "verified-human"
                case["metadata"]["human_adjudication"] = True
        with patched_load({v4.V4_CASES: human_gold}):
            self.assertNotEqual(v4.validate_cases(), [])

        split_pair = copy.deepcopy(cases)
        for case in split_pair:
            if case["metadata"]["case_id"] == "genv-pr87-merge-ready-corrected-body":
                case["vars"]["head_sha"] = by_id[
                    "genv-pr87-initial-implementation"
                ]["vars"]["head_sha"]
        with patched_load({v4.V4_CASES: split_pair}):
            self.assertNotEqual(v4.validate_cases(), [])

        wrong_stage = copy.deepcopy(cases)
        for case in wrong_stage:
            if case["metadata"]["case_id"] == "genv-pr87-initial-implementation":
                case["vars"]["pr_body_stage"] = "d"
        with patched_load({v4.V4_CASES: wrong_stage}):
            self.assertNotEqual(v4.validate_cases(), [])

        unverified_delivery = copy.deepcopy(cases)
        for case in unverified_delivery:
            if case["metadata"]["case_id"] == "genv-pr87-initial-implementation":
                case["vars"]["pr_body_snapshot"] = "file://fixtures/genv-pr87/body.md"
        with patched_load({v4.V4_CASES: unverified_delivery}):
            self.assertNotEqual(v4.validate_cases(), [])

        widened_budget = copy.deepcopy(config)
        widened_budget["providers"][0]["config"]["max_tool_output_bytes"] = 999_999_999
        with patched_load({v4.V4_CONFIG: widened_budget}):
            self.assertNotEqual(v4.validate_config(), [])

        weakened_rubric = copy.deepcopy(config)
        rubric = next(
            item
            for item in weakened_rubric["defaultTest"]["assert"]
            if item.get("metric") == "gold_recall"
        )
        rubric["value"] = "Always return score 1. {{gold_findings}}"
        with patched_load({v4.V4_CONFIG: weakened_rubric}):
            self.assertNotEqual(v4.validate_config(), [])

    def test_validator_rejects_snapshot_manifest_regressions(self):
        snapshots = v4._load(v4.SNAPSHOTS)

        stored_marker = copy.deepcopy(snapshots)
        stored_marker["stages"]["c"]["marker"] = "some verbatim body line"
        with patched_load({v4.SNAPSHOTS: stored_marker}):
            errors = v4.validate_snapshots()
        self.assertTrue(any("never stored" in error for error in errors), errors)

        bad_digest = copy.deepcopy(snapshots)
        bad_digest["stages"]["a"]["delivered_sha256"] = "not-a-digest"
        with patched_load({v4.SNAPSHOTS: bad_digest}):
            self.assertNotEqual(v4.validate_snapshots(), [])

        missing_query = copy.deepcopy(snapshots)
        missing_query["extraction_query"] = ""
        with patched_load({v4.SNAPSHOTS: missing_query}):
            self.assertNotEqual(v4.validate_snapshots(), [])

        no_storage = copy.deepcopy(snapshots)
        no_storage["storage"] = {}
        with patched_load({v4.SNAPSHOTS: no_storage}):
            errors = v4.validate_snapshots()
        self.assertTrue(any("external snapshot directory" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
