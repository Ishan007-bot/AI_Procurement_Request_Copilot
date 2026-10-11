"""Architecture comparison: same cases, same model, A (single agent) vs B (staged).

    python evals/run_comparison.py                      # core suite (16 cases), both architectures, Groq
    python evals/run_comparison.py --suite full         # all 23 cases
    python evals/run_comparison.py --report-only        # rebuild reports from saved runs
    python evals/run_comparison.py --max-runs 4         # stop after 4 new runs (quota budgeting)
    python evals/run_comparison.py --models openai/gpt-oss-20b,qwen/qwen3.8-27b
                                                        # spread cases over models (paired by case)

Built for free-tier quotas:
  * every finished run is appended to evals/results/runs.jsonl immediately;
  * re-running skips runs already recorded for the same model, so an interrupted
    evaluation resumes where it stopped;
  * a daily-quota error STOPS the evaluation. It is never recorded as a result
    (a rules-only fallback caused by quota would be measuring the quota, not the architecture);
  * cases run as A/B pairs, so partial results stay comparable;
  * with --models, case i is assigned to models[i % n]. A and B of a case always use the same
    model (a paired design blocked by model), so per-case differences are model-controlled and both
    architectures see an identical model mix. A model whose daily quota runs out is parked; the rest continue.

Cases run against evals/fixtures/data (the shipped data plus synthetic edge cases),
served through the normal interfaces: PROCUREMENT_DATA_DIR and a mock vendor-risk API.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE_DIR = ROOT / "evals" / "fixtures" / "data"
RESULTS_DIR = ROOT / "evals" / "results"
RUNS_PATH = RESULTS_DIR / "runs.jsonl"
CASES_PATH = ROOT / "evals" / "extended_cases.json"
ARCH_NAMES = {"single": "A · single agent", "staged": "B · staged (2 agents)", "rules_only": "Rules-only baseline (no LLM)"}
QUALITY = ["correct_next_action", "grounded_evidence", "policy_followed", "human_escalation_correct"]


def configure_environment(provider: str, model: str | None) -> None:
    if not FIXTURE_DIR.exists():
        import evals.build_fixtures as build
        build.main()
    os.environ["PROCUREMENT_DATA_DIR"] = str(FIXTURE_DIR)
    os.environ["LLM_PROVIDER"] = provider
    if model:
        os.environ["MODEL_NAME"] = model
    from src.local_services import start_vendor_api_in_background
    # Always a dedicated in-process API: one started by run_local.py would serve the default data directory.
    os.environ["VENDOR_RISK_BASE_URL"] = start_vendor_api_in_background()


def load_cases(suite: str) -> list[dict]:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    return cases if suite == "full" else [c for c in cases if c["suite"] == "core"]


def load_runs() -> list[dict]:
    if not RUNS_PATH.exists():
        return []
    return [json.loads(line) for line in RUNS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def record(run: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RUNS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(scrub(run), default=str, ensure_ascii=False) + "\n")


def is_capacity_failure(reason: str) -> bool:
    """Daily quota or persistent rate limiting: a property of the free tier, not of the architecture."""
    return "LLMQuotaExhausted" in reason or "rate limit" in reason.lower() or "Error code: 429" in reason


def scrub(value):
    """Remove account identifiers (e.g. Groq organisation IDs in error messages) before saving."""
    if isinstance(value, str):
        return re.sub(r"org_[A-Za-z0-9]+", "org_[redacted]", value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def assign_models(cases: list[dict], models: list[str]) -> dict[str, str]:
    return {c["case_id"]: models[i % len(models)] for i, c in enumerate(cases)}


def execute(case: dict, architecture: str, model_id: str, repeat: int) -> dict | None:
    from evals.scoring import override_counts, score_decision, score_draft
    from src.data_access import get_request
    from src.solution import run_copilot

    request = get_request(case["request_id"])
    os.environ["MODEL_NAME"] = model_id.split(":", 1)[1]
    started = time.perf_counter()
    run = run_copilot(request, architecture)
    wall_ms = (time.perf_counter() - started) * 1000
    if run.fallback_reason and is_capacity_failure(run.fallback_reason):
        return None  # provider capacity, not architecture behaviour: park the model, record nothing

    decision = run.decision.model_dump()
    tool_results = [r.to_dict() for r in run.tool_results]
    floor = {"required_approvals": run.policy.required_approvals, "risk_flags": run.policy.risk_flags}
    t = run.telemetry
    stages = {k: v for k, v in run.stages.items() if k != "evidence_pack"}
    return {
        "case_id": case["case_id"], "request_id": case["request_id"], "suite": case["suite"],
        "architecture": architecture, "model": model_id, "repeat": repeat,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **score_decision(decision, case["expect"], tool_results, request),
        **score_draft(run.draft.model_dump() if run.draft else None, case["expect"], floor),
        "fallback": run.fallback_reason is not None, "fallback_reason": run.fallback_reason,
        "recovered_from_text": any(isinstance(v, dict) and v.get("recovered_from_text") for v in stages.values()),
        "llm_calls": t["llm_calls"], "tool_calls": t["tool_calls"], "tool_names": t["tool_names"],
        "input_tokens": t["input_tokens"], "output_tokens": t["output_tokens"],
        "active_latency_ms": t["active_latency_ms"], "wall_ms": round(wall_ms, 1), "throttle_wait_ms": t["throttle_wait_ms"],
        "guardrail_overrides": t["guardrail_overrides"], "override_counts": override_counts(t["guardrail_overrides"]),
        "errors": t["errors"], "stages": stages, "decision": decision,
        "draft": run.draft.model_dump() if run.draft else None,
    }


def rules_only_rows(cases: list[dict]) -> list[dict]:
    """The deterministic engine alone (0 LLM calls): a reference point, not an architecture under test."""
    from evals.scoring import score_decision
    from src.data_access import get_request
    from src.guardrails import fallback_decision
    from src.telemetry import RunTelemetryCounter
    from src.tools import ToolRunner

    rows = []
    for case in cases:
        request = get_request(case["request_id"])
        runner = ToolRunner(request)
        started = time.perf_counter()
        policy = runner.policy_result()
        decision = fallback_decision(policy, RunTelemetryCounter(), "baseline").model_dump()
        decision["risk_flags"] = [f for f in decision["risk_flags"] if f != "llm_unavailable"]
        scores = score_decision(decision, case["expect"], [], request)
        scores["grounded_evidence"] = True  # every item is an engine finding over the data snapshot
        scores["problems"] = [p for p in scores["problems"] if not p.startswith(("evidence source", "figure"))]
        rows.append({"case_id": case["case_id"], "architecture": "rules_only", **scores,
                     "llm_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0,
                     "active_latency_ms": round((time.perf_counter() - started) * 1000, 1), "guardrail_overrides": [],
                     "fallback": False, "recovered_from_text": False})
    return rows


# ------------------------------------------------------------------------------------------- reporting

def _mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 1) if values else 0.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))], 1)


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    pct = lambda key: f"{sum(1 for r in rows if r.get(key)) }/{n}"
    summary = {
        "runs": n,
        **{key: pct(key) for key in QUALITY},
        "all_four": f"{sum(1 for r in rows if all(r.get(k) for k in QUALITY))}/{n}",
        "raw_correct_next_action": pct("raw_correct_next_action") if rows and "raw_correct_next_action" in rows[0] else "n/a",
        "raw_policy_followed": pct("raw_policy_followed") if rows and "raw_policy_followed" in rows[0] else "n/a",
        "raw_floor_coverage": round(statistics.fmean([r["raw_floor_coverage"] for r in rows]), 3)
        if rows and "raw_floor_coverage" in rows[0] else "n/a",
        "avg_active_latency_ms": _mean([r["active_latency_ms"] for r in rows]),
        "median_active_latency_ms": round(statistics.median([r["active_latency_ms"] for r in rows]), 1) if rows else 0.0,
        "p95_active_latency_ms": _p95([r["active_latency_ms"] for r in rows]),
        "avg_wall_ms": _mean([r.get("wall_ms", r["active_latency_ms"]) for r in rows]),
        "avg_llm_calls": _mean([r["llm_calls"] for r in rows]),
        "avg_tool_calls": _mean([r["tool_calls"] for r in rows]),
        "avg_input_tokens": _mean([r["input_tokens"] for r in rows]),
        "avg_output_tokens": _mean([r["output_tokens"] for r in rows]),
        "avg_guardrail_overrides": _mean([len(r["guardrail_overrides"]) for r in rows]),
        "fallbacks": sum(1 for r in rows if r.get("fallback")),
        "recovered_from_text": sum(1 for r in rows if r.get("recovered_from_text")),
    }
    categories: dict[str, int] = {}
    for r in rows:
        for cat, count in r.get("override_counts", {}).items():
            categories[cat] = categories.get(cat, 0) + count
    summary["override_categories"] = categories
    return summary


def rescore(run: dict, case: dict) -> dict:
    """Re-apply the CURRENT gold to a saved run, so gold revisions apply to every run identically.
    Grounding is kept as recorded: it needs that run's tool outputs, which were checked at run time."""
    from evals.scoring import escalation_check, policy_check
    from src.guardrails import match_label, normalize_approval, normalize_flag

    expect, d = case["expect"], run["decision"]
    label = match_label(d["recommendation"])
    approvals = set(d["required_approvals"])
    policy = policy_check(approvals, {normalize_flag(f) for f in d["risk_flags"]}, d["missing_information"], expect)
    escalation = escalation_check(label, approvals, bool(d["human_review_required"]), expect)
    grounding = [p for p in run["problems"] if p.startswith(("evidence source", "figure", "fewer than"))]
    updated = dict(run, label=label, correct_next_action=label in expect["labels"], policy_followed=not policy,
                   human_escalation_correct=not escalation,
                   problems=([f"label {label} not in {expect['labels']}"] if label not in expect["labels"] else [])
                   + grounding + policy + escalation)
    if run.get("draft"):
        draft = run["draft"]
        raw_label = match_label(draft["recommendation"])
        raw_policy = policy_check({normalize_approval(a) for a in draft["required_approvals"]} - {None},
                                  {normalize_flag(f) for f in draft["risk_flags"]}, draft["missing_information"], expect)
        updated.update(raw_label=raw_label, raw_correct_next_action=raw_label in expect["labels"],
                       raw_policy_followed=not raw_policy, raw_problems=raw_policy)
    return updated


