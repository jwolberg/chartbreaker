# USERS.md — Who ChartBreaker Is Built For

> **Companion docs:** [`THREAT_MODEL.md`](./THREAT_MODEL.md) (what we attack), [`ARCHITECTURE.md`](./ARCHITECTURE.md) (how the platform is built), [`PROJECT_STRATEGY.md`](./PROJECT_STRATEGY.md) (operating model + acceptance criteria).
> **Status:** Draft. Personas and workflows below are the working definition; refinement comes from operator experience post-MVP.

---

## Summary

ChartBreaker serves two operator personas who run the platform and three stakeholder personas who consume its output. The operators — an **Application Security / Red Team Engineer** and the **AI Platform Engineer** who owns the Co-Pilot — are the people who type commands, triage findings, and apply fixes. The stakeholders — the **Clinical Product Owner**, the **Compliance / Privacy Officer**, and the **CISO** — never touch the CLI; they read the dashboard, sign off on remediation, and decide whether the platform is trustworthy enough to gate future deploys. The platform exists because none of these roles can do their job on manual prompting alone: a clinical LLM with soft defenses (a one-paragraph DATA-ONLY rule, regex-and-schema verifiers, session-scoped rate limits) cannot be evaluated thoroughly, repeatably, or continuously without a multi-agent loop that mutates partial successes, judges with a separated verdict, and pins confirmed exploits into a regression suite. Automation is not a convenience here; it is the only way to make the *coverage*, *reproducibility*, and *regression-over-time* requirements achievable on a real release cadence.

---

## Operator Personas

These are the users who run ChartBreaker. They have shell access, they hold the API keys, and they own the outcomes.

### 1. AppSec / Red Team Engineer (primary operator)

**Role context.** A security engineer responsible for offensive evaluation of the Clinical Co-Pilot. Comfortable with HTTP, Python, the OWASP top 10, and LLM-specific attack categories (prompt injection, indirect injection, data exfiltration, jailbreaks). Not necessarily a clinical SME — relies on `THREAT_MODEL.md` and the seed cases to know what counts as "unsafe" in this domain.

**Primary workflows.**
- Trigger an on-demand campaign against a specific threat-model subcategory: `chartbreaker run --campaign cat-1b-indirect-injection --mutation-budget 10`
- Triage Judge verdicts after a campaign: filter the dashboard for `partial` verdicts, decide which ones deserve mutation budgets, which deserve human escalation, which should be dropped as off-target.
- Review Scribe drafts in `reports/draft/` and promote them to `reports/` after sanity-checking the reproducer.
- Run the full regression suite manually before sign-off on a Co-Pilot deploy: `chartbreaker regress`.
- Add new seed cases to `evals/seed_cases.yaml` when a novel attack technique is published externally.
- Periodically re-calibrate the Judge by adding labeled fixtures to `evals/judge_calibration.yaml` and validating accuracy stays above threshold.

**Pain points automation solves.**
- Manual prompting against a chatbot is slow, lossy, and unreproducible. The same human running the same exploit twice may get different results because session state drifts. Automation pins the attack + the target version + the verdict so the experiment is replayable.
- Mutating a near-miss attack ten different ways by hand takes a half-day. The Red Team specialists do it in a campaign tick.
- Without a regression suite, the operator has no way to know if last month's fix still holds. Manual re-testing of every prior exploit on every deploy is impossible at any real cadence.

**Artifacts they touch.** CLI directly. `evals/` directly. `reports/draft/` directly. The dashboard for triage.

### 2. AI Platform Engineer (Co-Pilot owner)

**Role context.** The engineer or team that owns the OpenEMR Clinical Co-Pilot module (`interface/modules/custom_modules/oe-module-clinical-copilot/`). Holds the keys to `AgentOrchestrator.php`, the verifiers, the prompt template, the RAG ingestion pipeline. Reads ChartBreaker findings, applies fixes, validates the fixes did not regress something else.

**Primary workflows.**
- Receive a Scribe-drafted `AF-NNN-*.md` report from the AppSec operator. Read the reproducer, run it locally against a non-prod Co-Pilot instance, confirm the exploit.
- Design a fix in PHP (most often a verifier hardening, a system-prompt addition, or a guard at `CopilotController.php`). Push the fix to a staging deploy.
- Trigger an on-demand regression sweep against the staged deploy via the CLI or via the GitHub Actions hook: `chartbreaker regress --target-version <git-sha>`.
- Read the cross-category regression report: did the fix hold? Did it cause a different category to regress? (The "the fix moved the symptom" check from `ARCHITECTURE.md` § Regression Harness.)
- Sign off on the fix by promoting the regression case verdict from `success` to `fixed` in `evals/regression_cases.yaml`.

**Pain points automation solves.**
- Without a regression harness, a fix that "looked right" in manual testing has no continuous proof that it stays right. Six months later, an unrelated change to the prompt template reintroduces the same exploit and nobody notices.
- The Judge's two-part verdict (verifier-replay + semantic) tells the platform engineer something they cannot get from logs alone: *did the model violate the DATA-ONLY rule even when the output looked safe after scrubbing?* That is the distinction that drives whether a fix is real or cosmetic.
- Cross-category regression detection — "you fixed Category 1b but Category 5 just regressed" — is impossible to do manually across the full attack surface, and is the single most expensive class of bug to ship.

