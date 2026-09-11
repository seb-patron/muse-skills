---
name: fix-verification
description: Verify a fix batch against every prior review finding with per-row repro evidence on the final head, and close or carry each row explicitly.
---

# Fix Verification

Close out a fix batch against the findings it claims to fix. A fix is verified only when each finding has its own row, its own hunks, and its own re-run evidence on the final head.

## Trigger

Run when a fixer pushes a fix batch addressing review findings, including after rebases of stacked branches. Do not run on a pre-fix head, and do not reuse evidence from a pre-rebase head after the base moves.

## 1. Build the traceability table

Build one table row per prior finding, plus one row per nit explicitly accepted as-is. Each row carries: finding id, severity, the exact failing repro command, the claimed fix (commit + hunks), the re-run result on the final head, and the verdict (closed or carried).

Record the head under test first: `git rev-parse HEAD` output pasted in the report. Evidence from any other SHA does not close a row.

## 2. Diff-to-row mapping

Cite the exact hunks (file + line range + commit) that close each row. A row with no cited hunks stays open, even if the surrounding code looks fixed — an unmapped claim is not a fix.

## 3. Re-run each repro on the final head

For every row, run the original failing command first, then the new regression test, both on the final head. Paste the exact command, exit code, and relevant output for each. If a prior finding carries no failing repro command, write one from the finding text and run it before the row can close — a row without a repro never closes as "verified". A green test suite never substitutes for a per-row repro. Example (FEL instance): a full suite passed while the preflight probe still crashed with `NameError: name 'sys' is not defined` because the only covering test was a substring check and the live path was mocked, so suite-green is not row evidence.

Mark implementer- or trial-supplied results as untrusted until you personally re-run them. "TRIAL.md: PASS" was once corroborated non-literally (own venvs, remapped placeholders) while the documented commands failed verbatim (`fel: command not found`); re-run means the documented command as written.

## 4. Rebase and stack re-verification

If the branch was rebased or its base moved, redo steps 2–3 on the new head from scratch. Check explicitly that fixes to a pre-rebase path were ported to the post-rebase path. Example (FEL instance): fixes to `tools/fel.py` had to be re-applied in `src/fel_ledger/fel.py` after the rebase, and an unported fix is a silently dropped fix. List each ported fix with its new hunks, or carry the row as lost.

## 5. Status-field consistency

Re-verify every artifact-emitted status value against reality in full — embedded/provenance flags, content digests, help/usage strings executed verbatim with prog names intact; a status field that contradicts the artifact is its own finding. Example (FEL instance): no `embedded: True` alongside a null or `unknown` commit (a dirty-tree build reported HEAD correctly-bytes but wrong-by-content, and sdist builds wrote `fel_commit: null` while claiming embedded); normalizing prog names hid `usage: fel lookup` for a command typed `fel handoff lookup`. PR-body numbers and scope ranges are spot-checked only (two entries); a full body audit belongs to the author pre-publish checklist, not to fix closeout. Example (FEL instance): wheel byte counts 53,133 vs 53,125 were both wrong, and a scope range of `tools/fel.py:1-30` cited a 27-line file.

## 6. Update the revision counter

Increment `Revision rounds: N/3` before each recheck batch. The initial review is not a round; at most three fix-and-recheck rounds follow. After 3/3 with blockers remaining, stop and request a human decision — there is no fourth fix batch.

Any edit after a passing verification invalidates that verification for the changed rows; re-run them.

## 7. Close or carry

Close a row only with evidence links: the cited hunks plus the pasted re-run output on the final head. Carry every other row with its finding, history, and custody intact — never silently park a row, and never close one on "suite green", "byte-identical rebuild", or "trial PASS" language alone (both phrases previously coexisted with live blockers).

Return the traceability table with per-row verdicts, the final head SHA, the `Revision rounds: N/3` value, and the list of carried rows with owners.
