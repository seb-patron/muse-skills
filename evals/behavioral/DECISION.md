# Retain / replace / retire decision record

Revision rounds: 2/3

Status: finalist frozen; production decision pending held-out evidence. No
production-skill change is authorized by this spike.

Decision rule:

- **Retain** the current skill only if it is competitive with the finalist on
  verified all-gold and blocking recall, supported precision, false approvals, and
  completion, without materially higher latency/token cost.
- **Replace** it in a separate follow-up PR only if one frozen candidate wins on
  validation and does not regress the human-adjudicated held-out cases.
- **Retire** it only if no candidate provides a reliable completion/quality tradeoff
  and the current skill's cost or false-approval behavior is unacceptable.

Required evidence before deciding:

1. Train: one run for each of the four hashed candidates on the three train cases.
2. Validation: three repeats for the best two, with finalist hash frozen first.
3. Held-out: one run only after human gold is complete; disclosed held-out cases
   must be replaced before further tuning.
4. Final Luna/Sol references as a milestone, not a wording-tuning loop.
5. Independent non-author Sol review of the combined spike and proportionate repair.

Current live-evaluation status: frozen validation `eval-pNM-2026-09-13T02:31:20`
completed all 12 rows with zero assertion failures or execution errors in 9m26s and
used 697,277 Terra grading tokens across 36 rubric requests. Current and minimal
each completed six clean-control rows with perfect contract/quality scores, zero
false approvals, pinned Spark telemetry, and observed explicit skill activation.
Minimal averaged 28.8s versus current's 31.7s on validation.

Validation cannot measure defect recall, so its clean-quality tie does not overturn
train `eval-pL4-2026-09-13T01:43:26`: current led blocking recall (0.667 versus
0.333), false approvals (1 versus 2), and verdict accuracy (0.667 versus 0.333).
`spike-current` is therefore frozen as the sole held-out finalist at SHA-256
`dd4f217a140beb155f209f9215932fa44e84936096796ade725f49199c239380`.
This selection is provisional: both finalists missed substantial train gold, and
minimal was materially faster on train. No retain/replace/retire decision is made
until human-adjudicated held-out evidence and the final reference milestone exist.

The exact repaired probe
`eval-T5V-2026-09-13T01:27:21` completed one current-skill Muse/Spark row plus all
three Terra rubrics in 4m09s with zero execution errors. It scored 0 blocking and
all-gold recall, 1 supported precision, and the correct `NEEDS_FIXES` verdict; it is
a wiring check and is excluded from the comparative train table. The prior
interrupted train's four ungraded Muse outputs and the earlier session-lease failures
remain diagnostic only. Held-out model rows have not yet run. The
committed historical baseline remains 8 cross-model rows, 21m27s, 2,839,735
candidate-plus-grader tokens, 1 pass, 5 quality failures, and 2 timeout errors. Those
results are preserved and not reinterpreted as a finalist decision. Held-out model
rows have not run and remain locked by pending human adjudication.