**Artifacts they touch.** CLI to run targeted regression sweeps. `reports/` to read findings. `evals/regression_cases.yaml` to promote verdict states. The dashboard to see regression-history trends per subcategory.

---

## Stakeholder Personas

These users never run a CLI command. They consume artifacts produced by the platform and gate decisions on them.

### 3. Clinical Product Owner

**Role context.** Owns the Co-Pilot as a product. Cares about which clinical workflows the assistant supports, which user-facing behaviors are acceptable, and which findings have real patient-safety implications versus which are theoretical. Not technical — reads English summaries of findings.

**Primary workflows.**
- Read the executive summary section of a Scribe-drafted vulnerability report to understand *clinical impact* — "could this exploit cause a clinician to read another patient's data?" "could this cause the assistant to recommend a treatment?" — and route the finding to the right priority lane.
- Decide whether a `medium`-severity finding is acceptable as a known limitation or must be fixed before a release. ChartBreaker surfaces the data; the product owner makes the call.
- Approve user-facing communication about a known limitation if the platform engineer decides not to fix it (or cannot fix it on the current release cycle).

**Pain points automation solves.**
- Manual security testing produces inconsistent reports — one engineer's "minor issue" is another's "P0." A Scribe-drafted report under a fixed schema, with severity scored against a deterministic rubric in the Judge, makes severity assessments comparable across findings.
- Without coverage telemetry, the product owner cannot answer "is the Co-Pilot less risky today than it was three months ago?" The dashboard's coverage-over-time view is the answer.

**Artifacts they touch.** `reports/AF-NNN-*.md` (the executive summary section especially). The dashboard's coverage-over-time and verdict-rate views.

### 4. Compliance / Privacy Officer

**Role context.** Responsible for HIPAA posture, BAA compliance, and the audit trail required to demonstrate due diligence. Cares about PHI handling, the BAA-gate behavior, the audit-log integrity, and whether the security testing process itself meets evidentiary standards.

**Primary workflows.**
- Audit ChartBreaker's own data handling: does the platform store real PHI? (No — fixture patients are synthetic, see § Out-of-Scope Users below.) Are raw response bodies redacted? (Yes — `redactor.py` runs pre-insert, with raw capture behind an explicit flag.) Is the audit log append-only? (Yes — `operator_action` rows in `runs.sqlite` are write-once.)
- Review BAA-gate findings (Category 3c, 6c-ii): can an admin flip `copilot_baa_acknowledged` without an actual BAA, and if so, does the platform document the exposure clearly enough for compliance to act on it?
- Sign off that the regression suite covers the categories most likely to surface a HIPAA-relevant incident — PHI leakage, cross-patient exposure, audit-log integrity.

**Pain points automation solves.**
- Manual security testing has no audit trail of *what was tested, by whom, when, with what result*. ChartBreaker's `operator_action` log + `runs.sqlite` + the pinned regression suite is the evidence package compliance needs to demonstrate ongoing diligence — not just one annual pentest report.
- Drift detection: did the system pass last quarter's PHI-leakage test? With manual testing the answer is "we think so." With ChartBreaker the answer is the date of the last green regression run for that subcategory, pulled from the dashboard.

**Artifacts they touch.** The dashboard's audit-log view. The full `reports/` directory. The retention policy in `PROJECT_STRATEGY.md`.

### 5. CISO / Security Leadership

**Role context.** Owns the organizational decision of whether to trust an AI-assisted clinical workflow in production. Not in the code, not in the eval cases, but holds the budget and the risk register. Asks questions like "is the Co-Pilot more or less resilient than it was a quarter ago?" and "what's the worst-case exploit and how confident are we it's been fixed?"

**Primary workflows.**
- Quarterly review of the platform's posture: review the dashboard (operator-shared screen or recent screenshots — ChartBreaker is operator-internal with no public CISO-facing URL), look at the coverage matrix, look at the trend lines for verdict success rate per category, read the executive summaries of any `critical`-severity findings still open.
- Approve or block deploys of the Co-Pilot to a wider clinical audience based on the regression-suite green/red state.
- Decide whether ChartBreaker itself is trustworthy enough to gate deploys — i.e., is the platform's calibration tight enough, are its findings reproducible enough, and is the Judge's accuracy good enough to make automated gating defensible?
- Sponsor the platform's resource allocation: API budget, dashboard hosting, operator headcount.

**Pain points automation solves.**
- Manual security testing cannot produce a defensible posture-over-time chart. The CISO's job in front of a board or an auditor requires that chart. ChartBreaker produces it.
- Without coverage telemetry, the CISO has to take the AppSec engineer's word for it that the testing was thorough. With the coverage table in the dashboard the CISO can verify it independently.
- The "what if our security tester quits" risk is real for manual testing. The regression suite + the orchestration logic mean the platform retains the institutional knowledge of what was tested and how — the operator becomes the person who maintains the platform, not the only person who knows the secrets.

