---
name: review-loop
description: Supervise a multi-step build or fix through plan refutation, bounded implementer-reviewer-fixer rounds, and human handoff when the cap or a stop condition is hit.
---

# Review Loop

Supervise one work unit from plan to APPROVE or human handoff. This skill is the
supervisor layer: it assigns roles, counts rounds, and enforces stop conditions.
It does not replace the round-level skills — run plan refutation through
`adversarial-review` thinking (attack the plan, not the diff) and every review
round through `adversarial-review` plus `fix-verification` for the fix batch.

## 1. Trigger: attach the loop early

Start the loop when the user asks for a plan spanning implementation, or any
multi-step build or fix. Attach the loop then — before code exists — not after
code is written. A unit already in progress with code but no loop joins at
Round 0 only if no implementation has been reviewed yet; otherwise it joins at
round 1 with its current head recorded as unreviewed.

## 2. Round 0: refute the plan (never skip)

Before any implementation, spawn one agent to refute the PLAN, not the code.
The refuter attacks: scope holes (work the plan silently excludes), missing or
untestable acceptance criteria, wrong base or branching (wrong parent, stacked
order, drift from remote), and consumer paths the plan never exercises (docs,
vendoring, install, packaging). The refuter returns concrete plan findings with
location in the plan, impact, and suggested repair. The plan is fixed until no
unrefuted blocking plan finding remains. Round 0 is cheap and mandatory — the
round-1 misses this loop exists to prevent came from plans nobody attacked.
Round 0 consumes no revision rounds.

## 3. Roles: separate agents, one writer per clone

Every round uses three separate agents: implementer (or fixer) → reviewer →
fixer. Rules:

- The reviewer never wrote and never previously reviewed this unit.
- The fixer never reviews their own fix; the next review goes to a fresh
  reviewer or a reviewer from an earlier round who did not write the fix.
- One writer per branch/clone: a clone has exactly one active writer. Never
  run two writers in one clone (the shared-clone double-fixer incident is why).
  A standby fixer stays parked with no clone until the active head fails review.

Record per worker a ledger row: unit, role, round, base/head SHAs, status —
plus command id, agent path, clone path, spawn time, expected duration, and end
time/notes. Keep a heartbeat log per worker (start line, progress lines, verdict
line); see §8.

## 4. Counter: `Revision rounds: N/5`, hard cap

State `Revision rounds: N/5` at every boundary. The initial review is round 1
when it returns findings (an APPROVE at round 1 with zero findings ends the
loop at `1/5`); each fix-and-recheck cycle increments N. APPROVE ends the loop
early at any round. After 5/5 with blockers still open, stop: write the
open-findings file (every unclosed finding with history and custody intact) and
hand off to a human. There is never a 6th round and never a silent drop.

## 5. Adaptive budget inside the cap

State which mode each round uses:

- **Full round** (blocking findings open): fixer produces a fix batch, then a
  full re-review round runs (`adversarial-review` + `fix-verification`).
- **Verify-fixes** (lows-only, no blockers): fixer addresses the lows, then the
  reviewer only verifies the fixes against the traceability table — no full
  re-review, no new probe catalogue.

Do not spend a full round on lows; do not close blockers with verify-fixes.

## 6. Later-round narrowing

Rounds 2+ admit new blocking findings ONLY for fix-introduced problems
(regressions, lost fixes after a rebase, newly broken consumer paths) or severe
misses (a round-1-class defect the earlier review should have caught, with the
miss named). Everything else found in rounds 2+ is recorded as follow-ups in
the PR body, not as blockers. Never relitigate a refuted or closed finding.

## 7. Artifact conventions

- **Finding format:** severity (blocking / should-fix / low) + file:line +
  failure scenario (exact command with exit code and output) + suggested fix.
  Never cite a suite result ("suite green", "trial PASS", "byte-identical") as
  evidence for or against a finding — both phrases previously coexisted with
  live blockers.
- **Traceability table:** one row per finding: finding id → fix commit + hunks
  → verifier (re-run command, exit code, output on the final head) → verdict
  (closed or carried). A row with no cited hunks or no final-head re-run stays
  open.
- **PR body rounds section:** per round, the verdict, the `Revision rounds`
  value, closed vs. carried findings, and follow-ups. Spot-check two body
  numbers per round; a full body audit is the author's pre-publish job.
- **Heartbeat log per worker:** start line, progress lines during work, verdict
  line at end. Expected cadence: a line at start, roughly every 20 minutes
  during work, and a status line by 40–60 minutes against the expected duration.
- **Ledger row per worker:** unit, role, round, base/head SHAs, status (plus
  command id, agent path, clone path, spawn/end times, notes).

## 8. Push and merge discipline

- Push only APPROVEd heads, one push per head. A re-review after a push needs a
  new head and a new round.
- PRs stay draft until the owner merges; reviewer APPROVE never authorizes
  merging, tagging, releasing, or closing issues.
- Stacked branches need explicit rebase + force-push authorization per push.
  After any rebase or base move, re-verify from scratch on the new head and
  confirm each pre-rebase fix was ported to the post-rebase path; an unported
  fix is a silently dropped fix and reopens its row.

## 9. Dead-agent handling

Sweep heartbeat freshness against expected duration: nudge a quiet agent once,
cancel and respawn if it stays silent. Respawn always in a FRESH clone — never
reuse the dead agent's clone. The dead clone's uncommitted diff may be read as
read-only context only, never committed from or pushed. Confirm DEAD only after
10 minutes of silence past the nudge with no heartbeat and no progress output,
then record the verdict, reassign the unit, and note the replacement in the
ledger. A slow-but-alive agent (heartbeats stale but commits or output exist)
is NOT dead — check for commits before declaring.

## 10. Stop conditions: hand to a human

Stop the loop and hand off with the open-findings file when any of these hold:

1. Cap exhausted: 5/5 with blockers still open.
2. A normative or product decision the brief does not settle (copyable vs.
   checkout-only, deprecation, scope cut, acceptance waiver).
3. Any request to merge, tag, release, or close issues — owner-only actions the
  loop never performs.

The handoff carries: the `Revision rounds` value, the traceability table with
per-row verdicts, carried rows with owners, and the ledger plus heartbeat
locations. Never a 6th round, never a silent drop.
