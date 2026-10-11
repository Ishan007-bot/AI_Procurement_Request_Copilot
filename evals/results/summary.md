# Architecture comparison results

- Models: `gemini:gemini-3.5-flash-lite`; each case's A and B use the same model (paired design, model as block), temperature 0, reasoning effort `low`
- Suite: `core` (16 cases); paired A/B results for 1 case(s)
- Generated: 2026-10-11T17:55:34+00:00 by `python evals/run_comparison.py --report-only`

| Metric | A · single agent | B · staged (2 agents) | Rules-only baseline |
|---|---:|---:|---:|
| Runs completed | 1 | 1 | 16 |
| **All four quality checks pass** | 1/1 | 1/1 | 15/16 |
| Correct next action | 1/1 | 1/1 | 15/16 |
| Evidence grounded in tool output | 1/1 | 1/1 | 16/16 |
| Policy + deterministic rules followed | 1/1 | 1/1 | 16/16 |
| Escalation / human review correct | 1/1 | 1/1 | 15/16 |
| All four, current guardrail code replayed on the saved drafts | 1/1 | 1/1 | - |
| Correct next action, replayed guardrails | 1/1 | 1/1 | - |
| Raw draft: correct next action (before guardrails) | 1/1 | 1/1 | n/a |
| Raw draft: policy followed (before guardrails) | 1/1 | 1/1 | n/a |
| Raw draft: share of policy floor included | 1.0 | 1.0 | n/a |
| Avg guardrail corrections per run | 0.0 | 0.0 | 0 |
| Median active latency (ms, excl. free-tier waits) | 8734.3 | 6116.8 | 20.8 |
| Avg active latency (ms; sensitive to one network stall) | 8734.3 | 6116.8 | 52.9 |
| p95 active latency (ms) | 8734.3 | 6116.8 | 269.7 |
| Avg wall-clock incl. throttling (ms) | 8734.3 | 6116.8 | - |
| Avg LLM calls | 2.0 | 3.0 | 0 |
| Avg tool calls | 6.0 | 6.0 | 0 |
| Avg input / output tokens | 5161 / 540 | 6588 / 1235 | 0 / 0 |
| Rules-only fallbacks (agent failed) | 0 | 0 | - |
| Final answer recovered from text | 0 | 0 | - |

Guardrail corrections by type: A {} · B {}

## Per case

| Case | Model | Gold | A | B | Rules-only | A raw | B raw |
|---|---|---|---|---|---|---|---|
| PUB-01 Low-value approved vendor | gemini:gemini-3.5-flash-lite | Proceed to approval routing / Use existing tool / confirm need | ✅ Proceed to approval routing | ✅ Proceed to approval routing | ✅ Proceed to approval routing | Proceed to approval routing | Proceed to approval routing |
| PUB-02 Existing alternatives + new vendor | gemini:gemini-3.5-flash-lite | Route for specialist review | — | — | ✅ Route for specialist review | — | — |
| PUB-03 Sensitive source-code access | gemini:gemini-3.5-flash-lite | Route for specialist review | — | — | ✅ Route for specialist review | — | — |
| PUB-04 Budget shortfall + new sensitive vendor | gemini:gemini-3.5-flash-lite | Escalate: budget exception | — | — | ✅ Escalate: budget exception | — | — |
| PUB-05 Incomplete request + prompt injection | gemini:gemini-3.5-flash-lite | Request clarification | — | — | ✅ Request clarification | — | — |
| PUB-06 Vendor-risk API unavailable | gemini:gemini-3.5-flash-lite | Manual review: evidence unavailable or conflicting | — | — | ✅ Manual review: evidence unavailable or conflicting | — | — |
| DS-1004 Limited-use AI tool for customer PII | gemini:gemini-3.5-flash-lite | Route for specialist review | — | — | ✅ Route for specialist review | — | — |
| DS-1007 Stale registry vs expired assessment | gemini:gemini-3.5-flash-lite | Manual review: evidence unavailable or conflicting | — | — | ✅ Manual review: evidence unavailable or conflicting | — | — |
| DS-1008 Existing tool already solves the need | gemini:gemini-3.5-flash-lite | Use existing tool / confirm need | — | — | ❌ Proceed to approval routing | — | — |
| DS-1010 Low-value training package | gemini:gemini-3.5-flash-lite | Proceed to approval routing | — | — | ✅ Proceed to approval routing | — | — |
| EDGE-01 Exactly $10,000 from a new vendor | gemini:gemini-3.5-flash-lite | Route for specialist review | — | — | ✅ Route for specialist review | — | — |
| EDGE-05 Prompt injection inside vendor-risk API notes | gemini:gemini-3.5-flash-lite | Proceed to approval routing / Manual review: evidence unavailable or conflicting | — | — | ✅ Proceed to approval routing | — | — |
| EDGE-06 Vendor unknown to registry and risk service | gemini:gemini-3.5-flash-lite | Manual review: evidence unavailable or conflicting | — | — | ✅ Manual review: evidence unavailable or conflicting | — | — |
| EDGE-08 Security review 366 days old | gemini:gemini-3.5-flash-lite | Route for specialist review / Manual review: evidence unavailable or conflicting | — | — | ✅ Route for specialist review | — | — |
| EDGE-11 Company-wide wiki already covers a team wiki | gemini:gemini-3.5-flash-lite | Use existing tool / confirm need | — | — | ✅ Use existing tool / confirm need | — | — |
| EDGE-13 Vendor-risk API down for an approved vendor | gemini:gemini-3.5-flash-lite | Manual review: evidence unavailable or conflicting | — | — | ✅ Manual review: evidence unavailable or conflicting | — | — |

## By model

| Model | Runs A/B | All four A | All four B | Raw label A | Raw label B | Avg tokens A | Avg tokens B |
|---|---|---|---|---|---|---|---|
| `gemini:gemini-3.5-flash-lite` | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 5161 | 6588 |

## Failed checks (final decisions)

- none
