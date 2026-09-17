# Review verdict delivery

Contract between a review caller and each reviewer child: the caller ends with
an identifiable, complete review or an explicit delivery failure — never an
apparent approval whose findings were lost.

## 1. Vocabulary

Verdicts are exactly `APPROVE` or `NEEDS_FIXES`. A consumer that uses other
words must map them explicitly before accepting:

| Consumer word | Canonical verdict |
| --- | --- |
| accept, ship, lgtm | `APPROVE` |
| reject, request-changes, fix | `NEEDS_FIXES` |

Delivery status is separate from verdict: `DELIVERED` vs `DELIVERY_FAILED`
with reason `missing`, `truncated`, `stale`, `wrong-run`, `wrong-head`, or
`incomplete`. A delivery failure is never `APPROVE`. A surviving first line
does not prove delivery.

## 2. Transport shape

- The chat/transport message carries a one-line summary: verdict, blocking
  count, and the artifact locator — nothing more is required there.
- Full findings and evidence go in a bounded artifact at a caller-specified
  location (e.g. `<artifact-dir>/<run-id>.json`).
- Never require committing the artifact into application source or copying it
  into a public review thread.

## 3. Bound artifact

The artifact carries: task/case id, reviewed base SHA, reviewed head SHA,
caller-assigned run id, runtime child run/session id, completion status,
verdict, findings (each with severity, location, evidence), and a content
hash of the findings.

The caller validates identity and completeness before accepting: every field
present, run ids matching the expected run, head SHA matching the reviewed
head, completion `completed`, and hash recomputing cleanly. Any missing or
mismatched field is `DELIVERY_FAILED`, never the artifact's verdict.

## 4. Recovery

If the transported result is missing or truncated, recover only the expected
run's completed final result from the runtime's own record — the
terminal/completed record for that run id (e.g. under `<session-store>/`) —
and re-check the reviewed head against it. An earlier message from that run
is not the final result. If that run has no completed final record, the
outcome is `DELIVERY_FAILED: incomplete`; whether to rerun is the caller's
decision. Reviewer count is a workflow choice, not a delivery repair.

## 5. Decision table

| Input facts | Delivery status | What the caller does |
| --- | --- | --- |
| Summary + artifact agree on run, head; hash ok | `DELIVERED` | Accept the verdict |
| Transport cut, completed record for expected run matches head | `DELIVERED` | Accept the recovered result |
| No summary and no record for the expected run | `DELIVERY_FAILED: missing` | Do not judge; rerun or drop per workflow |
| Record carries a different run id | `DELIVERY_FAILED: wrong-run` | Discard; keep waiting or rerun expected run |
| Artifact head differs from reviewed head | `DELIVERY_FAILED: wrong-head` | Discard; re-review the current head |
| Expected run has no completed final record | `DELIVERY_FAILED: incomplete` | Do not judge; caller decides on rerun |
| Artifact matches an earlier reviewed head | `DELIVERY_FAILED: stale` | Discard; re-review the current head |

## 6. Artifact examples

Valid:

```json
{"task_id": "case-014", "base_sha": "aaa111", "head_sha": "bbb222",
"caller_run_id": "<run-id>", "child_session_id": "sess-9",
"completion": "completed", "verdict": "NEEDS_FIXES",
"findings": [{"severity": "blocking", "location": "app.py:12",
"evidence": "unclosed resource on error path"}],
"content_hash": "9f2c"}
```

Invalid — unmapped verdict, incomplete run:

```json
{"task_id": "case-014", "base_sha": "aaa111", "head_sha": "bbb222",
"caller_run_id": "<run-id>", "child_session_id": "sess-9",
"completion": "partial", "verdict": "maybe",
"findings": [], "content_hash": "9f2c"}
```

Invalid — head mismatch (stale or wrong-head):

```json
{"task_id": "case-014", "base_sha": "aaa111", "head_sha": "aaa000",
"caller_run_id": "<run-id>", "child_session_id": "sess-9",
"completion": "completed", "verdict": "APPROVE",
"findings": [], "content_hash": "0000"}
```

These examples illustrate the runbook only; the eval provider's
completion/trace/accounting code is owned separately under task #25.
