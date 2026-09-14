# Review-spawn checklist (Muse sessions)

Operator checklist for spawning independent-reviewer children whose verdicts must
survive child-result transport. Motivating incident: muse-skills #10 (two gen-v
PR #94 reviewer verdicts truncated in transit, recovered via session-log
archaeology). This checklist is interim process until the
`review-verdict-delivery` skill (#10) lands; delete or merge it then.

## Spawn prompt must require

1. **Verdict on line one.** `Verdict: MERGEABLE/NEEDS-REVISION, N blockers`
   first, details after. Transport truncation cuts tails, never decisions.
2. **Compact output contract.** Verdict, `blockers[]` with `file:line` plus a
   one-line fix each, then nits. No pasted file evidence — cite paths.
   Reviewer prose should stay under ~2k chars; anything structural goes in the
   verdict file (item 4).
3. **Terse locators.** Cite `path:line`; never quote file contents at length.
4. **File-backed verdict.** Reviewer writes findings to a bounded worktree file
   (path named in the spawn prompt); chat carries the verdict line. Reviewer
   stays read-only toward history — the author commits the file. Pairs with
   gen-v #96.
5. **Narrow fan-out past the budget.** More than ~3 review dimensions means more
   reviewer children with fewer dimensions each, not one mega-review. Parallel
   children also isolate truncation blast radius to one dimension.

## Delete-when conditions

- Delete items 1–4 when child results deliver full text for 3 consecutive
  reviews without archaeology (record the dates here when retiring).
- Item 5 (narrow fan-out) stays regardless: it is good review hygiene even
  with perfect transport.
