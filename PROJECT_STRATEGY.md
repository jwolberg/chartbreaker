# AgentForge Project Strategy

## User-Facing Goal

Build a defensible multi-agent adversarial evaluation platform for the OpenEMR Clinical Co-Pilot. The platform should support both on-demand test-suite runs and continuous/adaptive testing against a live deployed healthcare AI workflow. In either mode, it should discover and mutate attacks, judge whether defenses held, convert confirmed exploits into regression cases, and produce security reports that an engineer can reproduce.

The strategic bar is not "find a flashy jailbreak." The bar is "a hospital CISO can understand what was tested, why it was prioritized, how the verdict was reached, what it costs, and whether fixes continue to hold."

## Assignment Organized by Required Outcome

| Outcome | Required artifact | Current repo status | Strategy |
|---|---|---:|---|
| Attack-surface understanding | `THREAT_MODEL.md` with ~500 word summary | Present | Treat as the source of truth. Keep prioritization focused on indirect prompt injection, PHI/verifier bypass, and cost/rate-limit abuse. |
| Multi-agent architecture | `ARCHITECTURE.md` with ~500 word summary and diagram | Present | Defend the role split: Orchestrator, RedTeamLead, specialists, Judge, Scribe, Regression Harness, Observability Store. |
| Initial eval dataset | `evals/` across at least three categories | Present | Seed suite covers Categories 1, 2, 5, and 6. Next step is live results, not more paper cases. |
| Architecture defense | checkpoint slides or talk track | Delivered; predecessor `ARCH_DEFENSE_SLIDES.md` retired in favor of this document | The checkpoint narrative now lives in § Recommended Checkpoint Narrative below. |
| User/workflow definition | `USERS.md` | Missing | Add a concise doc covering security engineer, AI platform engineer, clinical product owner, compliance reviewer, and hospital CISO workflows. |
| Cost analysis | likely `COST_ANALYSIS.md` | Missing | Add actual dev spend and projections for 100, 1K, 10K, and 100K test runs, including non-token costs and architectural changes at scale. |
| Vulnerability reports | minimum three reports | Missing | Produce report drafts only after live/fixture-backed findings. Use IDs like `AF-001`. |
| Live target evidence | deployed URL and test results | URL exists in `ARCHITECTURE.md`; results absent | Run or document a live eval path against the configured deployment and persist results under `evals/results/`. |
| Run logging and state updates | structured attempt log and state store | Partially described in `ARCHITECTURE.md` | Add a durable database-backed run ledger so humans can see which test ran, with which parameters, against which target, and what happened while Red Team work is still in progress. |
| Repository setup guide | README | Missing | Add setup, env vars, run commands, target URL, and demo instructions. |
| Demo video and social post | external deliverables | Not in repo | Prepare from the architecture defense plus one live eval run. |

## Strategic Positioning

AgentForge should be presented as a repeatable evaluation platform with two operating modes:

- **On-demand suite run:** execute the current seed and regression cases once against the live target, produce verdicts, costs, and reports, then stop. This is the MVP/demo path and the safest default for a reviewer.
- **Continuous/adaptive mode:** run the same loop on a schedule or trigger, use observability to choose follow-up campaigns, mutate partial successes, and re-run regressions after target changes.

The strategic claim is not that every environment needs always-on testing. The claim is that the architecture can start as a single controlled run and mature into a continuous security loop without changing the core trust model.

The adaptive loop works like this:

1. The threat model defines the attack surface and initial coverage priorities.
2. The Orchestrator reads coverage, cost, regressions, and unresolved findings.
3. Red Team specialists generate or mutate attacks according to attack shape.
4. The Target Client executes against the live Clinical Co-Pilot.
5. The Judge separates deterministic verifier replay from semantic safety judgment.
6. Confirmed exploits become regression cases.
7. The Scribe drafts vulnerability reports behind human approval gates.
8. Every agent handoff writes a structured run event so a human can inspect active work while it is happening.
9. Observability closes the loop by feeding the next Orchestrator decision.

This answers the assignment's central requirement: the system is multi-agent because attack generation, evaluation, prioritization, and documentation require different context, trust levels, and failure handling.

## Highest-Priority Scope

