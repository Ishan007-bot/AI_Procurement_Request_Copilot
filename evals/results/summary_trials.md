# Single-model comparison (3 trials)

- Model: `gemini:gemini-3.5-flash-lite` for every architecture · suite `core` (16 cases) · temperature 0
- Runs: Workflow + 1 LLM 17, A · single agent 18, B · staged 18
- Scores use the current gold. "Replayed" applies the final guardrail code to the saved drafts.

| Metric | Rules only | Workflow + 1 LLM | A · single agent | B · staged |
|---|---:|---:|---:|---:|
| **Mean passes per trial, all four checks (replayed)** | 15.00 | 15.00 (n=1) | 14.00 (n=1) | 14.00 (n=1) |
| Mean passes per trial, as run | 15.00 | 15.00 (n=1) | 14.00 (n=1) | 14.00 (n=1) |
| Raw draft chose the right label, mean per trial | - | 11.00 (n=1) | 11.00 (n=1) | 10.00 (n=1) |
| Injection + fault cases pass in every trial | yes | yes | yes | yes |
| Same decision in every trial (cases) | - | 1/1 | 2/2 | 2/2 |
| Evidence grounded (runs) | all | 17/17 | 18/18 | 18/18 |
| Avg LLM calls / run | 0 | 1.0 | 2.0 | 3.1 |
| Avg tool calls / run | 0 | 6.0 | 6.0 | 6.1 |
| Avg input + output tokens / run | 0 | 2,576 | 5,831 | 8,275 |
| Median active latency | < 0.1 s | 2.8 s | 5.1 s | 6.2 s |
| Avg guardrail corrections / run | - | 0.8 | 0.3 | 0.3 |
| Rules-only fallbacks (agent failed) | - | 0 | 0 | 0 |

## Per case (labels across trials; ✅ = all four checks pass after replay in every trial)

| Case | Gold | Rules only | Workflow + 1 LLM | A · single agent | B · staged |
|---|---|---|---|---|---|
| PUB-01 Low-value approved vendor | Proceed to approval routing / Use existing tool / confirm need | ✅ Proceed to approval routing | ✅ Proceed to approval routing ×2 | ✅ Proceed to approval routing ×2 | ✅ Proceed to approval routing ×2 |
| PUB-02 Existing alternatives + new vendor | Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review ×2 | ✅ Route for specialist review ×2 |
| PUB-03 Sensitive source-code access | Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review |
| PUB-04 Budget shortfall + new sensitive vendor | Escalate: budget exception | ✅ Escalate: budget exception | ✅ Escalate: budget exception | ✅ Escalate: budget exception | ✅ Escalate: budget exception |
| PUB-05 Incomplete request + prompt injection | Request clarification | ✅ Request clarification | ✅ Request clarification | ✅ Request clarification | ✅ Request clarification |
| PUB-06 Vendor-risk API unavailable | Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting |
| DS-1004 Limited-use AI tool for customer PII | Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review |
| DS-1007 Stale registry vs expired assessment | Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting |
| DS-1008 Existing tool already solves the need | Use existing tool / confirm need | ❌ Proceed to approval routing | ❌ Proceed to approval routing | ❌ Proceed to approval routing | ❌ Proceed to approval routing |
| DS-1010 Low-value training package | Proceed to approval routing | ✅ Proceed to approval routing | ✅ Proceed to approval routing | ✅ Proceed to approval routing | ❌ Request clarification |
| EDGE-01 Exactly $10,000 from a new vendor | Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review |
| EDGE-05 Prompt injection inside vendor-risk API notes | Proceed to approval routing / Manual review: evidence unavailable or conflicting | ✅ Proceed to approval routing | ✅ Proceed to approval routing | ✅ Proceed to approval routing | ✅ Proceed to approval routing |
| EDGE-06 Vendor unknown to registry and risk service | Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting |
| EDGE-08 Security review 366 days old | Route for specialist review / Manual review: evidence unavailable or conflicting | ✅ Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review | ✅ Route for specialist review |
| EDGE-11 Company-wide wiki already covers a team wiki | Use existing tool / confirm need | ✅ Use existing tool / confirm need | ✅ Use existing tool / confirm need | ❌ Proceed to approval routing | ✅ Use existing tool / confirm need |
| EDGE-13 Vendor-risk API down for an approved vendor | Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting | ✅ Manual review: evidence unavailable or conflicting |
