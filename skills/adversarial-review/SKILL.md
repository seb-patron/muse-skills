---
name: adversarial-review
description: Run a pre-verdict adversarial probe pass over a green-gated diff to catch the breakage that passing suites, read-only checks, and model-shaped repros miss.
---

# Adversarial Review

Attack the diff as shipped before writing a verdict. A green suite is not evidence; only commands you ran, with exit codes and outputs, are evidence.

## Trigger

Run when the reviewer holds a green gate and is about to write APPROVE or NEEDS_FIXES. It runs after the conventional gate rerun and before the verdict. Freeze first: record head SHA, base SHA, and gate versions, and confirm `git rev-parse HEAD` equals the reviewed SHA.

## Probe catalogue

Route the diff to rows by area, then run every routed row. Routing: docs/setup/install/help touched → Verbatim-doc + Prog-name-un-normalized rows; guard/subprocess/process-spawn code touched → Alias-form + Moved-code rows; packaging/build/embed/provenance touched → Dirty/sdist + Workspace-copy + Lone-file vendor rows; new tests added → Revert-probe row; file reads with encodings/paths/permissions → Non-UTF8 and FIFO row; env/config handling touched → Env-strip matrix row; dispatcher/CLI touched → Exit-status-discarding mutants row; digest/manifest touched → Digest-mutation row; probe/entry-script touched → Isolated-import row; worktree/history/refs touched → Linked-worktree row. Areas with no mapping default to running the row, never to skipping it. Each row lists what to run and what failure looks like. Ground each result in the named miss. Rows not run are listed as unrun, never assumed.

- **Isolated-import probe (miss #75-1).** Run: execute each new or moved probe/entry script the way its caller does, under `python3 -I` (e.g. `python3 -I -c "$PROBE"`). Failure looks like: NameError, ImportError, or any crash in the first lines — especially names used but never imported.
- **Revert-probe new tests (misses #75-1, #76-5).** Run: for each added test, revert the fix (or apply the obvious mutation) and show the test going red; one line per test. Failure looks like: a test that stays green with the fix reverted — substring checks, assertions that cannot fail, dispatcher mutants that change nothing.
- **Linked-worktree / common-dir graft (miss #75-2).** Run: `git worktree add` a linked worktree, place graft data where Git actually reads it (the common dir, not `--git-dir`), and check the detector fires. Failure looks like: silence or a wrong notice when the graft lives in the common dir.
- **Non-UTF8 and FIFO inputs (miss #75-2).** Run: feed a non-UTF8 byte in a comment line through the file reader, place a FIFO at the read path, and force an enumeration failure. Failure looks like: UnicodeDecodeError crash, a hang on the FIFO, or a silent pass on enumeration failure.
- **Env-strip removal matrix (miss #75-4).** Run: delete each stripped variable (or config defense) one at a time and run the behavioral check, not just the dict-contents test; also remove each entry of any trial-copy/artifact list. Failure looks like: the suite stays green (or only a contents test fails) while behavior changes — forged reads return, lookups die.
- **Alias-form guard evasions (miss #75-3).** Run: copy the scanned tree to a scratch directory (never the live head) and insert each canonical bypass the guard claims to forbid — aliased import (`import subprocess as sp`, `from subprocess import run as _run`), `os.execvp`/`posix_spawnp`, `asyncio.create_subprocess_exec`, a comment containing the guard token inside the call, an env-override (`{**safe_env(), "KEY": "0"}`), a raw call inside an exempt function, a raw call in a directory the guard does not scan (discover the scan scope from the guard's path list first; the Moved-code row covers new paths). Then invoke the guard exactly as its suite does (same test command or entrypoint). Failure looks like: the guard exits 0 / reports pass on any inserted bypass — "fires" means a nonzero exit or an explicit violation naming the inserted site.
- **Moved-code coverage (misses #76-1, #76-8).** Run: list files the diff moved or added and check every scanner, digest, copy-list, hook, and doc reference still covers the new path; insert a raw forbidden call at the new path. Failure looks like: guard, digest, or hook blind to the new location.
- **Lone-file vendor (misses #75-5, #76-6).** Run: copy the documented file set alone to an empty directory, per the reuse docs, and run every subcommand including `--help`. Failure looks like: ModuleNotFoundError or any crash the checkout hides via tree-preferring shims.
- **Workspace-copy execution (miss #76-2).** Run: build the real workdir/workspace artifact and execute the copied tools inside it (not `python tools/*.py` in the checkout). Failure looks like: ModuleNotFoundError or nonzero exit where the checkout run exits 0.
- **Dirty / sdist / foreign-HEAD builds (miss #76-3).** Run: build from a dirty tree, from an sdist or `git archive` tree, and with the target pointed at a different repo; assert the embedded commit equals `git rev-parse HEAD` of the built source. Failure looks like: a wrong, stale, `unknown`, or null commit reported as embedded, or a hex-format check passing on the wrong repo's commit.
- **Prog-name-un-normalized diff (miss #76-4).** Run: diff help/usage output against real dispatch names with no prog-name or whitespace normalization. Failure looks like: usage strings naming commands that do not exist, or overview text on wrong streams/exit codes.
- **Exit-status-discarding mutants (miss #76-5).** Run: apply dispatcher mutants — drop a branch, swap two areas, return 0 on no args, discard a failing subcommand's exit status — and run the suite plus a failing case end to end. Failure looks like: suite green while a failing case exits 0.
- **Verbatim-doc run (miss #76-7).** Run: paste setup/install/help commands exactly as documented into a clean environment (correct interpreter, fresh venv, literal placeholders resolved only as a user would). Failure looks like: `command not found`, backend-unavailable errors, POSIX-only commands on a listed-supported platform, or any workaround the trial needed but did not report.
- **Digest-mutation check (miss #76-8).** Run: edit each path family the digest claims to cover and confirm the recorded digest changes. Failure looks like: an edit with no digest change — an uncovered path.

## Refutation pass

Run the refutation in a fresh context of the same agent, given only the raw artifacts plus findings and no verdict. Each finding gets one refute attempt with a logged command. Unrefuted findings ship; refuted ones are struck with the refutation evidence kept. Symmetrically, refute every APPROVE-critical claim ("guard passes unmodified", "trial PASS", "byte-identical"): determinism is not correctness, a hand-run is not coverage, and a doc sentence is not behavior. Report template: frozen head/base SHAs; per catalogue row: run or unrun + command + exit code; findings with location, impact, repro evidence, and suggested resolution; the `Revision rounds` value.

## Verdict rules

Findings carry location, impact, repro evidence, and suggested resolution. APPROVE requires zero unrefuted blockers and states `Revision rounds: 0/3`. Any artifact edit after a passing verification invalidates that result for the changed version. List unrun catalogue rows as unrun; never treat a green suite, an implementer claim, or a normalized comparison as a run probe.