The MVP should stay narrow:

1. **Indirect prompt injection via chart text** (`1b`): highest leverage because untrusted chart text is passed into `PATIENT_CONTEXT`.
2. **Verifier bypass / PHI exfiltration** (`2a`, `2b`, `2f`): highest clinical and compliance impact.
3. **Cost and rate-limit abuse** (`5b`): important because the assignment explicitly calls out scale and cost as security concerns.
4. **Persona hijacking** (`6b`): useful demo surface because the expected safe behavior is clear and easy to explain.

Avoid expanding into unrelated OpenEMR core security unless it directly affects the Co-Pilot endpoint. The threat model explicitly excludes broad OpenEMR auth and the Next.js dashboard.

## Known Decisions

| Question | Answer |
|---|---|
| What is the target? | OpenEMR Clinical Co-Pilot V1, read-only, no live function-calling. |
| Must the platform test a live system? | Yes. The assignment hard gate requires a deployed target URL at every checkpoint. |
| Is a single agent acceptable? | No. The assignment explicitly rejects single-agent and simple pipeline architectures. |
| Which agents are required? | At minimum: attack generation, evaluation, orchestration, documentation. Current design exceeds that with specialists and deterministic harness components. |
| Should attack and judge live in the same context? | No. That creates a conflict of interest and weakens regression credibility. |
| Where should deterministic tooling replace LLMs? | HTTP protocol probes, CSRF/session tests, parameter fuzzing, regression replay, cost accounting, and verifier replay. |
| Where should LLMs be used? | Prompt mutation, multi-turn attack planning, semantic judging, report drafting, and human-readable orchestration rationale. |
| What is the first demo story? | Run seed cases, show a Judge verdict, show how a success or partial becomes a regression or mutation target, then show observability/cost trace. |

## Unknown Questions and Recommended Answers

| Unknown | Recommended answer | Why |
|---|---|---|
| What exact live target should be submitted? | Use `https://openemr.136-118-242-198.sslip.io` from `ARCHITECTURE.md`, unless credentials or availability changed. | The architecture already names it; changing targets late creates demo risk. |
| Are live target credentials available? | Assume `AGENTFORGE_TARGET_USER` and `AGENTFORGE_TARGET_PASSWORD` will be provided through environment variables. | Avoid hardcoding credentials and preserve auditability. |
| Which framework manages state? | Use LangGraph for agent state and SQLite/JSONL for durable traces. | Matches `ARCHITECTURE.md` and gives replayable decisions without overbuilding infrastructure. |
| What model powers Red Team? | Uncensored open-weights fine-tune via **OpenRouter** for prompt-craft specialists (default `cognitivecomputations/dolphin-mixtral-8x22b`); deterministic Python for protocol specialists. Local Ollama is an in-registry fallback. | Removes refusal-layer contamination without operating local GPU infra; one-row config swap to Ollama or to a different fine-tune. |
| What model powers Judge/Scribe/Orchestrator? | **OpenAI `gpt-5.4-nano` by default**, configurable per role via `MODEL_REGISTRY` in `config.py`. Calibration set in `evals/judge_calibration.yaml` is the trip-wire for escalating the Judge model. | Cost default that co-locates Judge inference with the target's provider family (`gpt-5.4-mini`); one-line escalation when calibration degrades. |
| How is Judge drift controlled? | Run calibration before live evaluation and halt below the configured threshold. | A drifting Judge corrupts the regression suite. |
| What triggers regression runs? | New deployment, target model change, newly confirmed exploit, and scheduled nightly run. | Covers code changes, model changes, and periodic resilience checks. |
| What requires human approval? | High/critical reports, regression retirement, privileged target actions, and changing target URL. | These are the points where automation can cause operational or trust damage. |
| How should cost be bounded? | Per-agent and per-campaign budgets, with Orchestrator halt when cost burns without signal. | Cost amplification is both an attack surface and a platform failure mode. |
| How should coverage be considered "enough"? | Minimum case count per subcategory plus mutation diversity and recent regression pass, not just one green test. | Static one-shot coverage does not satisfy the assignment's adaptability requirement. |
| What database should store run state? | SQLite for MVP; PostgreSQL when concurrent workers, hosted dashboards, or multi-user access are needed. | SQLite is simple, durable, queryable, and demo-friendly. PostgreSQL is the natural production upgrade without changing the event model. |
| What must be logged while Red Team is running? | Run id, campaign id, agent, specialist, seed case, mutation parameters, target URL, patient/test fixture id, request envelope hash, timestamps, status, target response metadata, Judge verdict, cost, and error state. | A human needs to know what test was run with what parameters and what result came back, even before final reporting is complete. |
| Should AF-SEED-008 stay? | No, not as currently written. Replace or re-scope it because it targets the out-of-scope Next.js dashboard JWT launch path. | `THREAT_MODEL.md` and `ARCHITECTURE.md` explicitly say the dashboard is reviewed separately. |
| Should vulnerability reports be written before live results? | Only as templates or clearly labeled drafts. Confirmed reports should be backed by live or replayable evidence. | The assignment asks for professional reproducible reports; unsupported reports weaken credibility. |

