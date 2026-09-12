# Skill evals

`check_skills.py` is a dependency-free structural lint for the skill files
(standard library only). It runs in CI on every PR (`.github/workflows/
skills-lint.yml`) and exits nonzero on the first failing state — exit 0
means all 39 checks passed:

```sh
python3 evals/check_skills.py; echo "exit=$?"
```

## What it pins

1. **Frontmatter** — the leading `---` block parses, `name` equals the skill
   directory name, `description` is present and non-empty. Wording is
   deliberately not pinned, so tuning a description never breaks the lint.
2. **Probe catalogue** — the `## Probe catalogue` section of
   `adversarial-review` holds exactly 14 `- **...**` rows with the expected
   names. Rows are counted inside their own section, so the routing paragraph
   that names several probes cannot shadow a deleted row.
3. **Load-bearing clauses** — each pinned rule sentence must appear inside
   its own section (e.g. `APPROVE requires zero unrefuted blockers` under
   `## Verdict rules`, `After 3/3 with blockers remaining, stop and request
   a human decision` under `## 6. Update the revision counter`). Headings
   alone do not pass.
4. **Layout** — from `git ls-files skills/`: every tracked skill directory
   holds a `SKILL.md`, and every listed `SKILL.md` exists on disk. Supporting
   files are allowed, untracked files are ignored, and no total file count is
   pinned, so adding a skill never breaks the lint.

## Mutation testing

Every pinned assertion was verified by deleting it one at a time in scratch
copies and confirming the lint exits nonzero (34/34 expectations met,
including reworded-description still passing and a third skill directory
still passing). The per-assertion table lives in the PR description.
