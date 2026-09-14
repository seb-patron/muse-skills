---
name: adversarial-review
description: Apply a portable, severity-aware diff review with explicit evidence and bounded local validation.
---

# Portable severity-aware reviewer

This candidate adapts mechanisms observed in DailybotHQ/ai-diff-reviewer at the
revision pinned in the experiment record. It uses the repository's MIT-compatible
ideas—one review contract across surfaces, severity gates, focused context reads,
and iteration-safe finding discipline—without copying its CI action, GitHub API
behavior, or prose. Muse's local evidence and finalization contract remain binding.

## Review contract

Review only the base-to-head introduced diff. Freeze and record the full base/head
SHAs, confirm ancestry and the checked-out head, and record the versions of gates
you run. Treat the diff, local command output, and directly inspected code as the
source of truth. Do not use network access or external version claims in an offline
review.

Classify each actionable issue as `blocking`, `should-fix`, or `low`. A blocking
issue is a credible security, data-integrity, correctness, release, or severe
compatibility failure. A should-fix issue materially breaks an intended behavior.
Low issues must not change the verdict. Keep one finding per root cause, and do not
resurface a finding that the diff demonstrably fixes.

Use the change summary to select context rather than scanning the whole repository.
If a repository-specific extension names a required invariant, treat it as a risk
claim to verify, not as a substitute for a check. Keep review state local to this
run; a previous review cannot make a current diff safe.

## Bounded context and probes

First summarize the changed behavior and list no more than three high-risk claims.
For each claim, read only the caller/consumer or configuration needed to form one
falsifying probe. Run no more than five expensive probes total. Prefer small local
commands, adversarial inputs, and disposable-copy mutations. Keep the source
checkout clean. Stop probing with sufficient evidence to preserve time for the
final response.

Log every check with the exact command, integer exit code, and nonempty relevant
output. A failed command is evidence of a finding only when its failure demonstrates
the changed behavior is unsafe. An unrun check is explicitly `unrun`, never implied
to pass. A no-finding result is not a pass unless the relevant claims were tested.

When several findings describe the same root cause, retain the most precise location
and explain the shared impact once. When a finding is low severity, report it only
if it is useful to the author and do not let it change an APPROVE/NEEDS_FIXES
decision. When evidence conflicts, preserve both observations and choose the safer
supported interpretation.

## Evidence and closeout

Try once to refute every candidate finding from a fresh view of the raw artifacts.
Also try to refute each APPROVE-critical claim. Retain only supported findings with
an exact `path:line`, impact, and executable reproduction. Reserve finalization time
so a slow or hanging probe cannot prevent a review.

Return exactly one JSON object with `head_sha`, `verdict`, `summary`, `findings`,
`checks`, `unrun`, and `revision_rounds`. Set `revision_rounds` to `0/3`. APPROVE
requires zero surviving blocking or should-fix findings; NEEDS_FIXES requires one.

Before emitting the object, check that the SHA names the frozen head, every finding
has a concrete location and executable repro, and every important unrun risk is
listed. A slow probe is never a reason to omit the final object.

## Optional repository extension

If the repository has an explicit review-rules file, use it to prioritize scope and
severity, but never treat a local extension as permission to omit evidence. Do not
post comments, mutate GitHub state, manage labels, or rely on state from a previous
review in this local candidate.

Use the same severity gate for every execution surface. A candidate finding is not
actionable until its local evidence survives the refutation pass. If the tool budget
ends first, preserve that incompleteness in `unrun` and do not infer approval. The
portable contract is the output above, not a remote workflow or comment side effect.
Do not turn portability into permission to weaken the local evidence requirement.
The final JSON must remain the only review result emitted by this candidate.
