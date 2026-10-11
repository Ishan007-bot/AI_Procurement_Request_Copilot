"""HTML/CSS building blocks for the Streamlit UI.

Every value rendered through these helpers is HTML-escaped: request and vendor
text is untrusted business data and must never be interpreted as markup.
"""
from __future__ import annotations

import html

from src.policy_engine import (
    BUDGET_EXCEPTION, CLARIFY, DO_NOT_PROCEED, MANUAL_REVIEW, PROCEED, SPECIALIST, USE_EXISTING,
)

CSS = """
<style>
:root {
  --ink: #0F172A; --muted: #64748B; --line: #E2E8F0; --card: #FFFFFF; --soft: #F1F5F9;
  --blue: #2563EB; --blue-bg: #EFF6FF; --green: #15803D; --green-bg: #F0FDF4;
  --amber: #B45309; --amber-bg: #FFFBEB; --red: #B91C1C; --red-bg: #FEF2F2;
  --violet: #6D28D9; --violet-bg: #F5F3FF; --teal: #0F766E; --teal-bg: #F0FDFA;
  --orange: #C2410C; --orange-bg: #FFF7ED; --slate-bg: #F1F5F9;
}
.block-container { padding-top: 3.4rem; padding-bottom: 3rem; max-width: 1400px; }
.pc-header { display: flex; justify-content: space-between; align-items: flex-end; flex-wrap: wrap; gap: .75rem;
  border-bottom: 1px solid var(--line); padding-bottom: .9rem; margin-bottom: 1.1rem; }
.pc-title { font-size: 1.7rem !important; font-weight: 750; color: var(--ink); letter-spacing: -.02em; line-height: 1.2; }
.pc-sub { color: var(--muted); font-size: .92rem; margin-top: .15rem; }
.pc-card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 1.1rem 1.2rem;
  box-shadow: 0 1px 2px rgba(15,23,42,.04); margin-bottom: .9rem; }
.pc-card .pc-h { margin: 0 0 .7rem 0; font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); font-weight: 600; }
.pc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: .65rem .9rem; }
.pc-field .k { font-size: .72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
.pc-field .v { font-size: .95rem; color: var(--ink); font-weight: 600; word-break: break-word; }
.pc-field .v.missing { color: var(--red); }
.pc-quote { background: var(--soft); border-left: 3px solid #CBD5E1; border-radius: 8px; padding: .6rem .8rem;
  color: #334155; font-size: .9rem; margin-top: .8rem; white-space: pre-wrap; }
.pc-quote.warn { border-left-color: var(--red); background: var(--red-bg); }
.pc-quote .tag { display: block; font-size: .7rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
  color: var(--muted); margin-bottom: .25rem; }
.pc-quote.warn .tag { color: var(--red); }
.pc-badge { display: inline-flex; align-items: center; gap: .3rem; padding: .18rem .6rem; border-radius: 999px;
  font-size: .76rem; font-weight: 600; border: 1px solid transparent; white-space: nowrap; margin: 0 .3rem .35rem 0; }
.pc-rec { border-radius: 14px; padding: 1.15rem 1.25rem; border: 1px solid var(--line); margin-bottom: .9rem; }
.pc-rec .label { font-size: 1.3rem; font-weight: 700; margin: .1rem 0 .45rem 0; letter-spacing: -.01em; }
.pc-rec .why { color: #334155; font-size: .95rem; line-height: 1.5; }
.pc-rec .next { margin-top: .8rem; padding-top: .75rem; border-top: 1px dashed rgba(15,23,42,.15); font-size: .92rem; color: var(--ink); }
.pc-rec .next b { display: block; font-size: .72rem; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); margin-bottom: .15rem; }
.pc-approval { display: flex; gap: .7rem; padding: .55rem 0; border-bottom: 1px solid var(--soft); }
.pc-approval:last-child { border-bottom: none; }
.pc-approval .num { flex: 0 0 1.6rem; height: 1.6rem; border-radius: 50%; background: var(--blue-bg); color: var(--blue);
  font-weight: 700; font-size: .8rem; display: flex; align-items: center; justify-content: center; }
.pc-approval .role { font-weight: 700; color: var(--ink); font-size: .95rem; }
.pc-approval .who { color: var(--blue); font-size: .82rem; font-weight: 600; margin-left: .35rem; }
.pc-approval .why { color: var(--muted); font-size: .82rem; margin-top: .1rem; }
.pc-flag { padding: .5rem .65rem; border-radius: 10px; margin-bottom: .45rem; font-size: .85rem; }
.pc-flag code { background: transparent; font-weight: 700; padding: 0; font-size: .8rem; }
.pc-flag .d { color: #334155; margin-top: .1rem; font-size: .82rem; }
.pc-missing { display: flex; gap: .5rem; align-items: flex-start; padding: .35rem 0; font-size: .9rem; color: var(--ink); }
.pc-missing .box { flex: 0 0 1rem; height: 1rem; border: 2px solid var(--red); border-radius: 4px; margin-top: .15rem; }
.pc-empty { color: var(--muted); font-size: .9rem; font-style: italic; }
.pc-banner { border-radius: 10px; padding: .65rem .85rem; font-size: .88rem; margin-bottom: .8rem; border: 1px solid; }
.pc-footer { color: var(--muted); font-size: .8rem; margin-top: 2rem; border-top: 1px solid var(--line); padding-top: .8rem; }
.pc-kv { width: 100%; border-collapse: collapse; font-size: .88rem; }
.pc-kv th { text-align: left; color: var(--muted); font-weight: 600; font-size: .75rem; text-transform: uppercase; letter-spacing: .04em;
  padding: .4rem .5rem; border-bottom: 1px solid var(--line); }
.pc-kv td { padding: .45rem .5rem; border-bottom: 1px solid var(--soft); color: var(--ink); vertical-align: top; }
.pc-kv tr.conflict td { background: var(--red-bg); }
@media (max-width: 640px) { .pc-title { font-size: 1.25rem !important; } .pc-rec .label { font-size: 1.1rem; } }
</style>
"""

