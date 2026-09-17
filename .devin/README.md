# Devin CLI permissions for this repository

`config.json` grants the Devin CLI (SWE-2) a standing permission set for headless runs in this
repository. Read it as a **permission grant**, not a sandbox, and change it only with the project
owner's review. It mirrors the shape and caveats of `gen-v-research-tools`' `.devin/config.json`
(deny > ask > allow, exact-rule execs); see that repo's `.devin/README.md` and issues #148/#156 for
the fuller writeup this one summarizes.

## What the rules mean

- `Exec(x)` is a **prefix match on complete word boundaries**. A path counts as one word, so
  `Exec(scripts/)` would **not** match `scripts/run_offline_checks.sh` — that is why the allow list
  below has an exact entry for every way the wrapper may be invoked (bare, `./`, `bash`, `sh`),
  instead of a `scripts/` prefix rule.
- Precedence is **deny > ask > allow**. A deny always wins.
- This is not a sandbox: an allow-listed script can still do anything its own body does. Devin's
  permission engine only sees the top-level `Exec` call (e.g. `bash scripts/run_offline_checks.sh`);
  it does not separately police the `python`/`node`/`git` commands that script runs internally. The
  offline-checks script is deliberately narrow (test/lint/validate only, `set -euo pipefail`, no
  network) so that allow-listing it as one unit is an acceptable risk.

## Allow list

`scripts/run_offline_checks.sh` (all invocation spellings) plus the read-only helpers
(`ls`, `cat`, `head`, `tail`, `wc`, `grep`) and read-only/staging `git` subcommands (`status`,
`diff`, `log`, `show`, `add`, `rev-parse`, `ls-files`, `worktree list`). `git commit` is
deliberately **not** allow-listed — like gen-v, it stays an `ask`, because a headless worker should
never commit on its own; the calling session reviews the diff and commits.

## Deny list

Network tools (`curl`, `wget`, `ssh`, `nc`), `git push` (bare and force/no-verify forms),
`muse`, `promptfoo`, `npm`/`npx`, `node evals/behavioral/run.mjs` (the real eval entry point —
`node --check` on it, run only from inside the allow-listed wrapper, is not a top-level `Exec` call
and is unaffected), `rm -rf`, hook-path tampering, and writes to `.env*`, `.git/**`, `.devin/**`
and `.github/workflows/**`.

**No eval or model command is permitted.** Nothing here allow-lists `npm run eval:*`, `promptfoo`,
or a real (non `--check`) run of `evals/behavioral/run.mjs`; those touch paid model providers and
are out of scope for a headless permission grant.

## Running Devin CLI headless (SWE-2)

Prerequisite: a `.venv` must exist in the worktree with `PyYAML==6.0.3` (it is git-ignored, so each
worktree needs its own):

```sh
uv venv .venv && uv pip install --python .venv/bin/python PyYAML==6.0.3
```

Launch through the owner's local wrapper (never call `devin` directly in `accept-edits`/`dangerous`
mode from an agent session):

```sh
swe2-run review|code <worktree> <prompt-file> <out-dir>
```

- `review` — no edits (`--permission-mode auto`).
- `code` — edits inside `<worktree>` (`--permission-mode accept-edits`).

Both fix `--model swe-2-high`. Check the transcript's served model after every run; never accept a
silent fallback to a different model or a Fusion/paid model.

## Native review requirement

Any coding run must spawn exactly one native SWE-2 review subagent via its `run_subagent` tool
before finishing, and report that subagent's result verbatim plus the model it served. gen-v's
`.devin/README.md` documents `run_subagent`'s `subagent_explore` profile as read-only and verified;
it does not document which model the subagent itself is served under, so treat that as unverified
until a transcript confirms it — do not assume it matches the parent's `swe-2-high`.

## What this config does not guarantee

- `Exec()` rules are whole-word prefix matches on the command as written. A deny such as
  `Exec(node evals/behavioral/run.mjs)` does not match `node ./evals/behavioral/run.mjs`; it is
  effective today only because no rule admits bare `node`, so any other spelling falls through to
  `ask`, which headless mode rejects. Re-check the denies before broadening any `allow` rule.
- "Read-only" git commands are not strictly read-only: `git diff`, `git log` and `git show` accept
  `--output=<file>`, and files written by an executed process are not covered by the `Write()`
  denies (including `.git/**`). Treat the allow-list as a guard against accidents, not a sandbox,
  and review every run's transcript and resulting tree.
- The reviewer subagent's served model is not recorded in the transcript (CLI 3000.10.27), so
  `swe2-run`'s served-model check covers the parent only.
