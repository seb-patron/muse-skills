# muse-skills

Review-loop skills for Muse sessions, built from a real incident: two round-1
reviewers approved with 0 blocking findings while an independent review found
13 real issues. These skills encode the protocol that caught everything in
rounds 2–3.

| Skill | What it does |
|---|---|
| [skills/adversarial-review](skills/adversarial-review/SKILL.md) | Pre-verdict adversarial probe pass over a green-gated diff: a runnable catalogue of probes (imports, worktrees, guard evasions, vendoring, builds, help text, docs-as-written) plus a refutation pass before APPROVE. |
| [skills/fix-verification](skills/fix-verification/SKILL.md) | Post-fix closeout: one traceability row per finding, diff-to-row mapping, per-row repro re-runs on the final head (incl. after rebases), status-field consistency, and a `Revision rounds: N/3` counter. |

Background (evidence, not required reading): the miss analysis, skills review,
and cross-review that produced these live in the originating work log, and each
skill names the concrete misses its steps catch.

## Use in a Muse session

Skills install from a local path. Clone once, install each skill (user scope
shown; omit `--scope user` for project scope):

```sh
git clone https://github.com/seb-patron/muse-skills.git
muse skills install muse-skills/skills/adversarial-review --scope user
muse skills install muse-skills/skills/fix-verification --scope user
```

Then invoke by name (`adversarial-review`, `fix-verification`) or let the
session load them when a review/fix round starts.

## Structural validation

The lint validates tracked skill roots, YAML metadata, the 14 probe identities,
and selected instruction fragments. It uses a pinned PyYAML dependency; CI runs
the lint and its regression tests on every PR. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r evals/requirements.txt
.venv/bin/python evals/check_skills.py
.venv/bin/python -m unittest discover -s evals -p 'test_*.py' -v
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.
This checks document structure and text presence; it does not execute Muse or
establish that Muse loads or follows the skills correctly. See
[evals/README.md](evals/README.md) for the contract and its limitations.

## Contributing

`main` is protected: all changes land via pull request with one approval
(stale reviews dismissed on push). Keep each skill's root document at
`skills/<skill-id>/SKILL.md`; nested supporting files are allowed. Stage new
skill files so the structural lint discovers them, run the gates above, and
validate the skill with Muse before opening a PR:

```sh
muse skills validate skills/<skill-id> --json
```

Both skills shipped `valid: true` with zero diagnostics.
