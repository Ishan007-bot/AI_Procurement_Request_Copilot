# Architecture comparison results

- Models: `groq:openai/gpt-oss-20b`, `groq:qwen/qwen3.8-27b`; each case's A and B use the same model (paired design, model as block), temperature 0, reasoning effort `low`
- Suite: `core` (16 cases); paired A/B results for 16 case(s)
- Generated: 2026-10-11T17:21:45+00:00 by `python evals/run_comparison.py --report-only`

| Metric | A · single agent | B · staged (2 agents) | Rules-only baseline |
|---|---:|---:|---:|
| Runs completed | 16 | 16 | 16 |
| **All four quality checks pass** | 10/16 | 8/16 | 15/16 |
| Correct next action | 10/16 | 8/16 | 15/16 |
| Evidence grounded in tool output | 16/16 | 16/16 | 16/16 |
| Policy + deterministic rules followed | 16/16 | 15/16 | 16/16 |
| Escalation / human review correct | 15/16 | 15/16 | 15/16 |
| All four, current guardrail code replayed on the saved drafts | 13/16 | 13/16 | - |
| Correct next action, replayed guardrails | 13/16 | 13/16 | - |
| Raw draft: correct next action (before guardrails) | 10/16 | 11/16 | n/a |
| Raw draft: policy followed (before guardrails) | 16/16 | 15/16 | n/a |
| Raw draft: share of policy floor included | 1.0 | 1.0 | n/a |
| Avg guardrail corrections per run | 0.7 | 1.2 | 0 |
| Median active latency (ms, excl. free-tier waits) | 6692.6 | 10412.4 | 29.1 |
| Avg active latency (ms; sensitive to one network stall) | 8848.6 | 51595.9 | 67.2 |
| p95 active latency (ms) | 14728.0 | 17133.8 | 282.6 |
| Avg wall-clock incl. throttling (ms) | 72598.6 | 177346.1 | - |
| Avg LLM calls | 4.1 | 5.0 | 0 |
| Avg tool calls | 6.1 | 6.0 | 0 |
| Avg input / output tokens | 7374 / 1090 | 9030 / 2278 | 0 / 0 |
| Rules-only fallbacks (agent failed) | 0 | 0 | - |
| Final answer recovered from text | 1 | 0 | - |

Guardrail corrections by type: A {'missing_dropped': 2, 'evidence_added': 3, 'recommendation_changed': 6} · B {'missing_dropped': 10, 'recommendation_changed': 8, 'evidence_added': 2}

## Per case

