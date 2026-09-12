# Structural skill validation

`check_skills.py` checks tracked skill documents. It uses PyYAML for safe YAML
1.1 parsing and Git for file discovery. Python 3.12 is the CI baseline.
From the repository root, install the pinned lint dependency and run both gates:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r evals/requirements.txt
.venv/bin/python evals/check_skills.py
.venv/bin/python -m unittest discover -s evals -p 'test_*.py' -v
```

On Windows, use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.
After setup, the checker can be invoked by its path from another working
directory; it derives the repository root from its own location.

The checker reports all failures it can assess and exits 1 if any check fails,
or 0 if all applicable checks pass. Missing files, unreadable UTF-8, invalid
frontmatter, and Git enumeration errors produce lint failures. A Git failure
ends the run because no reliable tracked-file set is available. The number of
checks depends on the tracked skills and available inputs; it is not fixed.

## What it checks

1. **Tracked skill roots and layout.** Every immediate `skills/<skill>/`
   directory represented in `git ls-files` must contain a tracked root
   `SKILL.md`. The two skills with pinned contracts remain required.
   Supporting files may live alongside that file or in nested directories;
   a nested example named `SKILL.md` is supporting content, not another skill
   root. Collection files such as `skills/README.md` are allowed. Every tracked
   file named `SKILL.md` must exist on disk.
2. **YAML metadata.** Each tracked root `SKILL.md` must begin with a `---`
   delimited YAML mapping. `name` must be a string equal to the directory name;
   `description` must be a nonblank string. Quoted values, YAML comments, and
   nonempty folded/literal descriptions are supported. Nulls, non-string
   descriptions, empty strings, malformed YAML, and unsafe tags fail. Description
   wording is not pinned. PyYAML scalar rules apply (for example, quote `yes`
   if it is intended as a string).
3. **Probe identities.** Inside `adversarial-review`'s `## Probe catalogue`,
   exactly 14 `- **...**` headers must identify each expected probe once.
   A header is the exact name, optionally followed by a provenance suffix
   such as ` (miss #75-1).` or ` (misses #75-1, #76-5).`; IDs may change.
   Extra text, unknown names, missing names, and duplicates fail. Row order
   is deliberately unrestricted. Mentions outside the catalogue or outside
   bold row headers cannot replace a row.
4. **Selected instruction fragments.** Eleven pinned fragments must appear
   inside their respective `##` sections. Whitespace is folded so ordinary
   line wrapping is allowed. Deleting a fragment or moving it to another
   section fails. Text extraction uses literal headings and row patterns.

All checks discover files from the Git index, then read their current
working-tree contents. Untracked drafts and `.DS_Store` files are ignored.
Stage a new skill with `git add skills/<skill>/` before expecting it to be
checked. Edits to an already tracked file are checked without staging them.

## Regression tests and CI

`test_check_skills.py` builds temporary skill trees with real Git indexes.
It covers valid and invalid YAML, missing/unreadable files, root versus nested
support files, new and untracked skills, duplicate/renamed/combined probe
headers, every individual probe-row deletion, every pinned-fragment deletion,
and the CLI's exit status when invoked from another directory. Fault injection
covers an unavailable Git executable and a file permission error.

The [skills-lint workflow](../.github/workflows/skills-lint.yml) installs the
same pinned dependency and runs both regression tests and the checker on every
PR and on pushes to `main`. It does not invoke Muse.

## What a passing result does not establish

This is static text validation. It does not run the catalogue's probes, validate
their instructions, or interpret Markdown and instruction meaning. Probe bodies
can change or disappear while the headers still pass. A matching fragment in
a comment, example, or negated sentence can satisfy a text-presence check.
These limitations require human review; the checker is not a semantic guard.

It also does not test Muse installation, skill discovery/selection, actual
agent compliance, finding quality, evidence handling, or stopping behavior.
Evaluating those requires representative Muse runs with known expected outcomes.
A passing lint is not evidence that concerns about Muse behavior are resolved.

Behavioral coverage lives separately under [`evals/behavioral`](behavioral/README.md).
That Promptfoo suite runs Muse against frozen review heads with and without the
current skill, then applies deterministic contract checks and an independent
model grader. It is intentionally not part of this structural lint command.
