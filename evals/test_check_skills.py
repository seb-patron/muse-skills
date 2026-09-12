"""Regression tests using temporary files, real Git indexes, and the CLI."""

import contextlib
import io
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_skills


SOURCE_ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("adversarial-review", "fix-verification")
ORIGINALS = {
    skill: (SOURCE_ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    for skill in SKILLS
}


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "--quiet")
        for skill, text in ORIGINALS.items():
            self.write(f"skills/{skill}/SKILL.md", text)
        self.git("add", "skills")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True,
            capture_output=True, text=True,
        )

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def change(self, skill, old, new):
        self.assertIn(old, ORIGINALS[skill])
        self.write(f"skills/{skill}/SKILL.md", ORIGINALS[skill].replace(old, new, 1))

    def lint(self, expected):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = check_skills.main(self.root)
        self.assertEqual(result, expected, output.getvalue())
        if expected == 0:
            self.assertNotIn("FAIL", output.getvalue())
        else:
            self.assertIn("FAIL", output.getvalue())
        return output.getvalue()

    def frontmatter(self, value):
        body = ORIGINALS["adversarial-review"].split("---", 2)[2]
        self.write("skills/adversarial-review/SKILL.md", f"---\n{value}\n---{body}")

    def test_baseline(self):
        self.lint(0)

    def test_valid_yaml_forms(self):
        for text in (
            'name: "adversarial-review"\ndescription: "A quoted description"',
            "name: adversarial-review # identifier\ndescription: A new description",
            "name: adversarial-review\ndescription: |\n  A description with\n  name: inside its body",
            "name: adversarial-review\ndescription: >\n  A folded\n  description",
            '{name: adversarial-review, description: "Flow mapping"}',
        ):
            with self.subTest(text=text):
                self.frontmatter(text)
                self.lint(0)

    def test_invalid_yaml(self):
        for value in ("[", '"unterminated', "{broken"):
            with self.subTest(value=value):
                self.frontmatter(f"name: adversarial-review\ndescription: {value}")
                self.assertIn("invalid YAML", self.lint(1))

    def test_description_must_be_nonblank_string(self):
        for value in ('""', "''", '"   "', "null", "~", "# comment", "|",
                      "[]", "{}", "123", "true"):
            with self.subTest(value=value):
                self.frontmatter(f"name: adversarial-review\ndescription: {value}")
                self.assertIn("description must be a nonblank YAML string", self.lint(1))
        self.frontmatter("name: adversarial-review")
        self.lint(1)

    def test_name_must_match_directory(self):
        for name in ("wrong-name", "null", "123", "[]"):
            with self.subTest(name=name):
                self.frontmatter(f"name: {name}\ndescription: Valid")
                self.lint(1)

    def test_frontmatter_must_be_mapping(self):
        for text in ("", "null", "- item", "a scalar"):
            with self.subTest(text=text):
                self.frontmatter(text)
                self.assertIn("must be a YAML mapping", self.lint(1))

    def test_unsafe_yaml_tags_are_rejected(self):
        self.frontmatter('name: adversarial-review\ndescription: !!python/tuple [one, two]')
        self.assertIn("invalid YAML", self.lint(1))

    def test_frontmatter_delimiters(self):
        for text in ("# No metadata\n", "---\nname: adversarial-review\n"):
            with self.subTest(text=text):
                self.write("skills/adversarial-review/SKILL.md", text)
                self.assertIn("missing leading", self.lint(1))
        fields = check_skills.parse_frontmatter(
            '---\r\nname: example\r\ndescription: Example\r\n---'
        )
        self.assertEqual(fields, {"name": "example", "description": "Example"})

    def test_support_files_and_collection_documentation(self):
        for rel in ("skills/adversarial-review/reference/example.md",
                    "skills/adversarial-review/example.md",
                    "skills/fix-verification/scripts/nested/helper.py",
                    "skills/README.md"):
            self.write(rel, "Supporting content\n")
        self.git("add", "skills")
        self.lint(0)

    def test_nested_skill_named_support_file_does_not_become_root(self):
        self.write("skills/adversarial-review/reference/SKILL.md", "Example document\n")
        self.git("add", "skills")
        self.lint(0)

    def test_untracked_files_are_ignored(self):
        self.write("skills/scratch/SKILL.md", "invalid draft\n")
        self.write("skills/.DS_Store", "junk")
        self.write("skills/adversarial-review/reference/draft.md", "draft")
        self.lint(0)

    def test_new_tracked_skill(self):
        self.write("skills/third/SKILL.md", "---\nname: third\ndescription: Third\n---\n")
        self.git("add", "skills")
        self.lint(0)
        self.write("skills/third/SKILL.md", "---\nname: wrong\ndescription: Third\n---\n")
        self.lint(1)

    def test_support_only_skill_root_fails(self):
        self.write("skills/third/reference/example.md", "support only\n")
        # An untracked root file must not satisfy the tracked layout contract.
        self.write("skills/third/SKILL.md", "---\nname: third\ndescription: Third\n---\n")
        self.git("add", "skills/third/reference/example.md")
        self.assertIn("skills/third/ holds tracked SKILL.md", self.lint(1))

    def test_missing_required_skill_file_reports_failure(self):
        for skill in SKILLS:
            with self.subTest(skill=skill):
                path = self.root / "skills" / skill / "SKILL.md"
                path.unlink()
                self.assertIn("exists on disk", self.lint(1))
                self.write(f"skills/{skill}/SKILL.md", ORIGINALS[skill])

    def test_staged_removal_cannot_be_hidden_by_untracked_copy(self):
        self.git("rm", "--cached", "skills/fix-verification/SKILL.md")
        self.assertIn("required skill 'fix-verification' is tracked", self.lint(1))

    def test_invalid_utf8_reports_failure(self):
        (self.root / "skills/fix-verification/SKILL.md").write_bytes(b"\xff")
        self.assertIn("readable as UTF-8", self.lint(1))

    def test_file_read_error_reports_failure(self):
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path == self.root / "skills/fix-verification/SKILL.md":
                raise PermissionError("fixture permission error")
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            self.assertIn("fixture permission error", self.lint(1))

    def test_git_command_failure_reports_failure(self):
        shutil.rmtree(self.root / ".git")
        self.assertIn("git ls-files failed", self.lint(1))

    def test_unavailable_git_reports_failure(self):
        with patch.object(check_skills.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertIn("cannot enumerate tracked skills", self.lint(1))

    def test_each_catalogue_row_deletion_fails(self):
        rows = re.findall(r"(?m)^- \*\*.*$", ORIGINALS["adversarial-review"])
        self.assertEqual(len(rows), 14)
        for row in rows:
            with self.subTest(row=row):
                self.change("adversarial-review", row + "\n", "")
                self.lint(1)

    def test_expected_name_elsewhere_does_not_replace_row(self):
        row = next(line for line in ORIGINALS["adversarial-review"].splitlines()
                   if line.startswith("- **Isolated-import"))
        self.change("adversarial-review", row, "Isolated-import probe mentioned in prose.")
        self.lint(1)

    def test_renamed_or_combined_headers_fail(self):
        for header in (
            "Isolated-import probe (legacy)",
            "Old Isolated-import probe (miss #75-1).",
            "Isolated-import probe / Revert-probe new tests (miss #75-1).",
            "Isolated-import probe (miss #75-1). trailing text",
        ):
            with self.subTest(header=header):
                self.change("adversarial-review", "Isolated-import probe (miss #75-1).", header)
                self.lint(1)

    def test_one_header_cannot_cover_two_expected_identities(self):
        text = ORIGINALS["adversarial-review"].replace(
            "Isolated-import probe (miss #75-1).",
            "Isolated-import probe / Revert-probe new tests (miss #75-1).", 1
        ).replace("Revert-probe new tests (misses #75-1, #76-5).", "Unrelated placeholder", 1)
        self.write("skills/adversarial-review/SKILL.md", text)
        self.lint(1)

    def test_duplicate_or_extra_probe_fails(self):
        self.change("adversarial-review", "Revert-probe new tests (misses #75-1, #76-5).",
                    "Isolated-import probe (miss #75-1).")
        self.lint(1)
        self.change("adversarial-review", "## Refutation pass",
                    "- **Unknown probe** Instructions\n\n## Refutation pass")
        self.lint(1)

    def test_reordering_and_supported_provenance_changes_pass(self):
        text = ORIGINALS["adversarial-review"]
        rows = re.findall(r"(?m)^- \*\*.*$", text)
        text = text.replace(rows[0] + "\n" + rows[1], rows[1] + "\n" + rows[0], 1)
        self.write("skills/adversarial-review/SKILL.md", text)
        self.lint(0)
        for header in ("Isolated-import probe", "Isolated-import probe (miss #90-2).",
                       "Isolated-import probe (misses #90-2, #91-3)."):
            with self.subTest(header=header):
                self.change("adversarial-review", "Isolated-import probe (miss #75-1).", header)
                self.lint(0)

    def test_each_pinned_clause_deletion_fails_even_if_copied_elsewhere(self):
        for skill, heading, clause in check_skills.EXPECTED_RULES:
            with self.subTest(skill=skill, heading=heading, clause=clause):
                for original_skill, text in ORIGINALS.items():
                    self.write(f"skills/{original_skill}/SKILL.md", text)
                body = check_skills.section_text(ORIGINALS[skill], heading)
                self.assertIn(clause, body)
                edited = ORIGINALS[skill].replace(body, body.replace(clause, "", 1), 1)
                edited += f"\n## Unrelated section\n\n{clause}\n"
                self.write(f"skills/{skill}/SKILL.md", edited)
                self.lint(1)

    def test_harmless_rule_line_wrapping_passes(self):
        self.change("adversarial-review", "APPROVE requires zero unrefuted blockers",
                    "APPROVE requires zero\nunrefuted blockers")
        self.lint(0)

    def test_cli_from_another_directory_and_failure_exit_status(self):
        entrypoint = self.root / "evals/check_skills.py"
        entrypoint.parent.mkdir()
        shutil.copyfile(SOURCE_ROOT / "evals/check_skills.py", entrypoint)
        result = subprocess.run([sys.executable, str(entrypoint)], cwd=self.root.parent,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.root / "skills/fix-verification/SKILL.md").unlink()
        result = subprocess.run([sys.executable, str(entrypoint)], cwd=self.root.parent,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
