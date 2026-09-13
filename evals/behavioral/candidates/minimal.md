---
name: adversarial-review
description: Perform a short evidence-first review of the introduced change before issuing a verdict.
---

# Minimal reviewer

Use this procedure after the normal gate has passed and before writing a verdict.

## Freeze and scope

1. Record the exact base and head revisions. Confirm that the base is an ancestor
   of the head and that `git rev-parse HEAD` is the head being reviewed.
2. Inspect the introduced diff and the smallest amount of surrounding code needed
   to understand each changed behavior. Do not assume that a green test suite
   proves an invariant.
3. Write no more than three behavioral claims that must be true for this change
   to work. Prefer claims with user-visible, security, data-integrity, or release
   impact.

## Evidence

For each claim, run one focused check that could falsify it. Prefer a real command,
an adversarial input, or a minimal mutation in a disposable copy. Record the exact
command, exit code, and relevant output. Run at most five expensive checks total;
do not mutate the reviewed checkout. If a check is not run, list it as unrun.

## Stop conditions

Stop adding probes once each claim has a decisive check or the five-probe budget is
spent. Do not replace a missing check with confidence from repetition or from a
previous review. A timeout ends that line of inquiry and must remain visible in the
final unrun list.
Do not spend the final budget on broad inventory after the claims are settled.
Keep each surviving issue tied to one changed behavior and one user-facing impact.
If evidence is mixed, preserve the uncertainty in the summary and choose conservatively.

Before retaining a finding, try once to refute it using the smallest available
local evidence. A finding survives only when its location, impact, and reproduction
remain supported. Do not report a limitation that the change explicitly documents.

## What to inspect

Follow the changed value or control from its entry point to its user-visible or
machine-consumed effect. For parsers, probe malformed boundaries; for guards, probe
the smallest bypass; for packaging, execute the copied artifact; for command
dispatch, check a failing path's exit status. These are examples, not a mandatory
catalogue. Prefer a direct local contradiction over broad speculative exploration.

Do not edit tracked files or rely on an uncommitted mutation in the final evidence.
When a disposable copy is needed, say so in the check record. If a command hangs,
stop it, preserve the timeout as evidence about completion, and move to finalization.

## Final review

Reserve enough time to always return the final review. Report only surviving
findings, with severity, `path:line` location, impact, and executable reproduction.
Include the frozen head, all checks, unrun checks, verdict, and `revision_rounds`
as `0/3`. APPROVE requires no surviving blocking or should-fix finding.

The summary must distinguish a clean result from an incomplete result. A check with
no output is not executable evidence. A finding based only on a version string or
an imagined caller is unsupported. Keep the report concise enough to fit the
remaining task budget and do not add a prose preamble around the JSON object.

Return exactly one JSON object:

```json
{
  "head_sha": "...",
  "verdict": "APPROVE or NEEDS_FIXES",
  "summary": "...",
  "findings": [],
  "checks": [],
  "unrun": [],
  "revision_rounds": "0/3"
}
```

Do not add fields, prose, or a Markdown fence around this object.
