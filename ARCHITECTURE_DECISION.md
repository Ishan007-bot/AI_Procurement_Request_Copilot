# Architecture Decision Memo

## Decision
Between A (single agent) and B (Analyst → Reviewer), **A is the better agent architecture**: same quality, lower cost. But we ship the simpler rung below both: **workflow + 1 LLM call**. Code gathers the evidence in a fixed order, and one model call makes the judgement. The deterministic policy engine and guardrails sit underneath all three.

## Evidence
Final run: one model for every architecture (`gemini-3.5-flash-lite`), the same 16 core cases (6 public, 4 dataset, 6 edge cases), 3 trials, temperature 0. Details: `evals/results/summary_trials.md`.

| Metric | Workflow + 1 LLM | Single agent | Staged / 2-agent |
|---|---:|---:|---:|
| Mean cases passing all four checks (of 16) | **15.67** | 13.67 | 14.33 |
| Same decision in every trial | 15/16 | 16/16 | 13/16 |
| Injection and fault cases pass in every trial | yes | yes | yes |
| Evidence grounded | all | all | all |
| LLM calls per request | **1.0** | 2.1 | 3.1 |
| Tokens per request | **2.6k** | 6.0k | 8.1k |
| Median active latency | **2.9 s** | 4.9 s | 6.9 s |

The rules-only baseline scores 15/16. An earlier run (two Groq models, paired by case) gave A and B 13/16 each. The ranking holds: the extra orchestration buys no accuracy.

## Why not more agents
In every run, A called the same five evidence tools (plus an occasional policy lookup). A fixed path is a workflow, so a model choosing it adds cost, not judgement. B's split is legitimate, and its Reviewer sometimes caught a weak "existing tool covers it" claim. But it was the least consistent (same decision in 13/16 cases across trials) and over-asked: clarification on a $950 training pack, and five times as many dropped follow-up questions. And it costs 3× the workflow's calls.

## Trade-offs
- The workflow can't take an unusual evidence path. It doesn't need to: policy mandates the same checks for every request.
- The model's real job is judging existing-tool overlap and explaining the outcome. It is also the main error source: TaskFlow already covers DS-1008, and that was caught in only 2 of 9 runs (both by the workflow). So that judgement stays advisory, and a human confirms it.
- The guardrails do slightly more work under the workflow (0.7 corrections per run vs 0.5 for A). This is by design: code holds every threshold and trigger.

## Risks / validate before production
- Small sample: 16 cases, 3 trials, free-tier models. Repeat on the production model with a held-out set.
- The evaluation found two guardrail flaws (model follow-up questions forcing "clarification"). Both were fixed and verified by replaying the saved drafts.
- Add seat-utilisation data, live registry and vendor feeds, authentication, and an audited approval workflow.

## Why this is the right MVP
It reaches the best quality measured, passes every safety case, and costs a third of B per request. AI interprets, code applies the policy, humans decide.
