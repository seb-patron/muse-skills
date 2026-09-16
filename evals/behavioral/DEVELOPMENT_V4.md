# Development profile v4

Status: case construction only. No candidate execution and no grader call has run
under this profile. Every verdict and finding below is **proposed gold pending
Sebastian's human adjudication**; none of it is human gold.

Protocol: `muse-adversarial-review-development-v4`

Normalization: unchanged `muse-review-metrics-v2`

Data role: disclosed development only

Development v4 is an additive, versioned profile. It keeps the executed v2 and the
calibrated v3 manifests, case packs, Promptfoo configs, prompt, results, and metrics
immutable, and adds three cases from the Gen V
[PR #87](https://github.com/seb-patron/gen-v-research-tools/pull/87) exact-head repair
chain. The case pack begins with the byte-identical v3 pack, so the three v3 cases are
unchanged by construction and the offline validator fails if that prefix ever drifts.
The candidate identities and hashes, Muse/Spark runtime, Terra grader, budgets, row
timeout, assertions, and the three rubric texts are the same as v3.

## Stages

Stage B (`8850ee3`, `genv-pr87-first-repair-type-boundary`) already existed in v3 and is
retained unchanged. This profile adds A, C, and D.

| Case | Stage | Head | Body snapshot | Expected verdict |
|---|---|---|---|---|
| `genv-pr87-initial-implementation` | A — initial implementation | `122108e` | `2026-09-14T01:56:04Z` | `NEEDS_FIXES` |
| `genv-pr87-first-repair-type-boundary` (v3) | B — first repair | `8850ee3` | not delivered | `NEEDS_FIXES` |
| `genv-pr87-merge-ready-stale-body` | C — code fixed, body stale | `33d850d` | `2026-09-14T03:23:11Z` | `NEEDS_FIXES` |
| `genv-pr87-merge-ready-corrected-body` | D — same code, body corrected | `33d850d` | `2026-09-14T03:39:14Z` | `APPROVE` |

C and D pin the identical code head and tree and differ only in the delivered body
snapshot. That pair carries the invariant the chain exists to test: a code SHA does not
uniquely identify the merge artifact, because changing the live description can change
merge readiness without changing Git. The validator fails if the pair ever stops sharing
a head or stops differing by its body.

A, B, C, and D are one disclosed development family (`genv-pr87`). Their findings and
proposed verdicts are public in issue #5 and influenced candidate design, so no stage may
support a validation, held-out, or generalization claim.

## Proposed gold and independent reproduction

Each stage-A finding and the stage-C body drift were reproduced independently on the
matching frozen head using public committed material and synthetic inputs only. No
emulator ran, no commercial input was used, and no private artifact was read.

| Finding | Stage | Reproduced | Evidence |
|---|---|---|---|
| `path-free-allowlist-bypass` (blocking) | A | yes | A symlinked synthetic summary with path-bearing caller identity was accepted; the emitted fixture echoed the caller canaries. Exit 0. |
| `repeatability-not-independently-bound` (blocking) | A | yes | Truthy string repeatability receipts were accepted and re-emitted in the durable fixture. |
| `missing-framebuffer-pre-intervention-control` (blocking) | A | yes | A synthetic arm whose framebuffer differed from control at the first checkpoint still exited 0 with `validity.state=accepted` and `pre_intervention_equality=pass`. |
| `classifier-prose-mismatch` (should-fix) | A | yes | Equal contrast offsets `{0, 4}` plus an unmatched contrast at frame 3600 still classified as `timing-shift-supported`. |
| `stale-live-pr-body` (should-fix) | C | yes | `git diff --shortstat` reports 3585 insertions and 97 added reading-trail lines for this head, while the contemporaneous body still reports 2884 and 66 and still presents the superseded `8850ee3` approval as current. |
| stage D scoped clean status | D | yes, scoped | The four stage-A probes and the two stage-B blockers are all refused at this head, the focused suite discovers and passes 52 tests, and the body's statistics match the head. The full suite, privacy gate, and hosted checks were not rerun. |

All four stage-A probes refuse on `33d850d`, which is the evidence behind the closure
expectations in C's and D's grader-only `resolved_findings`.

One possible extra stage-A finding is recorded in case metadata as
`unadjudicated_observation` and deliberately **excluded from gold**: the stage-A body
reports 2884 insertions and a 66-line reading-trail entry where that head's diff reports
2888 and 68. It needs human adjudication before it can affect scoring.

## Body snapshots: digests here, text outside this repository

`gen-v-research-tools` is private and this repository is public, so **no snapshot text is
committed here**. [`fixtures/genv-pr87/SNAPSHOTS.yaml`](fixtures/genv-pr87/SNAPSHOTS.yaml)
holds only digests and provenance: the exact GraphQL query and command, the edit
timestamps, the raw export digest of each of the five edits (including the one
intermediate edit no stage uses), the stored and delivered digest of each of the four
frozen snapshots, the sanitization rules, and the chain ordering. The pull-request title
is treated the same way and is recorded only as a digest. Per-stage leakage markers are
**derived at run time** from the verified text and never stored, because a stored marker
would itself be verbatim private-repository text.

The sanitized text lives in a local directory outside the repository:

- environment variable: `GENV_PR87_SNAPSHOT_DIR`
- default on this machine: `~/.local/share/muse-skills/genv-pr87-snapshots`
- contents: `pr-title.txt` plus one `pr-body-stage-{a,b,c,d}-<timestamp>.md` per stage

[`fixtures/genv-pr87/snapshot_var.py`](fixtures/genv-pr87/snapshot_var.py) is the only
path by which that text reaches a candidate. It verifies each file against **both** its
stored and its delivered (trimmed) digest before returning it, and raises loudly — naming
the environment variable and the default directory — when the directory is missing or a
digest does not match. It never substitutes another stage or unverified text.

### Reconstructing the snapshots from the source pull request

With read access to `seb-patron/gen-v-research-tools`, re-export the bodies and rebuild
the directory: run the `extraction_command` recorded in `SNAPSHOTS.yaml` (a
`gh api graphql` call using the recorded `extraction_query`, which returns the
`userContentEdits` connection for PR #87), confirm it still reports five edits with
`hasNextPage: false`, and check each node's `diff` against the `raw_export_sha256` for
its `editedAt` timestamp. Then apply the two sanitization rules recorded in the manifest
— replace the commercial title token and the truncated ROM/save/adapter/emulator digests
with the recorded placeholders — normalize each file to end with exactly one newline, and
write the four stage files plus `pr-title.txt` into the snapshot directory using the file
names in the manifest. The rebuild is correct exactly when every file matches its
recorded `sha256` and `delivered_sha256`; the loader refuses anything else, so a
mis-sanitized rebuild cannot silently reach a candidate.

## Delivery mechanism

The harness had no way to deliver a PR-body snapshot to the candidate. v3 passes
`{{change_summary}}` through the canonical prompt; v4 adds
[`prompts/review-v4.txt`](prompts/review-v4.txt), which is the v3 prompt plus one
optional block that renders the PR title and body snapshot when a case supplies them.
Each new case sets `pr_body_stage` and points `pr_title_snapshot` and `pr_body_snapshot`
at the verifying loader, which Promptfoo calls as a `file://...py` variable loader. Two
offline tests bound the change: the retained v3 cases render the v4 prompt
byte-identically to the v3 prompt, and the deterministic Python renderer used by the
preflight is cross-checked against Nunjucks itself when the snapshots and the dev
dependency are present.

## Leakage preflight

The candidate sees only the frozen base-to-head checkout prepared by the existing
isolation code, that stage's verified title/body snapshot, and its ordinary tools. Per
new case, the offline checks fail if any of the following becomes visible:

- a later commit, by full SHA or abbreviation, in any candidate-visible input;
- a later or otherwise foreign body snapshot, detected by a derived per-stage marker;
- a later ref, remote, `FETCH_HEAD`, alternates file, or later commit object in the
  prepared checkout, or a foreign snapshot written into the working tree;
- the text of either public review comment, or any grader-only gold field.

A separate check scans every tracked and untracked repository file for verbatim snapshot
lines, so body text cannot creep back in through a doc example or a test fixture.

One recorded exemption: the authentic stage-D body links to the public stage-B review
comment URL. The link is part of the real merge artifact and is retained; the review's
own text stays grader-only, and candidate runs have no network access.

## Offline validation

```sh
SPIKE_PYTHON=/path/to/python-with-pyyaml npm run eval:development-v4:validate
```

The command runs the deterministic preflight in
[`development_v4.py`](development_v4.py) and then `promptfoo validate config`. It makes
no model call. The same checks run under
`python -m unittest discover -s evals -p 'test_*.py'`.

**Without the snapshot directory** the structural, identity, immutability, prompt,
config, and no-committed-text checks all still run and pass; the digest-verification and
per-case leakage checks are reported as `SKIPPED` with the environment variable named,
and the delivery-dependent tests skip with the same reason. **With it**, every snapshot is
digest-verified and the full per-case leakage preflight runs.

## Limits

- No live v4 run mode is wired. Adding one needs the result-custody, reservation, and
  row-cardinality logic in `run.mjs`, and any live run needs separate authorization for
  inference and private-source delivery.
- A live run also needs the snapshot directory present on the runner; the loader fails
  the row loudly rather than delivering an unverified or absent body.
- Gold is proposed, not adjudicated. The chain-level gate proposed in issue #5 should
  only be reported as a provisional comparison until a non-author adjudicates.
- Stage B is retained exactly as v3 froze it and therefore receives no body snapshot yet,
  although its snapshot is frozen and hashed for the chain's completeness.
- Hosted check state is recorded in the snapshot manifest as provenance but is not
  delivered to the candidate as a separate input; the body snapshots describe it.
- Do not combine or compare v3 and v4 aggregates. v4 adds three rows and a new
  candidate-visible input to the profile.