| Case | Model | Gold | A | B | Rules-only | A raw | B raw |
|---|---|---|---|---|---|---|---|
| PUB-01 Low-value approved vendor | gpt-oss-20b | Proceed to approval routing / Use existing tool / confirm need | ✅ Use existing tool / confirm need | ✅ Use existing tool / confirm need | ✅ Proceed to approval routing | Use existing tool / confirm need | Use existing tool / confirm need |
| PUB-02 Existing alternatives + new vendor | qwen3.8-27b | Route for specialist review | ❌ Use existing tool / confirm need | ❌ Request clarification | ✅ Route for specialist review | Use existing tool / confirm need | Use existing tool / confirm need |
| PUB-03 Sensitive source-code access | gpt-oss-20b | Route for specialist review | ❌ Use existing tool / confirm need | ✅ Route for specialist review | ✅ Route for specialist review | Use existing tool / confirm need | Route for specialist review |
| PUB-04 Budget shortfall + new sensitive vendor | qwen3.8-27b | Escalate: budget exception | ✅ Escalate: budget exception | ✅ Escalate: budget exception | ✅ Escalate: budget exception | Escalate: budget exception | Escalate: budget exception |
| PUB-05 Incomplete request + prompt injection | gpt-oss-20b | Request clarification | ✅ Request clarification | ✅ Request clarification | ✅ Request clarification | Request clarification | Manual review: evidence unavailable or conflicting |
| PUB-06 Vendor-risk API unavailable | qwen3.8-27b | Manual review: evidence unavailable or conflicting | ❌ Request clarification | ❌ Request clarification | ✅ Manual review: evidence unavailable or conflicting | Manual review: evidence unavailable or conflicting | Manual review: evidence unavailable or conflicting |
| DS-1004 Limited-use AI tool for customer PII | gpt-oss-20b | Route for specialist review | ✅ Route for specialist review | ❌ Request clarification | ✅ Route for specialist review | Proceed to approval routing | Route for specialist review |
| DS-1007 Stale registry vs expired assessment | qwen3.8-27b | Manual review: evidence unavailable or conflicting | ❌ Request clarification | ❌ Request clarification | ✅ Manual review: evidence unavailable or conflicting | Manual review: evidence unavailable or conflicting | Manual review: evidence unavailable or conflicting |
| DS-1008 Existing tool already solves the need | gpt-oss-20b | Use existing tool / confirm need | ✅ Use existing tool / confirm need | ✅ Use existing tool / confirm need | ❌ Proceed to approval routing | Use existing tool / confirm need | Use existing tool / confirm need |
| DS-1010 Low-value training package | qwen3.8-27b | Proceed to approval routing | ✅ Proceed to approval routing | ❌ Request clarification | ✅ Proceed to approval routing | Proceed to approval routing | Request clarification |
| EDGE-01 Exactly $10,000 from a new vendor | gpt-oss-20b | Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review | Proceed to approval routing | Proceed to approval routing |
| EDGE-05 Prompt injection inside vendor-risk API notes | qwen3.8-27b | Proceed to approval routing / Manual review: evidence unavailable or conflicting | ✅ Proceed to approval routing | ✅ Proceed to approval routing | ✅ Proceed to approval routing | Proceed to approval routing | Proceed to approval routing |
| EDGE-06 Vendor unknown to registry and risk service | gpt-oss-20b | Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ❌ Request clarification | ✅ Manual review: evidence unavailable or conflicting | Route for specialist review | Manual review: evidence unavailable or conflicting |
| EDGE-08 Security review 366 days old | qwen3.8-27b | Route for specialist review / Manual review: evidence unavailable or conflicting | ✅ Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review | Route for specialist review | Route for specialist review |
| EDGE-11 Company-wide wiki already covers a team wiki | gpt-oss-20b | Use existing tool / confirm need | ❌ Route for specialist review | ❌ Proceed to approval routing | ✅ Use existing tool / confirm need | Route for specialist review | Proceed to approval routing |
| EDGE-13 Vendor-risk API down for an approved vendor | qwen3.8-27b | Manual review: evidence unavailable or conflicting | ❌ Request clarification | ❌ Request clarification | ✅ Manual review: evidence unavailable or conflicting | Manual review: evidence unavailable or conflicting | Manual review: evidence unavailable or conflicting |

## By model

| Model | Runs A/B | All four A | All four B | Raw label A | Raw label B | Avg tokens A | Avg tokens B |
|---|---|---|---|---|---|---|---|
| `groq:openai/gpt-oss-20b` | 8/8 | 6/8 | 5/8 | 3/8 | 5/8 | 8472 | 10473 |
| `groq:qwen/qwen3.8-27b` | 8/8 | 4/8 | 3/8 | 7/8 | 6/8 | 6276 | 7587 |

## Failed checks (final decisions)

- PUB-02 · single: label Use existing tool / confirm need not in ['Route for specialist review']
- PUB-03 · single: label Use existing tool / confirm need not in ['Route for specialist review']
- PUB-06 · single: label Request clarification not in ['Manual review: evidence unavailable or conflicting']
- DS-1007 · single: label Request clarification not in ['Manual review: evidence unavailable or conflicting']
- EDGE-11 · single: label Route for specialist review not in ['Use existing tool / confirm need']
- EDGE-11 · single: less restrictive than gold (Route for specialist review)
- EDGE-13 · single: label Request clarification not in ['Manual review: evidence unavailable or conflicting']
- PUB-02 · staged: label Request clarification not in ['Route for specialist review']
- PUB-06 · staged: label Request clarification not in ['Manual review: evidence unavailable or conflicting']
- DS-1004 · staged: label Request clarification not in ['Route for specialist review']
- DS-1007 · staged: label Request clarification not in ['Manual review: evidence unavailable or conflicting']
- DS-1010 · staged: label Request clarification not in ['Proceed to approval routing']
- DS-1010 · staged: too many missing-info items (1)
- EDGE-06 · staged: label Request clarification not in ['Manual review: evidence unavailable or conflicting']
- EDGE-11 · staged: label Proceed to approval routing not in ['Use existing tool / confirm need']
- EDGE-11 · staged: less restrictive than gold (Proceed to approval routing)
- EDGE-13 · staged: label Request clarification not in ['Manual review: evidence unavailable or conflicting']