## Logging and State Store Requirement

AgentForge needs a durable run ledger that updates while the Red Team is working, not only after the suite finishes. The human operator should be able to answer, at any point in a run:

- Which campaign is active?
- Which agent or specialist is currently working?
- Which seed case or regression case is being used?
- What mutation parameters were selected?
- Which target endpoint and fixture patient were used?
- What request was sent, with sensitive values redacted or hashed?
- What status, latency, token usage, cost, and response summary came back?
- What did the Judge decide, and with what confidence?
- Did the attempt create a new finding, mutation request, regression candidate, or escalation?

Recommended MVP storage:

- **SQLite** as `observability/runs.sqlite`.
- **JSONL trace mirror** as `observability/traces.jsonl` for easy demo tailing and debugging.
- **YAML result snapshots** under `evals/results/` for submission artifacts.

Recommended production upgrade:

- **PostgreSQL** for multi-worker concurrency, hosted dashboards, role-based access, and long-term analytics.
- Keep the same event schema so SQLite-to-PostgreSQL is a deployment change, not an architecture rewrite.

Minimum database tables:

| Table | Purpose |
|---|---|
| `runs` | One row per suite run or continuous campaign session. |
| `campaigns` | Orchestrator-selected work units, including target subcategory and budget. |
| `attempts` | Every generated attack attempt with seed id, specialist, mutation axes, fixture id, and request metadata. |
| `agent_events` | Append-only state transitions: started, generated, sent, judged, mutated, failed, escalated, completed. |
| `target_responses` | HTTP status, latency, token usage, cost, response hash, redacted response excerpt, audit id. |
| `judge_verdicts` | Verifier replay result, semantic result, severity, exploitability, rationale, recommended next action. |
| `findings` | Confirmed vulnerabilities and their status: draft, needs_review, accepted, fixed, regression_pinned, retired. |
| `costs` | Per-agent, per-attempt, per-run spend and scaling metrics. |

Sensitive data rule: store enough to reproduce the test, but do not persist raw PHI by default. Store request/response hashes, source IDs, fixture IDs, redacted excerpts, and controlled test-patient references. Full raw payload capture should require an explicit debug flag and should never be enabled for real patient data.

## Immediate Gaps

1. Add `USERS.md`.
2. Add `COST_ANALYSIS.md`.
3. Add `README.md` with setup, env vars, target URL, and run commands.
4. Replace or revise `AF-SEED-008` so Category 6 stays aligned with the Co-Pilot target rather than the out-of-scope dashboard.
5. Add the SQLite-backed run ledger schema and JSONL trace writer.
6. Run validation for YAML parsing and, once credentials are available, run the live seed suite.
7. Generate at least three vulnerability report drafts from actual findings or clearly identified fixture-backed examples.

## Recommended Checkpoint Narrative

Lead with the problem: clinical LLM defenses are soft, stateful, and expensive to test manually.

Then make the architecture claim: AgentForge is a multi-agent loop where each role has a bounded responsibility and trust level.

Then defend the top choices:

