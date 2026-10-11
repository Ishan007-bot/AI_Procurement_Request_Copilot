# Assumptions

Interpretations made where the policy or data leave room. Each is applied in code, not by the model.

## Organisation and approvers
- **Manager** is the requester's direct `manager_id`.
- **Department Head** is the first person with level `Director` or `VP` **strictly above** the requester in the reporting chain. A Director's request therefore goes to their VP, so nobody approves their own request.
- When that person sits in a different department (e.g. the Go To Market director over Sales and Customer Success), they are still used, and the mismatch is shown as evidence.
- If no Manager or Department Head can be found (e.g. a request from the top of the org), the role stays in `required_approvals`, but the named person is marked "to be assigned by Procurement". The system never guesses a name.

## Dates
- All date checks use the reference date in the active policy file (2026-09-30 in the provided snapshot), never the computer's clock.

## Thresholds and budget
- Approval tiers compare the annual amount rounded to cents: up to $1,000.00 → Manager; $1,000.01–$10,000.00 → Department Head + Procurement; $10,000.01–$25,000.00 → adds Finance; above $25,000.00 → adds CFO.
- The policy uses **different boundaries**: Finance starts **above** $10,000 (section 4), while Legal for a new vendor starts **at** $10,000 (section 7). Both are applied literally, so a $10,000.00 new-vendor request needs Legal but not Finance.
- A cost equal to the available budget is **within** budget.
- If the cost is missing, no tier is guessed. Procurement is added to triage the incomplete request, and the request goes back for clarification.
- A cost described as "one-time" is treated as the annual amount for thresholds, and this is shown as evidence.
- Urgency never waives a review or approval.

## Required information
- An empty integrations list (`[]`) means "no integrations" and is complete. A missing/null value means the field wasn't answered.
- A data-access level of `none` is a valid answer. `unknown`, blank, `tbd` and similar values mean it is missing.
- A business justification shorter than 15 characters counts as missing. Whether a longer justification is *credible* is judged by the model, not the rules.

