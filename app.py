"""Procurement Request Copilot — Streamlit product UI.

Request -> evidence -> recommendation -> human decision. The copilot only
recommends; the decision buttons at the bottom are the human step and are
recorded in logs/decisions.jsonl.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime

import streamlit as st

from src import config
from src import ui_components as ui
from src.data_access import load_employees, load_requests, resolve_approvers, to_number
from src.decision_log import HUMAN_ACTIONS, append_decision, read_decisions
from src.guardrails import GUARDRAIL_SOURCE, match_label
from src.injection import scan_fields
from src.local_services import ensure_vendor_api
from src.solution import CopilotRun, run_copilot

st.set_page_config(page_title="Procurement Copilot", page_icon="🧾", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)

ARCHITECTURES = {"Single agent (A)": "single", "Staged / 2-agent (B)": "staged", "Compare A vs B": "compare"}
DEAD_VENDOR_API = "http://127.0.0.1:9"


@st.cache_resource(show_spinner=False)
def vendor_api_base() -> str:
    """Use the API started by run_local.py, or start one in-process so the app also works standalone."""
    return ensure_vendor_api()


@contextmanager
def environment(**overrides: str):
    """Temporarily override environment variables for one run (provider choice, simulated outage)."""
    previous = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def split_recommendation(text: str) -> tuple[str | None, str]:
    label = match_label(text)
    rationale = text[len(label):].lstrip(" :—-") if label else text
    return label, rationale


# ----------------------------------------------------------------------------- state & sidebar

if "runs" not in st.session_state:
    st.session_state.runs = {}
if "custom_request" not in st.session_state:
    st.session_state.custom_request = None

api_base = vendor_api_base()
requests = load_requests()
employees = load_employees().to_dict(orient="records")

with st.sidebar:
    st.markdown("### 🧾 Procurement Copilot")
    source = st.segmented_control("Request source", ["Queue", "New request"], default="Queue") or "Queue"
    if source == "New request":
        request = st.session_state.custom_request
        if request is None:
            st.caption("Fill in the form on the right, then run the analysis.")
    else:
        by_id = {r["request_id"]: r for r in requests}
        linked = st.query_params.get("request")  # deep link: ?request=REQ-1007
        request_id = st.selectbox(
            "Request",
            list(by_id),
            index=list(by_id).index(linked) if linked in by_id else 0,
            format_func=lambda rid: f"{rid} · {by_id[rid]['product_name']} · "
                                    f"{ui.money(by_id[rid]['annual_cost_usd']) if by_id[rid]['annual_cost_usd'] is not None else 'cost ?'}",
        )
        request = by_id[request_id]

    st.divider()
    arch_label = st.radio("Architecture", list(ARCHITECTURES), index=0,
                          help="A: one agent with tools. B: analyst agent builds an evidence pack, reviewer agent decides.")
    architecture = ARCHITECTURES[arch_label]
    providers = list(config.PROVIDERS)
    provider = st.selectbox("Model provider", providers, index=providers.index(config.get_llm_provider()),
                            help="Both are called through their OpenAI-compatible endpoints (free tier).")
    with environment(LLM_PROVIDER=provider):
        model_name = config.get_model_name()
        has_key = bool(config.get_llm_api_key())
    st.caption(f"Model: `{model_name}`" + ("" if has_key else " · ⚠️ no API key: rules-only results"))
    simulate_outage = st.toggle("Simulate vendor-risk outage", value=False,
                                help="Points the vendor-risk client at a dead address for this run, to show failure handling.")
    st.divider()
    st.caption(f"Policy reference date: **{config.get_reference_date().isoformat()}**")
    st.caption(f"Vendor-risk API: `{api_base}`")
    if st.button("Clear cached results", width="stretch"):
        st.session_state.runs = {}
        st.rerun()

# ----------------------------------------------------------------------------- header

st.markdown(
    '<div class="pc-header"><div><div class="pc-title">AI Procurement Request Copilot</div>'
    '<div class="pc-sub">Gathers evidence, applies policy checks, and recommends the next action. '
    'Humans make every approval.</div></div><div>'
    + ui.badge(arch_label, "blue") + ui.badge(f"{provider} · {model_name}", "slate")
    + (ui.badge("Vendor outage simulated", "red", "⚠") if simulate_outage else "")
    + "</div></div>",
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------- new request form

if source == "New request":
    with st.expander("New purchase request", expanded=request is None):
        with st.form("new_request"):
            c1, c2, c3 = st.columns(3)
            emp_ids = [e["employee_id"] for e in employees]
            requester = c1.selectbox("Requester", emp_ids,
                                     format_func=lambda eid: next(f"{e['name']} ({e['department']})" for e in employees if e["employee_id"] == eid))
            product = c2.text_input("Product name", placeholder="e.g. TaskFlow Pro")
            vendor = c3.text_input("Vendor name", placeholder="e.g. TaskFlow")
            c4, c5, c6 = st.columns(3)
            category = c4.text_input("Category", placeholder="e.g. Project Management")
            cost_text = c5.text_input("Annual cost (USD)", placeholder="leave blank if unknown")
            users_text = c6.text_input("Number of users", placeholder="leave blank if unknown")
            c7, c8, c9 = st.columns(3)
            data_level = c7.selectbox("Data access level", ui.DATA_ACCESS_LEVELS, index=1)
            integrations = c8.text_input("Integrations (comma-separated)", placeholder="e.g. SSO, CRM")
            urgency = c9.selectbox("Urgency", ["normal", "high", "urgent"])
            justification = st.text_area("Business justification", height=90)
            if st.form_submit_button("Save request", type="primary"):
                st.session_state.custom_request = {
                    "request_id": f"NEW-{datetime.now().strftime('%H%M%S')}",
                    "requester_id": requester,
                    "product_name": product.strip(),
                    "vendor_name": vendor.strip(),
                    "category": category.strip(),
                    "annual_cost_usd": to_number(cost_text),
                    "user_count": int(to_number(users_text)) if to_number(users_text) else None,
                    "business_justification": justification.strip(),
                    "data_access_level": data_level,
                    "requested_integrations": [i.strip() for i in integrations.split(",") if i.strip()],
                    "urgency": urgency,
                }
                st.rerun()
    if request is None:
        st.stop()

# ----------------------------------------------------------------------------- request card

approvers = resolve_approvers(str(request.get("requester_id") or ""))
requester_row = approvers.get("requester") or {}
cost = to_number(request.get("annual_cost_usd"))
users = to_number(request.get("user_count"))
integrations = request.get("requested_integrations")
data_level = request.get("data_access_level")

col_request, col_result = st.columns([0.95, 1.05], gap="large")

with col_request:
    fields = "".join([
        ui.field("Requester", f"{requester_row.get('name', request.get('requester_id'))} · {requester_row.get('level', '?')}"
                 if requester_row else request.get("requester_id"), missing=not requester_row),
        ui.field("Department", requester_row.get("department", "Unknown"), missing=not requester_row),
        ui.field("Vendor", request.get("vendor_name"), missing=not request.get("vendor_name")),
        ui.field("Category", request.get("category") or "—"),
        ui.field("Annual cost", ui.money(cost), missing=cost is None),
        ui.field("Users / licences", int(users) if users else "", missing=not users),
        ui.field("Data access", data_level, missing=str(data_level or "unknown").lower() in {"", "unknown"}),
        ui.field("Integrations", ", ".join(integrations) if integrations else "None",
                 missing=integrations is None),
        ui.field("Urgency", request.get("urgency") or "—"),
    ])
    run_key_base = json.dumps(request, sort_keys=True, default=str)
    injected = bool(scan_fields(("request", f"request.{k}", v) for k, v in request.items()))
    quote_class = "pc-quote warn" if injected else "pc-quote"
    quote_tag = "Requester text · untrusted · contains embedded instructions (ignored)" if injected else "Business justification · requester text"
    st.markdown(
        ui.card(f"{request.get('request_id')} · {request.get('product_name') or 'Unnamed product'}",
                f'<div class="pc-grid">{fields}</div>'
                f'<div class="{quote_class}"><span class="tag">{ui.esc(quote_tag)}</span>'
                f'{ui.esc(request.get("business_justification") or "No justification provided.")}</div>'),
        unsafe_allow_html=True,
    )
    named = [f"Manager: {approvers['manager']['name']}" if approvers.get("manager") else None,
             f"Department head: {approvers['department_head']['name']}" if approvers.get("department_head") else None]
    st.caption(" · ".join(n for n in named if n) or "No reporting line on record")


# ----------------------------------------------------------------------------- running

def execute(arch: str, status) -> CopilotRun:
    def on_tool(result) -> None:
        mark = "✅" if result.ok else "⚠️"
        status.write(f"{mark} `{result.name}` · {result.latency_ms:.0f} ms")
    overrides = {"LLM_PROVIDER": provider}
    if simulate_outage:
        overrides["VENDOR_RISK_BASE_URL"] = DEAD_VENDOR_API
    with environment(**overrides):
        return run_copilot(request, arch, on_tool_call=on_tool)


def run_and_store(arch: str) -> CopilotRun | None:
    key = (run_key_base, arch, provider, simulate_outage)
    label = "Architecture A" if arch == "single" else "Architecture B"
    with st.status(f"{label}: gathering evidence…", expanded=True) as status:
        started = time.perf_counter()
        try:
            run = execute(arch, status)
        except NotImplementedError as exc:
            status.update(label=f"{label} is not available yet", state="error")
            st.info(str(exc))
            return None
        seconds = time.perf_counter() - started
        note = " (rules-only fallback)" if run.fallback_reason else ""
        status.update(label=f"{label} finished in {seconds:.1f}s{note}", state="complete", expanded=False)
    st.session_state.runs[key] = run
    return run


def stored(arch: str) -> CopilotRun | None:
    return st.session_state.runs.get((run_key_base, arch, provider, simulate_outage))


def render_recommendation(run: CopilotRun) -> None:
    d = run.decision
    label, rationale = split_recommendation(d.recommendation)
    overrides = run.telemetry["guardrail_overrides"]
    badges = ui.badge("Human review required", "red", "👤")
    badges += ui.badge("Rules-only result", "violet", "⚙") if run.fallback_reason else ui.badge(run.model or "model", "slate", "✦")
    if overrides:
        badges += ui.badge(f"{len(overrides)} guardrail correction{'s' if len(overrides) != 1 else ''}", "amber", "🛡")
    if run.fallback_reason:
        st.markdown(ui.banner("The AI assessment was unavailable, so this is the conservative rules-only result. "
                              f"Reason: {run.fallback_reason[:220]}", "violet"), unsafe_allow_html=True)
    st.markdown(ui.recommendation_card(label, rationale, d.next_step, badges), unsafe_allow_html=True)
    if run.draft and run.draft.existing_tool_assessment.strip():
        st.markdown(ui.card("Existing-tool assessment (AI)", f'<div class="why">{ui.esc(run.draft.existing_tool_assessment)}</div>'),
                    unsafe_allow_html=True)


def render_details(run: CopilotRun) -> None:
    d, policy = run.decision, run.policy
    tab_approvals, tab_evidence, tab_checks, tab_trace, tab_json = st.tabs(
        ["Approvals & risks", f"Evidence ({len(d.evidence)})", "Policy checks", "Agent trace", "Output JSON"])

    with tab_approvals:
        c1, c2, c3 = st.columns([1.1, 1, 0.9], gap="medium")
        c1.markdown(ui.card(f"Required approvals ({len(d.required_approvals)})",
                            ui.approval_rows(d.required_approvals, policy.approval_reasons, policy.approver_assignments)),
                    unsafe_allow_html=True)
        c2.markdown(ui.card(f"Risk flags ({len(d.risk_flags)})", ui.flag_rows(d.risk_flags)), unsafe_allow_html=True)
        c3.markdown(ui.card("Missing information", ui.missing_rows(d.missing_information)), unsafe_allow_html=True)

    with tab_evidence:
        st.caption(f"Each finding cites the tool that produced it. `{GUARDRAIL_SOURCE}` = added from the deterministic "
                   "policy engine because the agent didn't cite that source.")
        st.dataframe(
            [{"Source": e.source, "Finding": e.finding, "Reference": e.reference or ""} for e in d.evidence],
            width="stretch", hide_index=True,
            column_config={"Finding": st.column_config.TextColumn(width="large")},
        )

    with tab_checks:
        budget = policy.budget_check
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Requested (annual)", ui.money(policy.annual_cost_usd))
        b2.metric("Available budget", ui.money(budget.get("available_usd")))
        remaining = budget.get("remaining_after_usd")
        b3.metric("Remaining after", ui.money(remaining) if remaining is not None else "—",
                  delta=None if remaining is None else ("within budget" if remaining >= 0 else "over budget"),
                  delta_color="normal" if (remaining or 0) >= 0 else "inverse")
        b4.metric("Approval tier", ui.md_safe(ui.TIER_SHORT.get(policy.threshold_tier, policy.threshold_tier or "Unknown")),
                  help=f"Policy section 4 tier: {policy.threshold_tier or 'cannot be computed without an annual cost'}")
        if policy.annual_cost_usd is not None and budget.get("available_usd"):
            st.progress(min(policy.annual_cost_usd / budget["available_usd"], 1.0),
                        text=f"{policy.annual_cost_usd / budget['available_usd']:.0%} of {budget.get('department')}'s available software budget")

        va = policy.vendor_assessment
        reg, api = va.get("registry") or {}, va.get("api") or {}
        rows = [
            ["Security status", reg.get("status_raw", "—") if reg else "Not in registry", api.get("status_raw", "—") if api else f"Unavailable ({va.get('api_status')})"],
            ["Review date", reg.get("review_date") or "—", api.get("review_date") or "—"],
            ["Days since review", reg.get("days_since_review", "—"), api.get("days_since_review", "—")],
            ["Expired (> 365 days)", "Yes" if reg.get("date_expired") else "No", "Yes" if api.get("date_expired") else "No"],
            ["Procurement / risk level", reg.get("procurement_status", "—"), api.get("risk_level", "—")],
            ["Legal terms / data residency", reg.get("legal_terms_status", "—"),
             "Outside region" if api.get("stores_data_outside_region") else ("In region" if api else "—")],
        ]
        conflict_rows = {0, 1} if va.get("conflicts") else set()
        st.markdown(ui.card(f"Vendor evidence · {va.get('vendor_name')}",
                            ui.table(["Check", "Internal registry", "Vendor-risk service"], rows, conflict_rows)
                            + ("".join(ui.banner("Conflict: " + c, "red") for c in va.get("conflicts", [])) if va.get("conflicts") else "")),
                    unsafe_allow_html=True)

        candidates = policy.overlap_candidates
        if candidates:
            st.markdown(ui.card("Existing tools in the catalog", ui.table(
                ["ID", "Product", "Status", "Seats", "Scope", "Match", "Available to requester"],
                [[c["software_id"], c["product_name"], c["status"], int(c["licensed_seats"] or 0), c["scope"],
                  ", ".join(c["match_reasons"]), "Yes" if c["available_to_requester"] else "No"] for c in candidates])),
                unsafe_allow_html=True)
        else:
            st.markdown(ui.card("Existing tools in the catalog", '<div class="pc-empty">No overlapping approved products.</div>'),
                        unsafe_allow_html=True)
        if policy.untrusted_text_findings:
            st.markdown(ui.card("Untrusted content", ui.table(
                ["Type", "Field", "Pattern", "Excerpt"],
                [[f.kind, f.field, f.label, f.excerpt] for f in policy.untrusted_text_findings])), unsafe_allow_html=True)

    with tab_trace:
        t = run.telemetry
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("LLM calls", t["llm_calls"])
        m2.metric("Tool calls", t["tool_calls"])
        m3.metric("Active latency", f"{t['active_latency_ms'] / 1000:.1f}s", help="Wall-clock minus free-tier throttling waits")
        m4.metric("Throttle wait", f"{t['throttle_wait_ms'] / 1000:.1f}s")
        m5.metric("Tokens in / out", f"{t['input_tokens']:,} / {t['output_tokens']:,}")
        stage_summary = {k: v for k, v in run.stages.items() if k != "evidence_pack"}
        if stage_summary:
            st.caption("Stages: " + " · ".join(
                f"{name}: {info.get('llm_calls', info.get('iterations'))} LLM calls"
                + (" (answer recovered from text)" if info.get("recovered_from_text") else "")
                for name, info in stage_summary.items()))
        if "evidence_pack" in run.stages:
            with st.expander("Evidence pack handed from Analyst to Reviewer (architecture B)"):
                st.json(run.stages["evidence_pack"])
        if t["guardrail_overrides"]:
            st.markdown("**Guardrail corrections** (deterministic code adjusting the model's draft)")
            for override in t["guardrail_overrides"]:
                st.markdown(f"- `{override}`")
        st.markdown("**Tool calls**")
        if not run.tool_results:
            st.caption("No agent tool calls: the AI was unavailable, so the policy engine gathered the evidence directly.")
        for i, result in enumerate(run.tool_results, 1):
            with st.expander(f"{i}. {'✅' if result.ok else '⚠️'} {result.name} · {result.latency_ms:.0f} ms"):
                st.caption("Arguments")
                st.json(result.arguments or {})
                st.caption("Result" if result.ok else "Error")
                st.json(result.data if result.ok else {"error": result.error})
        if t["errors"]:
            with st.expander(f"Errors and retries ({len(t['errors'])})"):
                for err in t["errors"]:
                    st.code(err[:600], language=None)

    with tab_json:
        st.caption("The `ProcurementDecision` returned by `handle_request(...)` (evaluation contract).")
        st.json(d.model_dump())


def render_human_panel(run: CopilotRun) -> None:
    st.markdown("#### 👤 Human decision")
    st.caption("The copilot only recommends. The decision below is yours and is recorded in `logs/decisions.jsonl`.")
    label, _ = split_recommendation(run.decision.recommendation)
    with st.form(f"decision_{run.decision.request_id}_{run.architecture}", clear_on_submit=True):
        c1, c2 = st.columns([1, 2])
        reviewer = c1.text_input("Reviewer name")
        action = c1.selectbox("Decision", HUMAN_ACTIONS)
        comment = c2.text_area("Comment", height=108, placeholder="Reasoning, conditions, or questions for the requester")
        if st.form_submit_button("Record decision", type="primary"):
            try:
                append_decision({"request_id": run.decision.request_id, "reviewer": reviewer.strip(), "action": action,
                                 "comment": comment.strip(), "copilot_recommendation": label,
                                 "copilot_architecture": run.architecture, "copilot_model": run.model,
                                 "required_approvals": run.decision.required_approvals})
                st.success("Decision recorded.")
            except ValueError as exc:
                st.error(str(exc))
    history = read_decisions(run.decision.request_id)
    if history:
        st.dataframe([{k: h.get(k) for k in ("timestamp", "reviewer", "action", "comment", "copilot_recommendation")}
                      for h in reversed(history)], width="stretch", hide_index=True)


def render_compare(run_a: CopilotRun, run_b: CopilotRun) -> None:
    ca, cb = st.columns(2, gap="large")
    for col, run, name in ((ca, run_a, "A · Single agent"), (cb, run_b, "B · Staged")):
        with col:
            st.markdown(f"##### {name}")
            render_recommendation(run)
            t = run.telemetry
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("LLM calls", t["llm_calls"])
            m2.metric("Tool calls", t["tool_calls"])
            m3.metric("Active latency", f"{t['active_latency_ms'] / 1000:.1f}s")
            m4.metric("Corrections", len(t["guardrail_overrides"]))
    diffs = []
    for field_name in ("required_approvals", "risk_flags", "missing_information"):
        a, b = set(getattr(run_a.decision, field_name)), set(getattr(run_b.decision, field_name))
        if a - b:
            diffs.append(f"{field_name}: only A has {sorted(a - b)}")
        if b - a:
            diffs.append(f"{field_name}: only B has {sorted(b - a)}")
    if split_recommendation(run_a.decision.recommendation)[0] != split_recommendation(run_b.decision.recommendation)[0]:
        diffs.insert(0, "Recommendation labels differ")
    st.markdown(ui.card("Differences", "".join(f'<div class="pc-missing">• {ui.esc(x)}</div>' for x in diffs)
                        or '<div class="pc-empty">Same recommendation, approvals, flags and missing information.</div>'),
                unsafe_allow_html=True)


# ----------------------------------------------------------------------------- result column

with col_result:
    run_clicked = st.button("Run analysis", type="primary", width="stretch")
    # Demo link: ?request=REQ-1007&run=1 runs once on load.
    if st.query_params.get("run") == "1" and not st.session_state.get("autorun_done"):
        st.session_state.autorun_done = True
        run_clicked = True
    if architecture == "compare":
        if run_clicked:
            run_and_store("single")
            run_and_store("staged")
        run_a, run_b = stored("single"), stored("staged")
        if run_a and not run_b:
            render_recommendation(run_a)
            st.info("Architecture B result unavailable, showing A only.")
    else:
        if run_clicked:
            run_and_store(architecture)
        current = stored(architecture)
        if current:
            render_recommendation(current)
        elif not run_clicked:
            st.markdown(ui.card("Recommendation", '<div class="pc-empty">Run the analysis to gather evidence and get a '
                                                  'recommendation. Typical run: 10–90 s on the free tier.</div>'),
                        unsafe_allow_html=True)

if architecture == "compare":
    run_a, run_b = stored("single"), stored("staged")
    if run_a and run_b:
        render_compare(run_a, run_b)
        st.divider()
        tab_a, tab_b = st.tabs(["Details · A", "Details · B"])
        with tab_a:
            render_details(run_a)
        with tab_b:
            render_details(run_b)
        st.divider()
        render_human_panel(run_a)
    elif run_a:
        render_details(run_a)
        render_human_panel(run_a)
else:
    current = stored(architecture)
    if current:
        render_details(current)
        st.divider()
        render_human_panel(current)

st.markdown('<div class="pc-footer">Recommendations are advisory. The copilot never purchases, approves spend, changes '
            'budgets, accepts vendor terms or overrides Security, Privacy or Legal review. Request and vendor text is '
            'treated as untrusted data.</div>', unsafe_allow_html=True)