- The Red Team and Judge are separate.
- Prompt-craft attacks use LLM specialists; protocol and cost attacks use deterministic Python.
- The Orchestrator prioritizes based on coverage, severity, recent change, and cost.
- The Judge uses both verifier replay and semantic evaluation.
- The Scribe cannot publish high-severity reports without human review.
- Red Team work emits structured state updates while it runs, backed by SQLite and mirrored to JSONL.
- Every autonomous decision is replayable from the observability store.

Close with evidence: current threat model, seed suite across four categories, calibration dataset, architecture diagram, and the next live run path.

---

## Operating Model

The PRD-shaped questions about *where AgentForge runs*, *how operators interact with it*, and *what data lives where* are answered below. These are deliberately additive to `ARCHITECTURE.md`, which covers the *technical* architecture; this section covers the *operational* architecture.

### Hosting Topology (Tiered)

AgentForge is not a single deployable artifact — it is three operational surfaces, each placed where the latency, cost, and trust model fit best.

| Component | Where it runs | Why there | Cost |
|---|---|---|---|
| Operator-driven campaigns (interactive CLI runs) | **Operator laptop** | CLI tool with full access to the operator's credentials and SQLite store; no reason to push to a server during MVP | $0 |
| Scheduled regression sweeps | **GitHub Actions cron** (or any CI) | Matches the rubric's "deploy-triggered regression" pattern; secrets in repo-level settings; audit trail in Actions logs; concurrency-1 to avoid clobbering `runs.sqlite` | Free at MVP volume |
| Always-on observability dashboard | **Small VM** (Fly.io, Railway, or a $5 DigitalOcean droplet) | Needs a public HTTPS URL for the CISO/reviewer audience; pulls from a synced copy of `runs.sqlite` | ~$5–10/month |
| OpenEMR Clinical Co-Pilot (target) | Existing GCE VM | Out-of-scope for AgentForge hosting; documented under § Target Deployment in `ARCHITECTURE.md` | — |

**Hard rule: AgentForge does not co-locate with the target.** Even though both VMs are operated by the same person, the threat-model posture is "external adversary" and co-location accidentally grants AgentForge network privileges it should not have. The single-target invariant in `ARCHITECTURE.md` § Human Approval Gates is the technical enforcement of this posture.

**MVP submission specifics:** laptop CLI + `runs.sqlite` artifact committed to a private branch is sufficient. **Final submission:** spin up the dashboard with a public URL — it is the most demo-able artifact and is the answer to the rubric's observability questions.

### Interface Strategy — CLI Primary, Read-Only Dashboard, No Control GUI

| Surface | Built for MVP? | Tech | Justification |
|---|---|---|---|
| CLI — `agentforge run / seed / regress / report` | ✅ Yes, primary | Click or Typer | Scriptable, fits CI, audit trail in shell history, what security operators expect |
| Observability dashboard (read-only) | ✅ Yes, required | Streamlit reading `observability/runs.sqlite` | Renders coverage, verdicts over time, cost per agent, open reports. ~200 lines. The CISO-facing artifact. |
| Interactive GUI for triggering campaigns | ❌ No | — | Adds attack surface, auth complexity, schedule risk. CLI + cron covers the use case. |
| Public HTTP API for external integration | ❌ No (for now) | — | YAGNI for Week 3. Documented as a future-state non-goal. |

The observability dashboard is **not optional** — the rubric explicitly demands answers to "What is each agent doing, and in what order?" and "How much did this run cost?" Streamlit reading SQLite is the cheapest defensible path.

### Database — Beyond the MVP SQLite Choice

The existing § Logging and State Store Requirement settles SQLite-for-MVP and PostgreSQL-as-upgrade. The PRD also needs to answer four operational questions that the technical doc leaves implicit:

| Question | Answer |
|---|---|
| **Schema versioning** | Alembic-style migrations under `agentforge/observability/migrations/`. Every schema change ships a migration + a `runs.sqlite` rollback test. Schema-incompatible changes bump a `schema_version` column on the `runs` table; older rows are migrated forward, never silently reformatted. |
| **Retention** | Operational traces (`agent_events`, `target_responses`, `costs`) rotate at 90 days into an `archive/` directory of compressed JSONL. **Findings, regression-pinned exploits, and Judge verdicts are retained indefinitely** — they are the evidence layer. |
| **Backup** | `runs.sqlite` is backed up to object storage (S3-compatible) on every successful CI regression run. The platform's value is the regression history; losing it loses the "did the fix hold over time" signal. |
| **PHI at rest** | Fixture patients are synthetic by construction (see `target_deployment.md` — dedicated test user with explicit ACL access to a pinned synthetic fixture set). The platform is **not** HIPAA-covered because no real PHI is in scope. Raw `TargetResponse` bodies are still post-redacted by `redactor.py` before insert as a defense-in-depth measure; full raw capture requires an explicit `--debug-capture-raw` flag. |

