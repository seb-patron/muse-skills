# Evaluation correctness correction v2

Normalization identity: `muse-review-metrics-v2`.

This correction changes future normalized result semantics without altering the
frozen spike candidates, cases, finalist record, raw result identities, or committed
v1 reports.

## Recall denominators

Recall is reported only when the case contract contains applicable gold. A clean
case whose `gold_findings` value is exactly `NONE` has no recall denominator; its
recall is `null`, while verdict accuracy, false approval, precision, and completion
remain separate observations. A case with pending or missing gold also has unknown
recall. Blank or malformed gold without a recognized `(blocking)`, `(should-fix)`,
or `(low)` severity contract is also unknown. Blocking recall is applicable only
when the case contract contains a finding marked `(blocking)`.

Direct Promptfoo rubric scores do not override applicability. Provider metadata is
optional and cannot override a case contract that is pending, blank, malformed, or
missing. Its absence is not evidence of an empty gold set. Normalized aggregates
record both applicable-row and scored-row counts so every reported mean has visible
coverage.

Future runs write `*.metrics-v2.json` and embed the normalization identity. The raw
outputs needed to recompute the committed train and validation reports are not part
of this branch, so those reports remain historical v1 records. Their recall values
must not be compared with v2 output or used as corrected evidence.

Normalized rows also retain the native case role/family, source repository,
base/head identities, candidate content hash, and candidate-usage coverage supplied
by the provider. Missing candidate telemetry remains `unavailable` with a null token
value; it is never converted to zero. These additive receipt fields do not rewrite
the frozen v1 reports.

## Historical Git visibility

Historical review workspaces are initialized as new repositories and fetch only the
explicit head and its ancestor history. They keep base-to-head ancestry and diff
commands usable while omitting source remotes, tags, later refs, later objects, and
alternates that could recover the source checkout. A regression test proves a later
sentinel commit cannot be resolved from the fixture.

This is a Git-history visibility boundary. It does not by itself prove isolation
from every file, process, credential, or tool on the host; those remain separate
runtime and sandbox responsibilities.
