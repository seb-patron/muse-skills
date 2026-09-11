# Baseline eval record

Deterministic offline baseline for the `adversarial-review` and
`fix-verification` skills. No network, no API keys, no model calls
(`tokenUsage.total: 0` in the machine results).

- Command: `npm run eval:baseline -- --output evals/results/baseline-2026-09-11.json --no-progress-bar`
  (`eval:baseline` is defined as `promptfoo eval`; the config is
  auto-discovered as `promptfooconfig.yaml` at the repo root.)
- Date: 2026-09-11 (eval ID `eval-U0w-2026-09-11T21:04:46`)
- Tooling: promptfoo 0.123.0 (`devDependencies`), node_modules installed via `npm install`
- Result: **6/6 test cases passed, 43/43 assertions passed, 0 errors**

## Config paths

- `promptfooconfig.yaml` — eval config (prompts, providers, test cases, assertions)
- `evals/prompts/skill_surface.txt` — prompt template (verbatim skill echo)
- `evals/providers/skill_provider.py` — offline provider returning `skills/<skill>/SKILL.md`
- `evals/providers/layout_provider.py` — offline provider listing files under `skills/`
- `evals/results/baseline-2026-09-11.json` — machine-readable results (committed,
  eval ID `eval-U0w-2026-09-11T21:04:46`)
- `evals/results/baseline-2026-09-11-c18a44d.json` — second green run of the
  same config seconds later (eval ID `eval-iXs-2026-09-11T21:04:49`, also 6/6)

## Coverage

- `adversarial-review`: frontmatter (`name`/`description`), trigger section,
  all 14 probe catalogue rows, refutation pass, verdict rules,
  `Revision rounds: 0/3`, plus a row-count javascript check.
- `fix-verification`: frontmatter, trigger section, traceability table,
  diff-to-row mapping, per-row repro re-runs on the final head, rebase/stack
  re-verification, status-field consistency, revision counter
  (`Revision rounds: N/3`), close-or-carry rule.
- Repo layout: exactly one `SKILL.md` per skill directory (no stray files).

## Sensitivity (negative control, 2026-09-11)

Copy of the tree in `/tmp` with one probe row deleted
(`Digest-mutation check`) and the revision counter altered
(`Revision rounds: N/3` → `X/Y`) yields **4 passed / 2 failed** —
exactly the two affected cases go red, so the baseline is not vacuous.