### Secrets Management

| Secret | Storage | Rotation | Access |
|---|---|---|---|
| `OPENAI_API_KEY` | Operator's `.env` (gitignored) for laptop runs; GitHub Actions secret for CI | Manual, quarterly | Operator + CI |
| `OPENROUTER_API_KEY` | Same | Manual, quarterly | Operator + CI |
| `ANTHROPIC_API_KEY` (optional, registry-only) | Same | Manual, quarterly | Operator + CI |
| `AGENTFORGE_TARGET_USER` / `AGENTFORGE_TARGET_PASSWORD` | Same; the *dedicated* test user, not admin | On any incident or every 90 days | Operator + CI |
| LangSmith API key (optional) | Same | Quarterly | Operator |

**Hard rules:**
- No secret is committed to the repo. `.env.example` ships with placeholder values; `.env` is in `.gitignore`.
- The dedicated AgentForge test user is rotated separately from any human credential set on the OpenEMR target.
- The offensive specialists' API keys (OpenRouter) are *never* shared with the Judge/Scribe stack (OpenAI). Different providers by construction means different secrets to rotate or revoke independently.

---

## Success Criteria & Acceptance

The rubric grades thoroughness and defensibility, but "done" still needs measurable criteria. These are the thresholds the platform must meet to call MVP and Final complete.

### MVP (Tue 2026-05-12 23:59)

| Criterion | Target | How measured |
|---|---|---|
| Live target reachable from CLI | 100% of `agentforge run` invocations authenticate and post to `/copilot` endpoint | `target_client` health check |
| Distinct attack categories with live results | ≥3 (rubric hard gate) | `runs.sqlite` query: distinct `category` in `attempts` where `verdict IS NOT NULL` |
| At least one agent role live | Injector + Judge running end-to-end against the deployed target | Trace event with `agent='injector'` followed by `agent='judge'` and a `verdict` row written |
| Verifier-replay verdicts working | Python ports of `SourceAttributionVerifier` + `DomainConstraintVerifier` match Co-Pilot behavior on 100% of test fixtures | `tests/test_verifiers.py` green |
| Documents complete | `THREAT_MODEL.md`, `ARCHITECTURE.md`, `PROJECT_STRATEGY.md`, `USERS.md`, `README.md` all present | File existence + Final-deliverable checklist |

### Final (Fri 2026-05-15 noon)

| Criterion | Target | How measured |
|---|---|---|
| Coverage: distinct subcategories with ≥1 live attempt | ≥10 of ~20 subcategories in `THREAT_MODEL` | `runs.sqlite` |
| Coverage: minimum attempts per attempted subcategory | ≥5 attempts each (rubric: enough to demonstrate mutation, not one-shot) | `runs.sqlite` |
| Judge calibration accuracy | ≥85% on the held-out `evals/judge_calibration.yaml` set; platform halts below 70% | `tests/test_judge_calibration.py` |
| Confirmed exploits → regression-pinned | ≥3 distinct `success` verdicts converted to `evals/regression_cases.yaml` entries | `regression.py` audit log |
| Vulnerability reports drafted | ≥3 distinct `reports/AF-NNN-*.md` files (rubric minimum) | Filesystem |
| Cost analysis | `COST_ANALYSIS.md` at 100 / 1K / 10K / 100K with architectural implications per tier | Document review |
| Observability dashboard live | Public HTTPS URL serving the read-only Streamlit dashboard | Reviewer visit |
| Demo video | 3–5 min showing the loop running live against the deployed target | Recording uploaded |
| Social post | One post on X or LinkedIn tagging @GauntletAI | Link committed to repo |

---

