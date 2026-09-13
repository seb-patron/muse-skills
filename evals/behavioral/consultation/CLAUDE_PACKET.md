# Claude consultation packet (pending)

Revision rounds: 0/3

This sanitized packet contains only public, non-held-out evidence. It was prepared
for a model-neutral read-only consultation and was not transmitted after the
external-disclosure guard rejected the dispatch. No held-out gold is included.

Repository revision: `338d85855c8caf669490d065d8dda40f2c9d1b2d`.

Observed non-held-out evidence:

- Existing cross-model smoke: 8 rows, 21m27s, 2,839,735 candidate-plus-grader
  tokens, 1 pass, 5 quality failures, and 2 timeout errors.
- Luna control and current-skill treatment both approved the broken `e2e3f5e`
  head and recalled 0/5 verified defects.
- Luna control found the `33ebbdd` trailing-header defect; current-skill
  treatment missed it and approved.
- Sol control found supported novel findings but recalled none of the finite gold.
- Both Sol current-skill rows timed out while running mutation/revert probes and
  emitted no final review.
- Verified gold is the five-defect broken head and the one-defect header-gap head;
  repaired issues must not be resurrected.

## Questions

1. Give a row-by-row failure explanation, separating observations from hypotheses.
2. Identify instructions to remove, retain, or make optional.
3. Propose one short candidate procedure.
4. Predict that candidate's failure modes.

Do not treat this consultation as human adjudication, grading, or a final decision.
