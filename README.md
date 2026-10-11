# AI Procurement Request Copilot

An internal copilot that reviews software and service purchase requests. It gathers evidence with tools, applies the procurement policy deterministically, and recommends the next action. **Humans make every approval.**

Two architectures were built on the same tools, policy engine and guardrails, and compared on the same test set:

- **A: single agent.** One tool-calling agent gathers evidence and decides.
- **B: staged.** An Analyst agent gathers evidence into a typed evidence pack; a Reviewer agent (no tools) decides.

**Ship decision: A, the single agent.** It matches B on quality and is cheaper, faster and simpler (see [Architecture decision](#architecture-decision)).

---

## Quick start

Requires Python 3.11+ (built and tested on 3.12).

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1      macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env            # Windows: Copy-Item .env.example .env
# add GROQ_API_KEY (free: https://console.groq.com/keys) and set LLM_PROVIDER=groq
#   or GEMINI_API_KEY (free: https://aistudio.google.com/apikey) with LLM_PROVIDER=gemini
python verify_setup.py --llm    # preflight + one tiny model call
python run_local.py             # one command: vendor-risk API (:8001) + UI (:8501)
```

Open http://127.0.0.1:8501. Deep link for a demo: `http://127.0.0.1:8501/?request=REQ-1006&run=1`.

**Without an API key** everything still runs. Each request gets a conservative rules-only decision (flag `llm_unavailable`), and the UI says so.

| Command | What it does |
|---|---|
| `python -m unittest` | 150+ tests, no API key needed (the agents are tested with a scripted model) |
| `python evals/run_public_evals.py --architecture single\|staged` | Starter harness: 6 public cases, minimum checks |
| `python evals/run_comparison.py [--suite core\|full]` | A vs B comparison on the same cases and model; resumable, quota-aware |
| `python evals/run_comparison.py --report-only` | Rebuild reports from saved runs |
| `python evals/build_fixtures.py` | Rebuild the edge-case overlay dataset |

Configuration is in `.env` (see `.env.example`): `LLM_PROVIDER` (`groq` / `gemini`), `MODEL_NAME`, `LLM_REASONING_EFFORT`, throttle and retry settings, `VENDOR_RISK_BASE_URL`, `PROCUREMENT_DATA_DIR`. Keys can also come from OS environment variables.

## Product workflow

1. **Request**: pick one from the queue or enter a new one (form).
2. **Intake**: requester, department, reporting line (named Manager / Department Head), and a scan of the request text for embedded instructions. The UI warns before any run.
3. **Evidence**: the agent(s) call tools for budget, existing tools, vendor registry, the external vendor-risk API, and the policy engine. Tool calls stream into the UI live.
4. **Recommendation**: one of seven labels (proceed · use existing tool · specialist review · clarification · budget exception · manual review · do not proceed), with rationale, evidence, required approvals (named where possible), missing information, risk flags and next step.
5. **Guardrails**: deterministic code enforces the policy floor and grounding (below).
6. **Human decision**: the reviewer records route / request info / reject / escalate in the UI (`logs/decisions.jsonl`, `decided_by: human`).

The UI's tabs show approvals and risks, evidence (source and reference per finding), policy checks (budget bar, registry vs vendor-API table with conflicts highlighted, overlapping products), the agent trace (every tool call's input and output, latency, tokens, guardrail corrections) and the raw output JSON. A sidebar toggle **simulates a vendor-risk outage** to show failure handling live.

## Architecture

Diagrams and details: **[docs/architecture.md](docs/architecture.md)**.

| Layer | Responsibility |
|---|---|
| **AI** | Interpret the need, judge whether an existing tool covers it, choose the label, write rationale and next step |
| **Code** | Thresholds, budget, Security/Privacy/Legal triggers, 365-day expiry, registry vs API conflicts, missing fields, injection detection, guardrails |
| **Human** | Every approval, budget exception, specialist sign-off |

**Guardrails** (`src/guardrails.py`): the model's approvals, flags and missing information are merged with the policy engine's floor (it can add, never remove). Evidence citing a tool that didn't run is dropped. A recommendation less restrictive than the evidence allows is raised to the floor. `human_review_required` is always true. Each correction is recorded by type, and that is one of the comparison metrics.

**Tools** (7; 6 deterministic, 1 external HTTP API): `get_request_context`, `check_budget`, `search_software_catalog`, `get_vendor_registry_record`, `get_vendor_risk`, `evaluate_policy_rules`, `lookup_policy_section`.

**Models**: Groq (`openai/gpt-oss-120b` default; the evaluation used `gpt-oss-20b` and `qwen3.8-27b`, see below) and Gemini `gemini-3.8-flash`. Both are free tier and called through their OpenAI-compatible endpoints, with one `openai` client. Temperature 0, reasoning effort `low`.

### Code map

```text
src/
  policy_engine.py   deterministic policy rules -> floor (pure functions)
  evidence.py        collects the engine's inputs (I/O)
  injection.py       untrusted-text detection + <untrusted_data> wrapping
  tools.py           the 7 tools, argument validation, telemetry, model-facing rendering
  llm.py             Gemini/Groq client: throttle, retries, quota fail-fast, provider quirks
  prompts.py         system prompts + structured-output schemas
  agents/loop.py     shared tool-calling loop (repairs, text recovery, iteration cap)
  agents/single_agent.py, agents/staged.py   architectures A and B
  guardrails.py      floor merge, grounding, recommendation floor, rules-only fallback
  solution.py        handle_request(...) adapter + run_copilot(...)
  vendor_client.py   typed vendor-risk client (ok / not_found / unavailable / error)
app.py               Streamlit UI       evals/   harnesses, fixtures, results
```

## Assumptions

All interpretations of the policy and data are in **[docs/assumptions.md](docs/assumptions.md)**. The main ones:

- Reference date 2026-09-30, read from the policy file; a review is current for ≤ 365 days (day 365 current, 366 expired).
- Approval tiers as written. Finance starts **above** $10,000, while Legal for a new vendor starts **at** $10,000; both are applied literally.
- Department Head = first Director/VP strictly above the requester (resolves the "Go To Market" director; nobody approves their own request).
- Unavailable or conflicting vendor evidence is never read as favourable: Security review is added, plus Privacy for sensitive data whose residency can't be verified.
- Detected prompt injection is surfaced and ignored. It never lowers approvals, and doesn't block routing by itself (policy §9).

Starter-pack defects found and fixed (11, each with a test): **[docs/starter_fixes.md](docs/starter_fixes.md)**.

## Evaluation

Method, cases and scoring: **[docs/evaluation.md](docs/evaluation.md)**. Full results: **[evals/results/summary.md](evals/results/summary.md)**; per-run data in `evals/results/runs.jsonl`; template CSVs in `evals/results/results_{single,staged}.csv`.

- **Same test set for both:** 16 core cases (6 public, 4 other dataset requests, 6 edge cases), with gold expectations written from the policy text.
- **Same model within each case.** Groq's free tier caps each model at 200k tokens per day, so the 16 cases were split across `gpt-oss-20b` and `qwen3.8-27b` as a paired design. Both architectures saw the identical model mix.
- **Four quality checks per run:** correct next action, evidence grounded in tool output (every $ amount and date checked), policy followed, and escalation correct. The same checks are also run on the agent's raw draft before guardrails.

| Metric | A · single agent | B · staged (2 agents) | Rules-only baseline |
|---|---:|---:|---:|
| All four checks pass (as run) | 10/16 | 8/16 | 15/16 |
| All four checks pass, current guardrails replayed on saved drafts | **13/16** | **13/16** | - |
| Raw draft chose the right label (before guardrails) | 10/16 | 11/16 | - |
| Evidence grounded in tool output | 16/16 | 16/16 | 16/16 |
| Policy + deterministic rules followed | 16/16 | 15/16 | 16/16 |
| Median active latency (excl. free-tier throttling) | 6.7 s | 10.4 s | < 0.1 s |
| Avg LLM calls / tool calls | 4.1 / 6.1 | 5.0 / 6.0 | 0 / 0 |
| Avg input / output tokens | 7.4k / 1.1k | 9.0k / 2.3k | 0 |
| Guardrail corrections per run | 0.7 | 1.2 | - |
| Rules-only fallbacks | 0 | 0 | - |

**What the evaluation found:**
- **The guardrails did their job.** When drafts said "Proceed" for customer PII or for a $10k new vendor, the floor raised them to "Specialist review". The final decisions never missed a policy-required approval.
- **It also exposed a guardrail flaw.** Model follow-up questions forced "Request clarification" over correct "Manual review" drafts. It was fixed and verified by replaying the saved drafts with no new model calls (10 → 13 for A, 8 → 13 for B).
- **The LLM's added value is judging existing-tool overlap** (DS-1008, which the rules alone get wrong). That judgement is also the main remaining error source (PUB-02, PUB-03, EDGE-11).
- **Starter harness:** `run_public_evals.py` passed 6/6 live for A (`gpt-oss-120b`). The harness's own checks pass 6/6 for both A and B on the saved model decisions with the final guardrails. A live B confirmation on `qwen3.8-27b` is logged in `evals/results/public_staged.log`.
- **Gold revision (documented):** PUB-01 also accepts "Use existing tool / confirm need", per policy §3 (unused capacity).

## Architecture decision

**Ship A, the single agent.** Quality is a tie (13/16 each with the current guardrails). B costs +22% input tokens, 2× output tokens, +56% median latency and one more LLM call per run, and adds a second prompt and hand-off schema to maintain. The properties that matter most (no missed approvals, grounded evidence, conservative handling of unavailable or conflicting data, injection resistance) come from deterministic code that both share, not from the orchestration. Memo (≤ 500 words): **[ARCHITECTURE_DECISION.md](ARCHITECTURE_DECISION.md)**.

## Known limitations

- **Free-tier quotas shape the evaluation.** Groq allows ~8k tokens/min and 200k tokens/day per model; Gemini's free tier allows only 20 requests/day per model. The comparison is 16 cases × 1 run on two small models (paired by case). Variance across repeats and the larger `gpt-oss-120b` weren't measured; `python evals/run_comparison.py --models openai/gpt-oss-120b` resumes that when quota allows.
- `gpt-oss-120b` on Groq makes one tool call per turn (no parallel calls), so a run takes ~6–8 LLM calls. Latency is reported excluding throttling waits.
- Regex injection detection is a backstop. The structural defences are the policy floor and code-only approvals.
- Overlap judgement depends on catalog names, categories and notes. There's no seat-utilisation data, so "unused capacity" can't be checked.
- The human decision log is a local JSONL file with no authentication. It's an MVP stand-in for a real approval system.