**Artifacts they touch.** The dashboard's executive view (a future-state addition; MVP dashboard is operator-facing). The README's top-section summary. Quarterly posture reports drafted by the AppSec operator using dashboard exports.

---

## Cross-Cutting Workflows

How the personas hand off work through the platform — this is the operational workflow the platform is built to support.

```
AppSec Operator                       AI Platform Engineer
─────────────────                     ────────────────────
1. Triggers campaign       ───>       (notified async via report draft)
   via CLI
2. Triages verdicts        ───>
   in dashboard
3. Promotes Scribe draft   ───>       4. Reads report, builds reproducer locally
   to reports/                          5. Designs + ships fix to staging
                                        6. Triggers regression sweep against
                                           staged target-version
                                        7. Reads cross-category regression result
8. Approves the finding    <───       9. Promotes verdict state in regression_cases.yaml
   as `fixed` after a clean              and requests re-verification
   regression run

Clinical Product Owner       Compliance Officer       CISO
─────────────────────       ───────────────────       ────
- Reads executive            - Audits PHI handling     - Reads posture trend
  summaries quarterly          and BAA findings           charts quarterly
- Triages severity calls     - Signs off on retention   - Gates deploys on
                               + audit trail              regression-suite state
```

Every handoff above produces a structured trace event so a later reviewer can reconstruct who decided what, when, based on which artifact.

---

## Why Automation Is the Right Solution

The assignment's rubric explicitly asks for justification. Below is the consolidated case, persona by persona.

| For the AppSec Operator | Manual prompting cannot mutate a near-miss into ten variants in a tick; cannot replay yesterday's exploit against today's deploy; cannot maintain coverage telemetry over months. The Red Team specialists do all three. |
| For the AI Platform Engineer | A regression suite is the only way to know that last quarter's fix still holds after this quarter's unrelated change. ChartBreaker's pinned-fixture regression replay + cross-category regression flag is the operational answer to "did this fix move the symptom?" |
| For the Clinical Product Owner | Severity assessments must be consistent across findings to be useful for prioritization. A Judge with a deterministic rubric + a Scribe writing under a fixed schema produces comparable findings; ad-hoc manual reports do not. |
| For the Compliance Officer | HIPAA-grade audit trails require continuous evidence of testing, not annual pentests. ChartBreaker produces a date-stamped, verdict-pinned record of every test ever run against the Co-Pilot, which is the substrate compliance needs. |
| For the CISO | Continuous posture telemetry is the only basis for a defensible go/no-go on deploying AI-assisted clinical workflows. Manual testing cannot produce it. |

**Where automation is deliberately limited.** The platform stops short of automation at four points (see `ARCHITECTURE.md` § Human Approval Gates and `PROJECT_STRATEGY.md` § Platform Trust & Safety): no auto-publishing of critical reports, no auto-retiring of regression cases, no auto-deploying of fixes, no cross-target campaigns. These are the points where automation cost-out is high — a confidently-wrong critical-severity report wastes more engineering time than the platform saves, and an auto-retired regression case loses evidence. ChartBreaker automates *coverage, reproducibility, and judgment*; it leaves *publication, retirement, and remediation* to humans.

---

## Out-of-Scope Users (and Why)

These users are **not** served by ChartBreaker in its current form, and that is deliberate.

- **End clinicians using the Co-Pilot.** ChartBreaker is a behind-the-scenes security platform; it does not surface anything to the clinical UI and does not affect Co-Pilot responses in real time. Clinicians experience ChartBreaker only indirectly through the absence of bugs that would have shipped without it.
- **Patients.** No real PHI flows through ChartBreaker. Fixture patients are synthetic by construction. Patients are not users of this platform.
- **IT operations / SRE.** ChartBreaker is not an availability monitor, an alerting system, or a SIEM. SRE incidents are handled by other tooling.
- **Vendors / external CVE issuers.** ChartBreaker produces internal vulnerability reports; routing those reports externally is a separate process the platform does not implement.
- **Other OpenEMR module owners.** The platform's specialists, verifiers, and seed cases are tuned to the Clinical Co-Pilot. Adapting it to attack a different OpenEMR module is deliberate, supervised work — not a config flip.
- **Other Clinical Co-Pilot deployments.** The single-target invariant is a design commitment. Cross-target campaigns are blocked at the Target Client layer.

---

## Open Questions for the Personas Section

These are deliberately marked as open so the doc improves with operator experience.

- Does the AppSec operator persona need a separate "senior reviewer" sub-persona who approves promotion of `critical`-severity Scribe drafts? MVP collapses this into the single operator; Final may split it.
- Does the AI Platform Engineer persona overlap meaningfully with the AppSec operator in a small org? If so, the platform must avoid assuming a clean separation of duties at sub-team scale.
- Is the Clinical Product Owner the right name for the persona that owns severity triage, or is this better framed as a "Security PM" role? Naming pending operator feedback.
- Does the CISO persona warrant a dedicated dashboard view, or does the operator-facing dashboard suffice with curated exports? Currently planned: operator dashboard for MVP, optional CISO view for post-Final.