## Security, privacy, legal
- Security data triggers: any data level containing `pii`, `confidential`, `source_code`, `credential`, `secret`, `restricted`, or starting with `production`.
- Integration triggers use word-boundary keyword matching: production / cloud account / AWS / GCP / Azure / Kubernetes, Git / GitHub / GitLab / Bitbucket / source code, vault / secrets / credentials / passwords. So "Digital signage" does not match "git". SSO alone is not a trigger.
- An **unknown** data level adds Security (sensitive access can't be ruled out).
- A security assessment is current while it is **at most 365 days** old on the reference date (365 days = current, 366 = expired). A review date after the reference date is treated as untrustworthy.
- Registry and vendor-risk statuses are grouped (approved / expired / incomplete / rejected / unknown) before comparing. Registry "Pending" and API "not_completed" agree, so they're not a conflict. A different group, or a different review date, **is** a conflict: both values are shown, neither is picked, and the request goes to Security / manual review. "Unknown" in one source is treated as a gap, not a contradiction.
- If the vendor-risk service is unavailable (or has no record), nothing favourable is assumed: Security review is added. If the data is sensitive, Privacy is also added, because data residency can't be verified.
- Privacy: employee or customer PII, or vendor storage outside the operating region combined with sensitive data. A vendor that "processes personal data" while the request declares non-PII data is shown as a note, not a trigger.
- New vendor = not in the registry, or registry procurement status not `Approved`. A new vendor with an unknown cost gets Legal (the $10,000 threshold can't be ruled out).
- Cross-region storage of sensitive data counts as a material data-processing issue → Legal.
- An existing approval marked limited/restricted (catalog status, registry notes or vendor-risk notes) does not cover sensitive data: flag `ai_use_case_not_covered`, plus Security (and Privacy for PII).

## Overlap
- Catalog candidates match on the same product, the same product family (one name contains the other as whole words), the same vendor, or the same category. Each candidate is labelled `same product / expansion` or `alternative`, and marked as available to the requester when its scope is company-wide or the requester's department.
- `existing_tool_overlap` is raised whenever a candidate exists. It surfaces options and never rejects a request by itself. Whether the request shows a credible gap is the model's judgement.

## Rules-only (baseline) recommendation
Used as the no-LLM fallback and as an evaluation baseline. The first matching rule wins: missing info → *Request clarification*; blocked/rejected vendor → *Do not proceed*; unavailable, missing or conflicting evidence (or no budget record) → *Manual review*; budget shortfall → *Escalate: budget exception*; any Security/Privacy/Legal review → *Route for specialist review*; an available alternative tool → *Use existing tool / confirm need*; otherwise → *Proceed to approval routing*.

Known limitation: the rules can't tell a product-tier duplicate (e.g. "TaskFlow Pro" when TaskFlow is company-wide) from a genuine expansion (e.g. "SignFlow Add-on" for more signers), because both are same-vendor expansions by name. That needs the justification to be read, which is the model's job, and is measured in the evaluation.

## Untrusted content (section 9)
- Every free-text value the system reads is scanned: all request fields, registry fields, vendor-risk API fields, and the catalog and purchase-history notes of matched products. Text is normalised first (Unicode NFKC, zero-width characters removed, whitespace collapsed), so simple obfuscation doesn't hide a match.
- Two kinds of finding are kept apart:
  - `prompt_injection_detected`: text that tries to instruct the system (ignore/override rules, bypass a review, approve immediately, treat as approved / low risk, "no review required", role or prompt manipulation, secret exfiltration, fake delimiter tags).
  - `unverified_approval_claim`: text asserting an approval ("pre-approved", "CFO-approved"). It may be genuine context, but it is never evidence. Approvals only come from the policy floor and humans.
- Detection **never changes approvals or flags downward**. The embedded text is ignored as an instruction, routing continues under the real policy, and the reviewer sees the quoted excerpt plus a note in the next step. It does not block routing by itself: section 9 says to continue with the real policy.
- Untrusted text shown to a model is wrapped in `<untrusted_data field="...">...</untrusted_data>`; delimiter look-alikes inside the text are neutralised.
- Regex detection is a backstop, not the only defence. The structural defences are that the model can't lower the floor, and that approvals come only from code.

## Guardrails between the model and the final decision
- The final approvals, risk flags and missing information are the model's lists **plus** the policy engine's floor. Anything the model left out is added back and recorded (`approval_added`, `flag_added`, `missing_added`).
- Approval names are mapped to the eight canonical roles (e.g. "dept head" → Department Head, "InfoSec" → Security). Names that can't be mapped are dropped and recorded.
- A missing-information item is dropped (and recorded) only when it plainly asks for a field the request actually contains: annual cost, user count or data-access level. Other requests for information (e.g. "vendor SOC 2 report") are kept. The match is on phrases that *name* the field ("annual cost", "number of users", "data-access level"). A looser first version also dropped follow-up questions that merely mentioned "data access"; this was found in the evaluation and tightened after the core runs (which used the earlier version).
- `missing_information` means required request information that is missing (policy section 1). When the request is complete, the model's follow-up questions (e.g. "confirm the extra seats are needed") are moved into the next step as *Open questions for reviewers*, rather than making a complete request look incomplete (`missing_moved`). A stray `missing_information` flag on a complete request is removed.
- The clarification floor ("Request clarification") is driven only by the engine's section 1 check. Model follow-up questions never turn a correct "Manual review" or "Specialist review" into "Request clarification".
- Evidence must come from a tool that actually ran successfully in that run; anything else is dropped. Key engine findings (threshold, budget, vendor risk, conflicts, expiry, restricted use, untrusted content, missing fields) are added with source `policy_guardrail` when the agent didn't cite that tool. Named Manager / Department Head approvers are always added.
- The recommendation can't be less restrictive than the evidence allows. Order, most restrictive first: clarification → do not proceed → manual review → budget exception → use existing tool → specialist review → proceed. If the model picks a label below the floor, it is raised to the floor and the next step is rewritten. A *more* cautious label (e.g. "Use existing tool" where the rules alone say "Proceed") is kept, because that is the model's judgement.
- `human_review_required` is always `true`.
- If no model is usable (missing key, quota exhausted, provider error, no valid output within 10 turns), the rules-only decision is returned with the flag `llm_unavailable` and a note that a person must judge any existing-tool overlap. A run never fails outright.
