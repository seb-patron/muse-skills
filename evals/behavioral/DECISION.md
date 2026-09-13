# Retain / replace / retire decision record

Revision rounds: 0/3

Status: pending evidence. No production-skill change is authorized by this spike.

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

Current live-evaluation status: the repaired 12-row Muse/Spark train was attempted
with local execution permission, but its first row did not complete within the
roughly ten-minute host window and the process was stopped; no quality row completed
and no model tokens were observed. Earlier sandboxed attempts produced 12 immediate
session-lease errors before the repair. No validation or held-out model rows have
been run. The only measured quality evidence remains the committed historical
baseline: 8 cross-model rows, 21m27s, 2,839,735 candidate-plus-grader tokens,
1 pass, 5 quality failures, and 2 timeout errors. Those results are preserved and
not reinterpreted as a finalist decision.
