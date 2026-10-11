"""Single-model, multi-trial comparison report: rules-only vs workflow + 1 LLM vs A vs B.

    python evals/report_trials.py --model gemini:gemini-3.5-flash-lite

Reads evals/results/runs.jsonl (written by run_comparison.py), re-scores every run
against the current gold, replays the current guardrails on the saved drafts, and
writes evals/results/summary_trials.md + summary_trials.json.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.run_comparison import (  # noqa: E402
    QUALITY, RESULTS_DIR, configure_environment, load_cases, load_runs, replay_guardrails, rescore, rules_only_rows,
)

ARCHS = [("workflow", "Workflow + 1 LLM"), ("single", "A · single agent"), ("staged", "B · staged")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemini:gemini-3.5-flash-lite")
    parser.add_argument("--suite", default="core")
    args = parser.parse_args()
    provider = args.model.split(":", 1)[0]
    configure_environment(provider, args.model.split(":", 1)[1])

    cases = load_cases(args.suite)
    by_id = {c["case_id"]: c for c in cases}
    runs = [r for r in load_runs() if r["model"] == args.model and r["case_id"] in by_id]
    rows: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        scored = rescore(r, by_id[r["case_id"]])
        replayed = replay_guardrails(r, by_id[r["case_id"]]) or scored
        scored["replay_all_four"] = all(replayed.get(k) for k in QUALITY)
        scored["replay_label"] = replayed["label"]
        rows[r["architecture"]].append(scored)
    baseline = rules_only_rows(cases)

    def per_trial_mean(items: list[dict], key) -> str:
        trials = defaultdict(list)
        for x in items:
            trials[x["repeat"]].append(key(x))
        if not trials:
            return "-"
        complete = [t for t in trials.values() if len(t) == len(cases)]
        means = [sum(t) for t in (complete or trials.values())]
        return f"{statistics.fmean(means):.2f} (n={len(complete or trials)})"

    def consistency(items: list[dict]) -> str:
        labels = defaultdict(set)
        trials = defaultdict(int)
        for x in items:
            labels[x["case_id"]].add(x["label"])
            trials[x["case_id"]] += 1
        multi = [c for c in labels if trials[c] > 1]
        if not multi:
            return "-"
        return f"{sum(1 for c in multi if len(labels[c]) == 1)}/{len(multi)}"

    def avg(items, key, fmt="{:.1f}"):
        return fmt.format(statistics.fmean(key(x) for x in items)) if items else "-"

    def med(items, key):
        return f"{statistics.median(key(x) for x in items) / 1000:.1f} s" if items else "-"

    safety_cases = {"PUB-05", "PUB-06", "EDGE-05", "EDGE-06", "EDGE-13"}  # injection + fault cases

    def safety(items: list[dict]) -> str:
        relevant = [x for x in items if x["case_id"] in safety_cases]
        if not relevant:
            return "-"
        failed = sorted({f"{x['case_id']} t{x['repeat'] + 1}" for x in relevant if not x["replay_all_four"]})
        return "yes" if not failed else "no (" + ", ".join(failed) + ")"

    header = "| Metric | Rules only | " + " | ".join(name for _, name in ARCHS) + " |"
    lines = [
        "# Single-model comparison (3 trials)",
        "",
        f"- Model: `{args.model}` for every architecture · suite `{args.suite}` ({len(cases)} cases) · temperature 0",
        f"- Runs: " + ", ".join(f"{name} {len(rows[a])}" for a, name in ARCHS),
        "- Scores use the current gold. \"Replayed\" applies the final guardrail code to the saved drafts.",
        "",
        header,
        "|---|---:|" + "---:|" * len(ARCHS),
    ]
    base_pass = sum(1 for b in baseline if all(b[k] for k in QUALITY))
    metric_rows = [
        ("**Mean passes per trial, all four checks (replayed)**", f"{base_pass}.00",
         lambda a: per_trial_mean(rows[a], lambda x: x["replay_all_four"])),
        ("Mean passes per trial, as run", f"{base_pass}.00",
         lambda a: per_trial_mean(rows[a], lambda x: all(x.get(k) for k in QUALITY))),
        ("Raw draft chose the right label, mean per trial", "-",
         lambda a: per_trial_mean(rows[a], lambda x: bool(x.get("raw_correct_next_action")))),
        ("Injection + fault cases pass in every trial", "yes", lambda a: safety(rows[a])),
        ("Same decision in every trial (cases)", "-", lambda a: consistency(rows[a])),
        ("Evidence grounded (runs)", "all",
         lambda a: f"{sum(1 for x in rows[a] if x['grounded_evidence'])}/{len(rows[a])}" if rows[a] else "-"),
        ("Avg LLM calls / run", "0", lambda a: avg(rows[a], lambda x: x["llm_calls"])),
        ("Avg tool calls / run", "0", lambda a: avg(rows[a], lambda x: x["tool_calls"])),
        ("Avg input + output tokens / run", "0",
         lambda a: avg(rows[a], lambda x: x["input_tokens"] + x["output_tokens"], "{:,.0f}")),
        ("Median active latency", "< 0.1 s", lambda a: med(rows[a], lambda x: x["active_latency_ms"])),
        ("Avg guardrail corrections / run", "-", lambda a: avg(rows[a], lambda x: len(x["guardrail_overrides"]))),
        ("Rules-only fallbacks (agent failed)", "-", lambda a: str(sum(1 for x in rows[a] if x["fallback"]))),
    ]
    for name, base, fn in metric_rows:
        lines.append(f"| {name} | {base} | " + " | ".join(fn(a) for a, _ in ARCHS) + " |")

    lines += ["", "## Per case (labels across trials; ✅ = all four checks pass after replay in every trial)", "",
              "| Case | Gold | Rules only | " + " | ".join(n for _, n in ARCHS) + " |",
              "|---|---|---|" + "---|" * len(ARCHS)]
    for case in cases:
        base = next(b for b in baseline if b["case_id"] == case["case_id"])
        cells = []
        for a, _ in ARCHS:
            mine = [x for x in rows[a] if x["case_id"] == case["case_id"]]
            if not mine:
                cells.append("—")
                continue
            counts = Counter(x["replay_label"] for x in mine)
            ok = all(x["replay_all_four"] for x in mine)
            cells.append(("✅ " if ok else "❌ ") + ", ".join(f"{lab} ×{n}" if n > 1 else str(lab) for lab, n in counts.items()))
        lines.append(f"| {case['case_id']} {case['title']} | {' / '.join(case['expect']['labels'])} | "
                     f"{'✅' if all(base[k] for k in QUALITY) else '❌'} {base['label']} | " + " | ".join(cells) + " |")
    (RESULTS_DIR / "summary_trials.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (RESULTS_DIR / "summary_trials.json").write_text(json.dumps({a: len(rows[a]) for a, _ in ARCHS}, indent=2), encoding="utf-8")
    print("\n".join(lines[:22]))


if __name__ == "__main__":
    main()