def replay_guardrails(run: dict, case: dict) -> dict | None:
    """Re-apply the CURRENT guardrail code to a saved draft (no LLM calls). The policy floor is recomputed
    deterministically from the fixture data, so guardrail fixes can be measured on real model outputs."""
    from src.contracts import DraftDecision
    from src.data_access import get_request
    from src.guardrails import apply_guardrails
    from src.telemetry import RunTelemetryCounter
    from src.tools import ToolRunner

    if not run.get("draft"):
        return None
    request = get_request(case["request_id"])
    policy = ToolRunner(request).policy_result()
    failed = {e.split(":", 1)[0].removeprefix("tool ").strip() for e in run.get("errors", []) if e.startswith("tool ")}
    decision = apply_guardrails(DraftDecision.model_validate(run["draft"]), policy, request,
                                set(run["tool_names"]) - failed, RunTelemetryCounter())
    return rescore(dict(run, decision=decision.model_dump()), case)


def write_reports(cases: list[dict], assignment: dict[str, str], suite: str) -> None:
    case_ids = [c["case_id"] for c in cases]
    by_id = {c["case_id"]: c for c in cases}
    runs = [rescore(r, by_id[r["case_id"]]) for r in load_runs()
            if r["case_id"] in case_ids and r["model"] == assignment[r["case_id"]]]
    by_arch = {arch: [r for r in runs if r["architecture"] == arch] for arch in ("single", "staged")}
    baseline = rules_only_rows(cases)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Template-format CSVs (templates/evaluation_results_template.csv)
    for arch, rows in by_arch.items():
        with (RESULTS_DIR / f"results_{arch}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["case_id", "architecture", "correct_next_action", "grounded_evidence", "policy_followed",
                        "human_escalation_correct", "latency_ms", "llm_calls", "tool_calls", "notes"])
            for r in sorted(rows, key=lambda r: case_ids.index(r["case_id"])):
                notes = "; ".join(r["problems"] + [f"raw draft: {p}" for p in r.get("raw_problems", [])][:3]
                                  + ([f"fallback: {r['fallback_reason'][:80]}"] if r["fallback"] else []))
                w.writerow([r["case_id"], arch, r["correct_next_action"], r["grounded_evidence"], r["policy_followed"],
                            r["human_escalation_correct"], r["active_latency_ms"], r["llm_calls"], r["tool_calls"], notes])

    # Paired per-case comparison
    with (RESULTS_DIR / "comparison.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "title", "gold_labels", "A_label", "B_label", "rules_only_label", "A_raw_label", "B_raw_label",
                    "A_all_four", "B_all_four", "A_overrides", "B_overrides", "A_llm_calls", "B_llm_calls",
                    "A_tokens_in", "B_tokens_in", "A_active_ms", "B_active_ms"])
        for case in cases:
            a = next((r for r in by_arch["single"] if r["case_id"] == case["case_id"]), None)
            b = next((r for r in by_arch["staged"] if r["case_id"] == case["case_id"]), None)
            base = next(r for r in baseline if r["case_id"] == case["case_id"])
            g = lambda r, k: (r or {}).get(k, "")
            four = lambda r: all(r.get(k) for k in QUALITY) if r else ""
            w.writerow([case["case_id"], case["title"], " | ".join(case["expect"]["labels"]), g(a, "label"), g(b, "label"),
                        base["label"], g(a, "raw_label"), g(b, "raw_label"), four(a), four(b),
                        len(g(a, "guardrail_overrides") or []), len(g(b, "guardrail_overrides") or []),
                        g(a, "llm_calls"), g(b, "llm_calls"), g(a, "input_tokens"), g(b, "input_tokens"),
                        g(a, "active_latency_ms"), g(b, "active_latency_ms")])

    summaries = {"single": summarize(by_arch["single"]), "staged": summarize(by_arch["staged"]),
                 "rules_only": summarize(baseline)}
    by_id = {c["case_id"]: c for c in cases}
    for arch in ("single", "staged"):
        replayed = [x for x in (replay_guardrails(r, by_id[r["case_id"]]) for r in by_arch[arch]) if x is not None]
        n = len(by_arch[arch])
        summaries[arch]["replay_all_four"] = f"{sum(1 for x in replayed if all(x.get(k) for k in QUALITY))}/{n}"
        summaries[arch]["replay_correct_next_action"] = f"{sum(1 for x in replayed if x['correct_next_action'])}/{n}"
    per_model = {}
    for model in sorted(set(assignment.values())):
        per_model[model] = {arch: summarize([r for r in rows if r["model"] == model]) for arch, rows in by_arch.items()}
    (RESULTS_DIR / "summary.json").write_text(json.dumps({"assignment": assignment, "suite": suite, "cases": len(cases),
                                                          "summaries": summaries, "per_model": per_model}, indent=2),
                                              encoding="utf-8")
    write_markdown(cases, assignment, suite, summaries, by_arch, baseline, per_model)


