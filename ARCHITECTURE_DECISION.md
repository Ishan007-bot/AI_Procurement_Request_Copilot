# Architecture Decision Memo

## Decision
Ship **Architecture A: the single agent**, on top of the deterministic policy engine and guardrails.

## Evidence
Same 16 core cases (6 public, 4 dataset, 6 edge cases) for both. Each case's A and B ran on the same model: `gpt-oss-20b` for 8 cases, `qwen3.8-27b` for 8 (Groq free tier). Details: `evals/results/summary.md`.

| Metric | Single agent | Staged / 2-agent |
|---|---:|---:|
| All four quality checks pass (as run) | 10/16 | 8/16 |
| Same, current guardrails replayed on saved drafts | **13/16** | **13/16** |
| Raw draft chose the right label | 10/16 | 11/16 |
| Evidence grounded / policy followed | 16/16 · 16/16 | 16/16 · 15/16 |
| Median active latency | 6.7 s | 10.4 s |
| Avg LLM calls | 4.1 | 5.0 |
| Avg input / output tokens | 7.4k / 1.1k | 9.0k / 2.3k |
| Guardrail corrections per run | 0.7 | 1.2 |

The rules-only baseline (no LLM) scores 15/16.

## Trade-offs
- **Quality: a tie.** B's drafts got one more label right, but its Reviewer raised five times as many dropped follow-up questions and was over-cautious on a $950 training pack. Neither caught the wiki overlap on `gpt-oss-20b`.
- **Cost: B is worse on every axis.** +22% input tokens, 2× output tokens, +56% median latency, one more LLM call, and a second prompt and hand-off schema to maintain.
- **Safety is shared.** Grounding, mandatory approvals and escalation come from deterministic code both use. Every final decision included every policy-required approval.

## Also learned
- The engine does most of the policy work. The LLM's real contribution is judging existing-tool overlap (DS-1008, which rules can't do), and that judgement is also the main error source (PUB-02, PUB-03, EDGE-11). It should stay advisory.
- The evaluation found a guardrail flaw. Model follow-up questions forced "Request clarification" over correct "Manual review" drafts, causing most as-run failures in both architectures. It was fixed and verified by replaying the saved drafts, with no new model calls.

## Risks and what to validate before production
- **Small sample:** 16 cases, one run each, two small free-tier models. The daily token cap blocked the intended `gpt-oss-120b` run. Repeat on the production model.
- **Overlap judgement:** keep "Use existing tool" as a suggestion a human confirms, and add seat-utilisation data.
- **Integration:** live registry and vendor-risk feeds, authentication, and an audited approval workflow.

## Why A is the right MVP
A matches B on quality while being cheaper, faster and easier to operate. The properties that matter (no missed approvals, no unverified evidence, conservative handling of missing or conflicting data, injection resistance) live in shared deterministic code, not in the orchestration. B's separation bought no accuracy. It's worth revisiting only if the evidence pack must become an interface for other systems.
