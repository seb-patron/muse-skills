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

**These checks are the only route to `DELIVERED`.** Normal delivery and
recovered delivery (section 4) both pass all of them, against the caller's
expected task/case, base and head SHA, caller run id, runtime child identity,
completion status, and complete findings with a recomputed hash. The
abbreviated rows in section 5 name the distinguishing fact only; they are not
shortcuts past this list. `DELIVERED` means the review arrived intact — it
never turns `NEEDS_FIXES` into an approval.

`content_hash` is SHA-256, hex, over the artifact's `findings` array
serialized as compact JSON with sorted keys and no spaces — in Python,
`hashlib.sha256(json.dumps(findings, sort_keys=True, separators=(",", ":")).encode()).hexdigest()`.
Both sides must use the same convention; if a caller already has one, record
it instead.

## 4. Recovery

If the transported result is missing or truncated, recover only the expected
run's completed final result from the runtime's own record — the
terminal/completed record for that run id (e.g. under `<session-store>/`) —
and re-check the reviewed head against it. An earlier message from that run
is not the final result. If that run has no completed final record, the
outcome is `DELIVERY_FAILED: incomplete`; whether to rerun is the caller's
decision. A recovered result is accepted only after the section 3 checks pass
on it exactly as they would for a transported one. Reviewer count is a
workflow choice, not a delivery repair.

## 5. Decision table

| Input facts | Delivery status | What the caller does |
| --- | --- | --- |
| All section 3 checks pass on the transported artifact | `DELIVERED` | Accept the verdict |
| Transport cut; the expected run's completed record is recovered and passes every section 3 check | `DELIVERED` | Accept the recovered result |
| No summary and no record for the expected run | `DELIVERY_FAILED: missing` | Do not judge; rerun or drop per workflow |
| Record carries a different run id | `DELIVERY_FAILED: wrong-run` | Discard; keep waiting or rerun expected run |
| Artifact head differs from reviewed head | `DELIVERY_FAILED: wrong-head` | Discard; re-review the current head |
| Expected run has no completed final record | `DELIVERY_FAILED: incomplete` | Do not judge; caller decides on rerun |
| Artifact matches a head this caller reviewed earlier | `DELIVERY_FAILED: stale` | Discard; re-review the current head |

`wrong-head` and `stale` are both head mismatches: use `stale` when the head
is one the caller itself reviewed in an earlier round, and `wrong-head`
otherwise. The caller's action is the same.

## 6. Artifact examples

These are schematic: SHAs, ids and `content_hash` are shortened placeholders,
not values that recompute. Only the field shapes and the accept/reject reason
are meaningful.

Valid shape (passes every section 3 check):

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

Invalid — head mismatch. `stale` if `aaa000` is a head this caller reviewed
earlier, otherwise `wrong-head`:

```json
{"task_id": "case-014", "base_sha": "aaa111", "head_sha": "aaa000",
"caller_run_id": "<run-id>", "child_session_id": "sess-9",
"completion": "completed", "verdict": "APPROVE",
"findings": [], "content_hash": "0000"}
```

These examples illustrate the runbook only; the eval provider's
completion/trace/accounting code is owned separately under task #25.
