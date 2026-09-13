---
name: adversarial-review
description: Run a bounded risk-first falsification pass over a green-gated diff and finalize an evidence-backed review.
---

# Risk-first adversarial review

Use this short pass after the conventional gate rerun and before the verdict.
The objective is to falsify the most consequential behavioral assumptions, not to
execute a catalogue mechanically. A green gate is evidence about its assertions,
not proof that the change works outside them.

## 1. Freeze the change

Record the full base SHA, full head SHA, and gate/tool versions. Confirm that the
base is an ancestor of the head and that `git rev-parse HEAD` equals the reviewed
head. Inspect only the introduced diff first. Read surrounding callers, consumers,
configuration, and docs only when they are needed to test a claim.

## 2. Choose risks

State at most three high-risk behavioral claims. Rank them by user harm, security,
data loss, compatibility, or likelihood that the existing gate misses them. A claim
must name the changed behavior and the invariant that would make it safe. Examples
include an input boundary, a fallback, an authorization check, a generated artifact,
or a command's exit status. Do not turn every touched file into a mandatory probe.

Use a small risk table while reasoning: `claim`, `failure impact`, `evidence to
inspect`, `falsifying probe`, and `status`. Keep the table in working notes unless
the output contract asks for it. A claim can be supported by direct diff evidence;
execution is required only when a runnable behavior is still uncertain.

## 3. Falsify economically

Choose one falsifying probe per claim. Use the smallest runnable command or
disposable-copy mutation that can distinguish the safe behavior from its failure.
Execute no more than five expensive probes total, including setup, mutation, and
revert work. Stop early when the claims are tested. Keep the reviewed checkout
unchanged; use a temporary copy for mutations. Reserve the final part of the
available budget for writing the review, even when a probe is slow or hangs.

For every probe, record the exact command, exit code, and relevant output. A skipped
probe is not a pass: put it in `unrun` with the reason. Do not make claims about
current external versions or compatibility without authoritative evidence; mark
that check unrun when offline.

Prefer probes that cross a boundary the change may have weakened: malformed input,
an absent dependency, a failing subprocess, a clean copied artifact, or a consumer
that uses the changed interface. Avoid spending a probe on formatting, broad
inventory, or a duplicate of the normal gate. Never apply a mutation to the reviewed
checkout, and do not count a command that only prints source text as behavioral
evidence.

## 4. Refute before reporting

For every candidate finding, make one fresh refutation attempt from the raw local
artifacts. Also challenge every claim needed for APPROVE, such as “the guard catches
the bypass” or “the trial is byte-identical.” Keep the refutation command and result.
Strike a finding only when the refutation directly explains why it is not actionable.

If the refutation is inconclusive, keep the finding or mark the uncertainty in the
summary; do not silently convert it into an approval. If all probes are unrun, say
so and make the verdict reflect the remaining risk rather than claiming coverage.

## 5. Finalize

Report only findings that survive refutation. Each finding must include severity,
an exact `path:line` location, concrete impact, and a reproduction containing a
command, integer exit code, and nonempty observed output. Include the full frozen
head SHA, a short summary, every executed check, every important unrun probe, and
`revision_rounds: "0/3"`. Use `NEEDS_FIXES` for at least one surviving blocking or
should-fix finding; use `APPROVE` only when none remains. Return exactly one JSON
object matching the review contract and no Markdown fence.

Specialized probe catalogues may be consulted when the changed area warrants one,
but no catalogue row is mandatory merely because its topic appears in a generic
checklist. The risk claims and recorded evidence determine coverage.

Keep the result reproducible: name the exact revisions and avoid machine-local paths.
Do not report a green gate as proof of the risk claims.
