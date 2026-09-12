#!/usr/bin/env python3
"""Structural lint for tracked skill files; requires evals/requirements.txt.

Exit 0 when all applicable checks pass, 1 otherwise, reporting all failures
that can be checked with the available inputs. Validate YAML metadata, exact
probe identities, selected section-scoped text fragments, and skill roots.
Nested supporting files and untracked drafts do not become skill roots.

This is text validation, not a Markdown semantic checker or a Muse run.
Probe bodies and instruction meaning are not validated.

Usage: python3 evals/check_skills.py  (run from anywhere; repo root is derived
from this file's location)
"""

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

try:
    import yaml
except ModuleNotFoundError:
    raise SystemExit(
        "PyYAML is required; install with: "
        "python3 -m pip install -r evals/requirements.txt"
    ) from None

REPO_ROOT = Path(__file__).resolve().parents[1]

# Expected identities; catalogue order is not part of the contract.
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


def parse_frontmatter(text):
    """Parse a leading --- block as a safe YAML mapping, or raise ValueError."""
    match = re.match(r"\A---\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)",
                     text, re.DOTALL | re.MULTILINE)
    if not match:
        raise ValueError("missing leading --- delimited frontmatter")
    try:
        fields = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    if not isinstance(fields, dict):
        raise ValueError("frontmatter must be a YAML mapping")
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


def probe_identity(header):
    """Accept an exact name, optionally followed by the known miss suffix."""
    return re.sub(
        r" \(miss(?:es)? #\d+-\d+(?:, #\d+-\d+)*\)\.$", "", header
    )


def tracked_skill_files(repo_root):
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", "skills/"],
            cwd=repo_root, capture_output=True, text=True,
        )
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot enumerate tracked skills: {exc}") from exc
    if out.returncode != 0:
        raise ValueError(f"git ls-files failed: {out.stderr.strip()}")
    return [p for p in out.stdout.split("\0") if p]


def main(repo_root=REPO_ROOT):
    repo_root = Path(repo_root)
    failures = []
    passes = []

    def check(label, ok, detail=""):
        (passes if ok else failures).append(label)
        print(f"{'ok' if ok else 'FAIL'}  {label}"
              + (f" — {detail}" if detail and not ok else ""))

    # Use the same Git-index discovery for both layout and content checks.
    try:
        tracked = set(tracked_skill_files(repo_root))
    except ValueError as exc:
        check("layout: git ls-files runs", False, str(exc))
        print(f"\n{len(passes)} passed, {len(failures)} failed")
        return 1
    check("layout: git ls-files runs", True)
    dirs = sorted({PurePosixPath(p).parts[1] for p in tracked
                   if len(PurePosixPath(p).parts) >= 3})
    check("layout: at least one skill directory", bool(dirs))
    for skill in dirs:
        rel = f"skills/{skill}/SKILL.md"
        check(f"layout: skills/{skill}/ holds tracked SKILL.md", rel in tracked)
    for skill in sorted({skill for skill, _, _ in EXPECTED_RULES}):
        check(f"layout: required skill '{skill}' is tracked",
              f"skills/{skill}/SKILL.md" in tracked)
    for rel in sorted(tracked):
        if PurePosixPath(rel).name == "SKILL.md":
            check(f"layout: {rel} exists on disk", (repo_root / rel).is_file())

    # Parse only tracked root SKILL.md files, reading current working-tree bytes.
    texts = {}
    for skill in dirs:
        rel = f"skills/{skill}/SKILL.md"
        path = repo_root / rel
        if rel not in tracked or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            check(f"{skill}: SKILL.md readable as UTF-8", False, str(exc))
            continue
        texts[skill] = text
        try:
            fields = parse_frontmatter(text)
        except ValueError as exc:
            check(f"{skill}: frontmatter parses", False, str(exc))
            continue
        check(f"{skill}: frontmatter parses", True)
        check(f"{skill}: name == directory name",
              isinstance(fields.get("name"), str) and fields["name"] == skill,
              f"name={fields.get('name')!r} dir={skill!r}")
        check(f"{skill}: description present and non-empty",
              isinstance(fields.get("description"), str)
              and bool(fields["description"].strip()),
              "description must be a nonblank YAML string")

    # Probe identities are section-scoped and must each occur exactly once.
    adv = texts.get("adversarial-review", "")
    catalogue = section_text(adv, "Probe catalogue")
    check("adversarial-review: Probe catalogue section exists",
          catalogue is not None)
    if catalogue is not None:
        headers = probe_row_headers(catalogue)
        identities = Counter(probe_identity(header) for header in headers)
        check("adversarial-review: exactly 14 probe rows",
              len(headers) == len(EXPECTED_PROBE_ROWS),
              f"found {len(headers)}")
        for name in EXPECTED_PROBE_ROWS:
            check(f"adversarial-review: probe row '{name}'",
                  identities[name] == 1,
                  f"expected once, found {identities[name]}")
        unexpected = sorted(set(identities) - set(EXPECTED_PROBE_ROWS))
        check("adversarial-review: no unexpected probe identities",
              not unexpected, repr(unexpected))

    # Text-presence assertions only; fold whitespace to allow line wrapping.
    for skill, heading, clause in EXPECTED_RULES:
        body = section_text(texts.get(skill, ""), heading)
        check(f"{skill} [{heading}]: '{clause[:48]}...'",
              body is not None and " ".join(clause.split()) in " ".join(body.split()),
              "section missing" if body is None else "clause missing")

    print(f"\n{len(passes)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