TONES = {
    "blue": ("var(--blue)", "var(--blue-bg)"), "green": ("var(--green)", "var(--green-bg)"),
    "amber": ("var(--amber)", "var(--amber-bg)"), "red": ("var(--red)", "var(--red-bg)"),
    "violet": ("var(--violet)", "var(--violet-bg)"), "teal": ("var(--teal)", "var(--teal-bg)"),
    "orange": ("var(--orange)", "var(--orange-bg)"), "slate": ("#475569", "var(--slate-bg)"),
}

LABEL_STYLE = {
    PROCEED: ("green", "✓", "Ready for business approval routing"),
    USE_EXISTING: ("teal", "↺", "An approved tool may already cover this"),
    SPECIALIST: ("amber", "⚑", "Specialist review before approval"),
    CLARIFY: ("blue", "?", "Requester must supply missing information"),
    BUDGET_EXCEPTION: ("orange", "$", "Finance budget exception needed"),
    MANUAL_REVIEW: ("violet", "!", "Evidence could not be verified"),
    DO_NOT_PROCEED: ("red", "✕", "Do not proceed with this vendor"),
}

FLAG_INFO = {
    "budget_insufficient": ("red", "Cost exceeds the department's available software budget."),
    "conflicting_vendor_evidence": ("red", "Internal registry and vendor-risk service disagree."),
    "vendor_risk_unavailable": ("red", "Vendor-risk service could not be reached; nothing was assumed."),
    "vendor_risk_record_missing": ("red", "Vendor-risk service has no record of this vendor."),
    "prompt_injection_detected": ("red", "Request or vendor text tried to instruct the system; ignored."),
    "vendor_blocked": ("red", "Vendor procurement status blocks purchasing."),
    "vendor_security_rejected": ("red", "Vendor security assessment was rejected."),
    "high_vendor_risk": ("red", "Vendor-risk service rates this vendor high risk."),
    "llm_unavailable": ("red", "AI assessment unavailable; rules-only result shown."),
    "security_review_required": ("amber", "Security review is required by policy section 5."),
    "privacy_review_required": ("amber", "Privacy review is required by policy section 6."),
    "legal_review_required": ("amber", "Legal review is required by policy section 7."),
    "vendor_review_expired": ("amber", "Vendor security assessment is older than 365 days."),
    "ai_use_case_not_covered": ("amber", "Existing approval is limited and doesn't cover this data class."),
    "missing_information": ("amber", "Required request information is missing."),
    "unverified_approval_claim": ("amber", "Text claims an approval that isn't on record; not relied on."),
    "vendor_not_in_registry": ("amber", "Vendor is not in the internal registry (new vendor)."),
    "budget_record_missing": ("amber", "No budget record for the requester's department."),
    "existing_tool_overlap": ("slate", "An approved catalog product overlaps with this request."),
}

