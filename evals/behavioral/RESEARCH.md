# Review mechanism research

Revision rounds: 0/3

This is mechanism research for the spike, not a claim that any upstream system
transfers unchanged. The blueprint was inspected at `agent-review-evals` main
`4c2530343d92a1ca000bbeea863a5be3a14d47d0`; no files were migrated from it.

| System | Exact inspected revision | License evidence | Mechanism worth testing | Why it does not transfer cleanly |
| --- | --- | --- | --- | --- |
| [PR-Agent](https://github.com/The-PR-Agent/pr-agent) | `v0.39.0`, release commit [`8e4d32e5497defd43c023a404f73560c62728961`](https://github.com/The-PR-Agent/pr-agent/commit/8e4d32e5497defd43c023a404f73560c62728961) | The pinned repository `LICENSE` states MIT; the project-transition notice describes an Apache-2.0 handoff, so reuse requires legal confirmation at the exact source revision. | Explicit review sections, risk/security/test dimensions, repository context, structured findings, and large-diff chunking. | It is provider/API-oriented: persistent findings, inline anchors, labels, checks, permissions, and GitHub state have no direct Muse equivalent. Its `SKILL.md` support is not evidence of Muse-loader compatibility. |
| [AI Diff Reviewer](https://github.com/DailybotHQ/ai-diff-reviewer) | `v2.0.1`, peeled tag commit [`87109d628ccaee7cd62cdac851afdaecc87bba7a`](https://github.com/DailybotHQ/ai-diff-reviewer/commit/87109d628ccaee7cd62cdac851afdaecc87bba7a) | MIT text verified in the pinned `LICENSE`; the exact license is also linked from the [repository](https://github.com/DailybotHQ/ai-diff-reviewer/blob/v2.0.1/LICENSE). | One methodology across local/CI surfaces, focused context reads, explicit severity, evidence-backed findings, and iteration-aware deduplication. | CI-side comments, labels, GitHub permissions, provider credentials, and stateful deduplication do not belong in a local Muse skill. “Same prompt” does not guarantee same agent behavior. |
| [PR-AF](https://github.com/Agent-Field/pr-af) | Public `main` at [`48ae7eeb4f07779004db6354728d49ca7b36dbc3`](https://github.com/Agent-Field/pr-af/commit/48ae7eeb4f07779004db6354728d49ca7b36dbc3) | Package metadata declares Apache-2.0 in [pyproject.toml](https://raw.githubusercontent.com/Agent-Field/pr-af/48ae7eeb4f07779004db6354728d49ca7b36dbc3/pyproject.toml). | Dynamic risk planning, evidence extraction from AST/callers/imports, cross-reference and deduplication, adversarial challenges, synthesis, and coverage gates. | AgentField control-plane services, Docker, Python/Go nodes, dynamic fan-out, credentials, GitHub side effects, and 35–50 minute reviews are disproportionate to Muse's lightweight bounded procedure. |
| [PR-AF Martian benchmark](https://github.com/Agent-Field/pr-af/tree/main/benchmark/martian-code-review-bench) | Public `main` at [`2b092b670f7d6cae6d429babaaee18948b4bdacb`](https://github.com/withmartian/code-review-benchmark/commit/2b092b670f7d6cae6d429babaaee18948b4bdacb) | MIT in the benchmark [LICENSE](https://github.com/withmartian/code-review-benchmark/blob/2b092b670f7d6cae6d429babaaee18948b4bdacb/LICENSE). | Blind real-PR cases, human-curated golden comments, semantic matching, multiple judges, adversarial disagreement sampling, later-fix tracking, and audit trails. | The benchmark reports 50 PRs/173 gold comments while PR-AF runs 38 recoverable cases; judge calibration, contamination, gold ceilings, and product-vs-model effects limit direct comparison. It is evidence methodology, not a Muse runtime. |

## Selection

The spike uses a small subset of these mechanisms: risk claims, focused context,
one falsifier per claim, a hard probe cap, per-finding refutation, explicit
severity, and structured local evidence. The upstream-derived candidate is based
on the MIT-compatible AI Diff Reviewer mechanisms at the pinned commit. It does
not copy upstream prompt prose or CI behavior. PR-AF's architecture remains a
research reference rather than an imported dependency.
