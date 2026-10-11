# Evaluation method

## What is compared

| | A · single agent | B · staged (Analyst → Reviewer) |
|---|---|---|
| Model | same (`groq:openai/gpt-oss-120b`, temperature 0, reasoning effort low) | same |
| Tools, policy engine, guardrails, loop, fallback | same | same |
| Orchestration | one agent gathers evidence and decides | Analyst gathers into a typed evidence pack; Reviewer (no tools) decides |

Only the orchestration differs. A **rules-only baseline** (the policy engine alone, 0 LLM calls) is reported as a reference point.

## Test set (`evals/extended_cases.json`)

Same cases, same order, for both architectures. They run against `evals/fixtures/data`: the shipped data plus synthetic vendors, vendor-risk records and requests, built by `evals/build_fixtures.py`. They go through the same interfaces hidden cases would use (`PROCUREMENT_DATA_DIR` and the mock vendor-risk HTTP API).

- **core (16):** the 6 public cases, the other 4 dataset requests, and 6 edge cases: exactly $10,000 from a new vendor (Legal at ≥ $10k, Finance only above), prompt injection inside **vendor-risk API notes**, a vendor unknown to both registry and API (404), a review 366 days old, an existing company-wide wiki covering a "team wiki" request, and the API down for an approved vendor.
- **full (+7):** $25,000.01 (CFO), exactly $1,000 (Manager only), cost equal to the available budget, review exactly 365 days old (still current), employee PII via HRIS, a vague request with undecided data access, and a limited-use AI tool for confidential contracts.

Each case's gold expectation was written from the policy text, not from the engine's output: accepted labels, approvals that must and must not appear (e.g. no CFO under $25k), required/forbidden risk flags, required missing-information items. Where two labels are defensible (e.g. specialist vs manual review for an expired assessment), both are accepted.

## Scoring (`evals/scoring.py`)

| Column | Pass when |
|---|---|
| `correct_next_action` | recommendation label is one of the gold labels |
| `grounded_evidence` | every evidence source is a tool that ran in that run (or the policy guardrail); every $ amount and date in agent-written findings appears in that run's tool output; ≥ 2 evidence items |
| `policy_followed` | all required approvals/flags/missing items present; no forbidden approval or flag |
| `human_escalation_correct` | human review flagged; required Finance/Security/Privacy/Legal reviewers routed; label not less restrictive than gold |

The same checks also run on the agent's **raw draft, before guardrails**. Final decisions share one safety net, so the raw draft is where the architectures themselves differ. We also report the share of the policy floor the draft included, and guardrail corrections by type.

Cost and speed: LLM calls, tool calls, input/output tokens, **active latency** (wall-clock minus free-tier throttling waits) and wall-clock.

## Running under free-tier quotas

Groq's free tier for `gpt-oss-120b` allows about 8k tokens per minute and has a daily token cap. A run uses about 11–13k input tokens. So the runner:

- appends each finished run to `evals/results/runs.jsonl` immediately and **resumes** on re-run (skipping recorded runs for the same model);
- **stops** on a daily-quota error without recording it. A rules-only fallback caused by quota would measure the quota, not the architecture;
- runs cases as A/B **pairs**, so partial results stay comparable;
- regenerates `summary.md`, `comparison.csv` and `results_{single,staged}.csv` (template columns) from the saved runs.

Agent failures that are *not* quota-related (no valid output, provider errors) are recorded as results, with `fallback = true`, because robustness is part of what is being compared.

## Gold revisions (made during the evaluation, applied equally to both architectures)

- **PUB-01 (SignFlow add-on, 3 seats):** gold was `Proceed to approval routing` only. Two different models independently answered `Use existing tool / confirm need`. Re-reading policy §3 ("Exact duplicates **or unused existing capacity** should be reviewed before creating a new purchase") with 45 company-wide SignFlow seats already licensed, confirming spare capacity first is defensible. Both labels are now accepted. Reports are always regenerated from the saved runs, so this applies retroactively and identically to A and B.

## Findings recorded during the runs

- **Infrastructure failures were excluded, not scored.** The first `gpt-oss-20b` pilot run fell back to rules-only because per-minute 429s were counted against the error-retry budget, and because the model leaked a harmony channel tag into a tool name (`search_software_catalog<|channel|>commentary`) and kept passing an unsupported `request_id` argument. All three were fixed in the client/tools (separate rate-limit budget, tag stripping, unknown arguments ignored with a note), and the run was deleted and redone. Capacity failures (daily cap, persistent rate limiting) are never recorded as results.
- **Guardrail false positive (found in PUB-02 · B · Qwen):** the "drop missing-information items contradicted by the request" rule removed a legitimate question about *what personal data* the vendor would process, because it contains the phrase "data access". It didn't change the outcome in that run. The evaluation process kept the original rule for every core run (consistency). The rule was tightened in the code afterwards, with a regression test (see `docs/assumptions.md`).
- **Guardrail flaw: follow-up questions forced "Request clarification"** (PUB-06, DS-1007, EDGE-13 and others, in both architectures). The recommendation floor treated *any* missing-information item, including the model's own follow-up questions about the vendor, as "ask the requester", which overwrote correct "Manual review" drafts. Fixed: the clarification floor now comes only from the engine's section 1 check.
- **Follow-up questions on complete requests** (starter harness PUB-01 for B: "3 missing-information items, expected 0"). They now go to the next step as open questions.
- **Measuring guardrail fixes without new model calls:** each saved run keeps the agent's draft, and the policy floor is deterministic, so `run_comparison.py` replays the current guardrail code on every saved draft and reports it alongside the as-run scores. As run: A 10/16, B 8/16. With the fixed guardrails replayed: A 13/16, B 13/16. All six remaining failures are model-judgement errors on existing-tool overlap (PUB-02, PUB-03, EDGE-11) or over-caution (DS-1010, B).
- **Latency outliers:** one B run (PUB-05) hit a network connection stall (667 s "active"), and the machine slept or hibernated during a later public-harness run. Medians are reported next to means.