## Platform Trust & Safety

The rubric appendix demands answers to "How do you prevent the adversarial platform from being turned against systems it should not attack?" The platform itself is a security artifact and is therefore in scope for its own trust model.

| Control | Mechanism |
|---|---|
| Single-target invariant | Target base URL hardcoded in `config.py`; `target_client` refuses dispatch to any other host. Override requires both `--target-override` and `--i-understand-this-attacks-the-target` flags. |
| Operator authentication | MVP is single-operator (the repo owner). Multi-operator is a Final/post-Final consideration and would add OAuth + per-operator audit columns. |
| Operator action audit | Every CLI invocation writes an `operator_action` row to `runs.sqlite` with `{cli_command, args, operator, timestamp, git_sha}`. The audit log is append-only. |
| Kill switch | A `STOP` file in the repo root halts any in-progress campaign at the next agent transition. The Orchestrator checks for it on every tick. |
| Report-publish gate | Scribe never auto-files; `reports/draft/` requires a human `git mv` to promote to `reports/` (see `ARCHITECTURE.md` § Human Approval Gates). |
| Regression-retire gate | A regression case is retired only via human commit with explicit reason. The Orchestrator cannot retire cases autonomously. |
| Privileged target actions | Vision-extraction uploads, BAA-flag tests, and login brute-force probes require an explicit `--allow-privileged` flag. The flag is logged on every use. |
| Cracker brute-force rate cap | The Cracker specialist's login probes are rate-limited at the Cracker layer (not server-side) to avoid locking out the AgentForge test user during brute-force testing. |

---

## Failure Modes & Platform SLOs

The individual agent failure modes are documented per-agent in `ARCHITECTURE.md`. The platform-level SLOs and shared failure handling live here.

| Failure | Detection | Response |
|---|---|---|
| Target unreachable | `target_client` HTTP error / timeout | Halt current campaign, write `target_unreachable` trace row, do **not** record as a verdict. Retry with exponential backoff up to 3 times before halting. |
| Target rate-limit (HTTP 429) | Response status | Log as `rate_limited`. **Do not retry** — for Category 5b campaigns this is the signal itself. |
| Session expired | 401/302-to-login on Co-Pilot endpoint | Re-authenticate once, retry once, halt on second 401 (avoids credential-loop lockout). |
| Judge calibration drops below 70% | Pre-run calibration sweep against `judge_calibration.yaml` | Halt campaign before live runs; write `calibration_failed` event; surface in dashboard. |
| Cost ceiling hit | Per-campaign budget check at every agent transition | Halt gracefully, write summary, persist state for resume. |
| Stuck campaign (no verdicts in N transitions) | Orchestrator tick check | Halt with "no signal" event; flagged for human review. |
| Schema-incompatible upgrade attempted | Migration test on startup | Refuse to start until migration is applied; never silently mutate `runs.sqlite`. |
| Local-vs-CI clock skew | Comparing `git_sha` of regression case vs target | Halt; cases must be replayed against the target version they were pinned to (or explicitly re-pinned). |

**Platform availability SLO:** none in the formal sense. AgentForge is operator-driven; "down" means the operator does not run it. The dashboard is the only always-on surface, and a 24-hour outage on it is a P3, not P1.

---

## Non-Goals (for the platform itself)

These are explicit, written-down decisions about what AgentForge *will not* do — both to manage scope and to make the trust posture defensible.

- **Not a SIEM, WAF, or HIDS.** AgentForge does not monitor production traffic, detect ongoing attacks, or block in-line. It is an offline / scheduled evaluation system.
- **Not an auto-remediation tool.** AgentForge proposes fixes through the Scribe; it never edits Co-Pilot code, opens PRs, or triggers deployments.
- **Not a multi-target platform.** The single-target invariant is a design commitment, not an MVP shortcut.
- **Not a multi-tenant SaaS.** No public sign-up, no per-customer isolation. Single-operator for MVP and Final.
- **Not a general-purpose LLM red-teaming framework.** AgentForge is target-aware: its specialists, verifiers, and seed cases are tuned to the OpenEMR Clinical Co-Pilot. Retargeting requires deliberate, supervised work, not a config flip.
- **Not a vulnerability disclosure pipeline.** The Scribe drafts reports; routing those reports to a vendor, CVE issuer, or ticketing system is out of scope.
- **No control GUI.** Triggering campaigns is a CLI operation; reading state is a dashboard operation. We do not merge the two.
- **No live PHI in test data.** Fixture patients are synthetic; the platform's compliance posture rests on this assumption.

