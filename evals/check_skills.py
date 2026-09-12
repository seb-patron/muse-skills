#!/usr/bin/env python3
"""Structural lint for skill files. Standard library only.

Exit 0 when every check passes, 1 otherwise. Each check searches within the
section that owns the text, so a routing paragraph that merely names a probe
cannot shadow a deleted catalogue row.

Checks:
  1. Frontmatter: the leading YAML block parses; `name` equals the skill
     directory name; `description` is present and non-empty (wording not pinned).
  2. Probe catalogue (adversarial-review): the `## Probe catalogue` section
     holds exactly 14 `- **...**` rows, each with its expected name.
  3. Load-bearing rule clauses: each pinned clause is present inside its own
     section (headings alone do not pass).
  4. Layout: from `git ls-files skills/`, every tracked skill directory holds
     a `SKILL.md`, and every listed `SKILL.md` exists on disk. Supporting
     files are allowed, untracked files are ignored, and no total file count
     is pinned, so new skills do not break the lint.

Usage: python3 evals/check_skills.py  (run from anywhere; repo root is derived
from this file's location)
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"

# Expected probe-row names, in catalogue order.
EXPECTED_PROBE_ROWS = [
    "Isolated-import probe",
    "Revert-probe new tests",
    "Linked-worktree / common-dir graft",
    "Non-UTF8 and FIFO inputs",
    "Env-strip removal matrix",
    "Alias-form guard evasions",
    "Moved-code coverage",
    "Lone-file vendor",
    "Workspace-copy execution",
    "Dirty / sdist / foreign-HEAD builds",
    "Prog-name-un-normalized diff",
    "Exit-status-discarding mutants",
    "Verbatim-doc run",
    "Digest-mutation check",
]

# (skill, section heading, load-bearing clause) — the clause must appear
# inside that section, not just anywhere in the file.
EXPECTED_RULES = [
    ("adversarial-review", "Trigger",
     "confirm `git rev-parse HEAD` equals the reviewed SHA"),
    ("adversarial-review", "Refutation pass",
     "Each finding gets one refute attempt with a logged command"),
    ("adversarial-review", "Verdict rules",
     "APPROVE requires zero unrefuted blockers"),
    ("adversarial-review", "Verdict rules",
     "Revision rounds: 0/3"),
    ("fix-verification", "1. Build the traceability table",
     "one table row per prior finding"),
    ("fix-verification", "2. Diff-to-row mapping",
     "A row with no cited hunks stays open"),
    ("fix-verification", "3. Re-run each repro on the final head",
     "Paste the exact command, exit code"),
    ("fix-verification", "4. Rebase and stack re-verification",
     "redo steps 2\u20133 on the new head"),
    ("fix-verification", "6. Update the revision counter",
     "After 3/3 with blockers remaining, stop and request a human decision"),
    ("fix-verification", "6. Update the revision counter",
     "Revision rounds: N/3"),
    ("fix-verification", "7. Close or carry",
     "never silently park a row"),
]


def read_skill(skill):
    path = SKILLS_DIR / skill / "SKILL.md"
    return path.read_text(encoding="utf-8")


def parse_frontmatter(text):
    """Return the leading --- delimited block as a dict of key -> value."""
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return None
    fields = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


def section_text(text, heading):
    """Return the body of the `## <heading>` section (up to the next `## `)."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line == f"## {heading}":
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return "\n".join(lines[start:end])


def probe_row_headers(catalogue_section):
    headers = []
    for line in catalogue_section.splitlines():
        match = re.match(r"^- \*\*(.+?)\*\*", line)
        if match:
            headers.append(match.group(1))
    return headers


def tracked_skill_files():
    out = subprocess.run(
        ["git", "ls-files", "-z", "skills/"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    return [p for p in out.stdout.split("\0") if p]


def main():
    failures = []
    passes = []

    def check(label, ok, detail=""):
        (passes if ok else failures).append(label)
        print(f"{'ok' if ok else 'FAIL'}  {label}"
              + (f" — {detail}" if detail and not ok else ""))

    skill_files = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in SKILLS_DIR.glob("*/SKILL.md")) if SKILLS_DIR.is_dir() else []

    # 1. Frontmatter per skill file found on disk.
    for rel in skill_files:
        skill = rel.split("/")[1]
        text = read_skill(skill)
        fields = parse_frontmatter(text)
        check(f"{skill}: frontmatter parses", fields is not None, rel)
        if fields is None:
            continue
        check(f"{skill}: name == directory name",
              fields.get("name") == skill,
              f"name={fields.get('name')!r} dir={skill!r}")
        check(f"{skill}: description present and non-empty",
              bool(fields.get("description")),
              "description missing or empty")

    # 2. Probe catalogue rows, counted inside their own section.
    adv = read_skill("adversarial-review")
    catalogue = section_text(adv, "Probe catalogue")
    check("adversarial-review: Probe catalogue section exists",
          catalogue is not None)
    if catalogue is not None:
        headers = probe_row_headers(catalogue)
        check("adversarial-review: exactly 14 probe rows",
              len(headers) == len(EXPECTED_PROBE_ROWS),
              f"found {len(headers)}")
        for name in EXPECTED_PROBE_ROWS:
            check(f"adversarial-review: probe row '{name}'",
                  any(name in h for h in headers))

    # 3. Load-bearing clauses inside their own sections.
    texts = {"adversarial-review": adv,
             "fix-verification": read_skill("fix-verification")}
    for skill, heading, clause in EXPECTED_RULES:
        body = section_text(texts[skill], heading)
        check(f"{skill} [{heading}]: '{clause[:48]}...'",
              body is not None and clause in body,
              "section missing" if body is None else "clause missing")

    # 4. Layout from tracked files: one SKILL.md per skill dir, all on disk.
    tracked = tracked_skill_files()
    check("layout: git ls-files runs", tracked is not None)
    if tracked is not None:
        dirs = sorted({str(Path(p).parent) for p in tracked})
        check("layout: at least one skill directory", len(dirs) > 0)
        for d in dirs:
            names = [Path(p).name for p in tracked if str(Path(p).parent) == d]
            check(f"layout: {d}/ holds SKILL.md", "SKILL.md" in names)
        for p in tracked:
            if Path(p).name == "SKILL.md":
                check(f"layout: {p} exists on disk",
                      (REPO_ROOT / p).is_file())

    print(f"\n{len(passes)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