DATA_ACCESS_LEVELS = ["none", "internal_documents", "internal_marketing", "confidential_documents", "source_code",
                      "production_telemetry", "employee_pii", "customer_pii", "credentials", "unknown"]


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"${number:,.0f}" if number == int(number) else f"${number:,.2f}"


def badge(text: str, tone: str = "slate", icon: str = "") -> str:
    fg, bg = TONES.get(tone, TONES["slate"])
    return (f'<span class="pc-badge" style="color:{fg};background:{bg};border-color:{fg}22">'
            f'{esc(icon) + " " if icon else ""}{esc(text)}</span>')


def card(title: str, body_html: str) -> str:
    return f'<div class="pc-card"><div class="pc-h">{esc(title)}</div>{body_html}</div>'


def field(label: str, value: object, missing: bool = False) -> str:
    shown = "Not provided" if missing else value
    return (f'<div class="pc-field"><div class="k">{esc(label)}</div>'
            f'<div class="v{" missing" if missing else ""}">{esc(shown)}</div></div>')


def banner(text: str, tone: str = "amber") -> str:
    fg, bg = TONES.get(tone, TONES["amber"])
    return f'<div class="pc-banner" style="color:{fg};background:{bg};border-color:{fg}33">{esc(text)}</div>'


def recommendation_card(label: str | None, rationale: str, next_step: str, badges_html: str = "") -> str:
    tone, icon, caption = LABEL_STYLE.get(label or "", ("slate", "•", ""))
    fg, bg = TONES[tone]
    return (f'<div class="pc-rec" style="background:{bg};border-color:{fg}33">'
            f'<div>{badges_html}</div>'
            f'<div class="label" style="color:{fg}">{esc(icon)}&nbsp; {esc(label or "Recommendation")}</div>'
            f'<div class="why">{esc(rationale or caption)}</div>'
            f'<div class="next"><b>Next step</b>{esc(next_step)}</div></div>')


def approval_rows(approvals: list[str], reasons: dict[str, list[str]], assignments: dict[str, str]) -> str:
    if not approvals:
        return '<div class="pc-empty">No approvals listed.</div>'
    rows = []
    for i, role in enumerate(approvals, 1):
        why = reasons.get(role) or ["Added by the AI assessment"]
        who = f'<span class="who">{esc(assignments[role])}</span>' if role in assignments else ""
        rows.append(f'<div class="pc-approval"><div class="num">{i}</div><div>'
                    f'<div class="role">{esc(role)}{who}</div>'
                    f'<div class="why">{"<br>".join(esc(r) for r in why[:3])}</div></div></div>')
    return "".join(rows)


def flag_rows(flags: list[str]) -> str:
    if not flags:
        return '<div class="pc-empty">No risk flags.</div>'
    order = {"red": 0, "amber": 1, "slate": 2}
    ranked = sorted(flags, key=lambda f: order.get(FLAG_INFO.get(f, ("slate", ""))[0], 2))
    rows = []
    for flag in ranked:
        tone, desc = FLAG_INFO.get(flag, ("slate", "Additional flag raised by the AI assessment."))
        fg, bg = TONES[tone]
        rows.append(f'<div class="pc-flag" style="background:{bg};color:{fg}"><code>{esc(flag)}</code>'
                    f'<div class="d">{esc(desc)}</div></div>')
    return "".join(rows)


def missing_rows(items: list[str]) -> str:
    if not items:
        return '<div class="pc-empty" style="color:var(--green);font-style:normal">✓ All required information is present.</div>'
    return "".join(f'<div class="pc-missing"><div class="box"></div><div>{esc(i)}</div></div>' for i in items)


def table(headers: list[str], rows: list[list[object]], highlight: set[int] | None = None) -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(
        f'<tr class="{"conflict" if highlight and i in highlight else ""}">' + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>"
        for i, row in enumerate(rows))
    return f'<table class="pc-kv"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


TIER_SHORT = {
    "Up to $1,000": "≤ $1k",
    "$1,000.01 - $10,000": "$1k – $10k",
    "$10,000.01 - $25,000": "$10k – $25k",
    "Above $25,000": "> $25k",
}


def md_safe(text: object) -> str:
    """Escape `$` so Streamlit markdown doesn't render text between two dollar signs as LaTeX."""
    return str(text).replace("$", r"\$")