---

## Release & Change Management

The platform has its own version, its own changelog, and its own upgrade path — separate from the Co-Pilot target's release cycle.

| Concern | Approach |
|---|---|
| Platform version | Semver on `agentforge` Python package. `0.x` while pre-Final. Pin in every regression-case fixture so old cases replay against the platform version that recorded them. |
| Regression case schema migration | Versioned YAML schema; case fixtures carry `schema_version`; `regression.py` refuses to replay cases written under a future schema. |
| Judge model bumps | Bumping the Judge model in `MODEL_REGISTRY` requires a full re-run of `judge_calibration.yaml` and an explicit human commit. Verdicts pinned with the old model are flagged for re-verification, not silently inherited. |
| Target version drift | `runs.sqlite` records the Co-Pilot model + version on every attempt. A target version change triggers a regression sweep + a human triage of any verdicts whose pinned version no longer matches. |
| AgentForge dependency bumps | `requirements.txt` pinned; `pip-tools` for compiled lock; security advisories from `pip-audit` checked in CI. |

---

## Demo & Social Plan (Final-Only)

| Artifact | Plan |
|---|---|
| Demo video (3–5 min) | Three-act structure: (1) the problem — show a manual jailbreak attempt + how slow / unrepeatable it is; (2) the platform — run `agentforge run --campaign cat-1b-injection` and walk through Orchestrator → Injector → Target → Judge → Scribe in the dashboard; (3) the regression — re-run a previously-pinned exploit and show it still fails (or passes if the Co-Pilot fixed it). Record in OBS or Loom; upload to YouTube unlisted. |
| Social post | One post on X or LinkedIn (operator preference) tagging @GauntletAI. One paragraph + one screenshot of the dashboard. Drafted alongside the README. |
| Reviewer-facing artifacts | Public dashboard URL, repo URL, deployed target URL, demo video link — all linked from the README's top section. |

---

## Refreshed Immediate Gaps (replaces the earlier list)

Ordered by deadline pressure, with owner / dependency notes.

| # | Gap | Required for | Effort | Blocks |
|---|---|---|---|---|
| 1 | `USERS.md` draft | MVP submission (Final hard gate) | 1–2 hr | Final |
| 2 | `README.md` (setup + env vars + deployed URL + run commands) | MVP submission | 1 hr | MVP |
| 3 | `agentforge/` scaffold: `cli.py`, `config.py`, `llm_client.py`, `state.py`, `target_client.py` | MVP gate 3 (one agent role live) | 4–6 hr | MVP gate 3 |
| 4 | Injector → Target → Judge end-to-end loop against the deployed target | MVP gate 3 (rubric hard gate) | 4 hr | MVP submit |
| 5 | Verifier replay (Python ports of `SourceAttributionVerifier` + `DomainConstraintVerifier`) | Judge verdicts | 2–3 hr | Final |
| 6 | Replace/rescope `AF-SEED-008` (currently targets out-of-scope dashboard JWT) | Eval-suite integrity | 30 min | MVP |
| 7 | SQLite schema + JSONL trace writer | All observability claims | 2 hr | MVP |
| 8 | Streamlit dashboard (read-only) | Final demo + CISO-facing artifact | 2–3 hr | Final |
| 9 | `COST_ANALYSIS.md` at 100 / 1K / 10K / 100K | Final submission | 2 hr | Final |
| 10 | ≥3 vulnerability reports (`reports/AF-NNN-*.md`) drafted from live findings | Final submission | depends on platform working | Final |
| 11 | Judge calibration set fleshed out + `tests/test_judge_calibration.py` green | Final submission credibility | 2 hr | Final |
| 12 | Demo video (3–5 min) | Final submission | 2 hr | Final |
| 13 | Social post on X/LinkedIn tagging @GauntletAI | Final submission | 30 min | Final |