def write_markdown(cases, assignment, suite, summaries, by_arch, baseline, per_model) -> None:
    model_list = ", ".join(f"`{m}`" for m in sorted(set(assignment.values())))
    s, b, r = summaries["single"], summaries["staged"], summaries["rules_only"]
    paired = [c["case_id"] for c in cases
              if any(x["case_id"] == c["case_id"] for x in by_arch["single"])
              and any(x["case_id"] == c["case_id"] for x in by_arch["staged"])]
    rows = [
        ("Runs completed", s["runs"], b["runs"], r["runs"]),
        ("**All four quality checks pass**", s["all_four"], b["all_four"], r["all_four"]),
        ("Correct next action", s["correct_next_action"], b["correct_next_action"], r["correct_next_action"]),
        ("Evidence grounded in tool output", s["grounded_evidence"], b["grounded_evidence"], r["grounded_evidence"]),
        ("Policy + deterministic rules followed", s["policy_followed"], b["policy_followed"], r["policy_followed"]),
        ("Escalation / human review correct", s["human_escalation_correct"], b["human_escalation_correct"], r["human_escalation_correct"]),
        ("All four, current guardrail code replayed on the saved drafts", s.get("replay_all_four", "-"),
         b.get("replay_all_four", "-"), "-"),
        ("Correct next action, replayed guardrails", s.get("replay_correct_next_action", "-"),
         b.get("replay_correct_next_action", "-"), "-"),
        ("Raw draft: correct next action (before guardrails)", s["raw_correct_next_action"], b["raw_correct_next_action"], "n/a"),
        ("Raw draft: policy followed (before guardrails)", s["raw_policy_followed"], b["raw_policy_followed"], "n/a"),
        ("Raw draft: share of policy floor included", s["raw_floor_coverage"], b["raw_floor_coverage"], "n/a"),
        ("Avg guardrail corrections per run", s["avg_guardrail_overrides"], b["avg_guardrail_overrides"], 0),
        ("Median active latency (ms, excl. free-tier waits)", s["median_active_latency_ms"], b["median_active_latency_ms"],
         r["median_active_latency_ms"]),
        ("Avg active latency (ms; sensitive to one network stall)", s["avg_active_latency_ms"], b["avg_active_latency_ms"], r["avg_active_latency_ms"]),
        ("p95 active latency (ms)", s["p95_active_latency_ms"], b["p95_active_latency_ms"], r["p95_active_latency_ms"]),
        ("Avg wall-clock incl. throttling (ms)", s["avg_wall_ms"], b["avg_wall_ms"], "-"),
        ("Avg LLM calls", s["avg_llm_calls"], b["avg_llm_calls"], 0),
        ("Avg tool calls", s["avg_tool_calls"], b["avg_tool_calls"], 0),
        ("Avg input / output tokens", f"{s['avg_input_tokens']:.0f} / {s['avg_output_tokens']:.0f}",
         f"{b['avg_input_tokens']:.0f} / {b['avg_output_tokens']:.0f}", "0 / 0"),
        ("Rules-only fallbacks (agent failed)", s["fallbacks"], b["fallbacks"], "-"),
        ("Final answer recovered from text", s["recovered_from_text"], b["recovered_from_text"], "-"),
    ]
    lines = [
        "# Architecture comparison results",
        "",
        f"- Models: {model_list}; each case's A and B use the same model (paired design, model as block), "
        "temperature 0, reasoning effort `low`",
        f"- Suite: `{suite}` ({len(cases)} cases); paired A/B results for {len(paired)} case(s)",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `python evals/run_comparison.py --report-only`",
        "",
        "| Metric | A · single agent | B · staged (2 agents) | Rules-only baseline |",
        "|---|---:|---:|---:|",
        *[f"| {name} | {va} | {vb} | {vr} |" for name, va, vb, vr in rows],
        "",
        f"Guardrail corrections by type: A {s['override_categories'] or '{}'} · B {b['override_categories'] or '{}'}",
        "",
        "## Per case",
        "",
        "| Case | Model | Gold | A | B | Rules-only | A raw | B raw |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        a = next((x for x in by_arch["single"] if x["case_id"] == case["case_id"]), None)
        bb = next((x for x in by_arch["staged"] if x["case_id"] == case["case_id"]), None)
        base = next(x for x in baseline if x["case_id"] == case["case_id"])
        mark = lambda x: "—" if x is None else (("✅ " if all(x.get(k) for k in QUALITY) else "❌ ") + str(x["label"]))
        raw = lambda x: "—" if x is None else str(x.get("raw_label"))
        model_short = assignment[case["case_id"]].split("/")[-1]
        lines.append(f"| {case['case_id']} {case['title']} | {model_short} | "
                     f"{' / '.join(case['expect']['labels'])} | {mark(a)} | {mark(bb)} | "
                     f"{mark(base)} | {raw(a)} | {raw(bb)} |")
    lines += ["", "## By model", "",
              "| Model | Runs A/B | All four A | All four B | Raw label A | Raw label B | Avg tokens A | Avg tokens B |",
              "|---|---|---|---|---|---|---|---|"]
    for model, sums in per_model.items():
        sa, sb = sums["single"], sums["staged"]
        lines.append(f"| `{model}` | {sa['runs']}/{sb['runs']} | {sa['all_four']} | {sb['all_four']} | "
                     f"{sa['raw_correct_next_action']} | {sb['raw_correct_next_action']} | "
                     f"{sa['avg_input_tokens']:.0f} | {sb['avg_input_tokens']:.0f} |")
    problems = [(x["case_id"], x["architecture"], p) for x in by_arch["single"] + by_arch["staged"] for p in x["problems"]]
    lines += ["", "## Failed checks (final decisions)", ""]
    lines += [f"- {cid} · {arch}: {p}" for cid, arch, p in problems] or ["- none"]
    (RESULTS_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", choices=["core", "full"], default="core")
    parser.add_argument("--architectures", default="single,staged")
    parser.add_argument("--provider", default="groq")
    parser.add_argument("--model", default=None, help="defaults to the provider's default model")
    parser.add_argument("--models", default=None,
                        help="comma-separated models; case i uses models[i %% n] for both A and B")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-runs", type=int, default=None, help="stop after this many new runs")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    configure_environment(args.provider, args.model)
    from src import config
    names = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else [config.get_model_name()]
    models = [f"{config.get_llm_provider()}:{m}" for m in names]
    cases = load_cases(args.suite)
    assignment = assign_models(cases, models)
    architectures = [a.strip() for a in args.architectures.split(",") if a.strip()]

    if not args.report_only:
        if not config.get_llm_api_key():
            sys.exit(f"No API key for {args.provider}: set {config.get_provider_settings()['api_key_env']} (see .env.example)")
        done = {(r["case_id"], r["architecture"], r["repeat"], r["model"]) for r in load_runs()}
        todo = [(c, a, rep) for rep in range(args.repeats) for c in cases for a in architectures
                if (c["case_id"], a, rep, assignment[c["case_id"]]) not in done]
        print(f"Models {', '.join(models)} · suite {args.suite} · {len(todo)} run(s) to do")
        print(f"Estimated input tokens: ~{len(todo) * 11000:,} (free tier: ~8k tokens/min, daily cap applies)\n")
        new_runs = 0
        parked: set[str] = set()
        for case, arch, rep in todo:
            model_id = assignment[case["case_id"]]
            if model_id in parked:
                continue
            if args.max_runs is not None and new_runs >= args.max_runs:
                print(f"Stopping after --max-runs {args.max_runs}.")
                break
            label = f"{case['case_id']:8} {arch:6} r{rep} {model_id.split('/')[-1]:12}"
            print(f"{label} ...", end=" ", flush=True)
            result = execute(case, arch, model_id, rep)
            if result is None:
                parked.add(model_id)
                print(f"CAPACITY LIMIT (daily quota / rate limit) for {model_id}: parked, nothing recorded; "
                      "re-run later to resume.")
                continue
            record(result)
            new_runs += 1
            status = "PASS" if all(result[k] for k in QUALITY) else "FAIL"
            print(f"{status} label={result['label']} raw={result['raw_label']} llm={result['llm_calls']} "
                  f"tok={result['input_tokens']} active={result['active_latency_ms'] / 1000:.1f}s "
                  f"wall={result['wall_ms'] / 1000:.0f}s overrides={len(result['guardrail_overrides'])}"
                  + (" FALLBACK" if result["fallback"] else ""))
            if result["problems"]:
                print("         - " + "\n         - ".join(result["problems"][:4]))

    write_reports(cases, assignment, args.suite)
    print(f"\nReports: {(RESULTS_DIR / 'summary.md').relative_to(ROOT)}, comparison.csv, results_single.csv, results_staged.csv")


if __name__ == "__main__":
    main()
