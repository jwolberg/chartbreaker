# Build Plan

## Project
- **Name:** ChartBreaker — Multi-Agent Adversarial Evaluation Platform
- **Summary:** ChartBreaker is a multi-agent adversarial platform that continuously probes the OpenEMR Clinical Co-Pilot for vulnerabilities, evaluates whether confirmed exploits reproduce, and converts them into a regression suite that runs on every deploy. The Clinical Co-Pilot's defenses are *deliberately soft* (one-paragraph DATA-ONLY rule, regex+schema verifiers, session-keyed rate limits); ChartBreaker's job is to identify which specific bypasses are reachable in this deployment, how reliably they reproduce, and whether fixes hold under mutation.

## Source of Truth
- **Assignment:** `/docs/ASSIGNMENT.md`
- **Strategy / acceptance criteria / non-goals:** `/docs/PROJECT_STRATEGY.md`
- **Technical design:** `/docs/ARCHITECTURE.md`
- **Personas / workflows:** `/docs/USERS.md`
- **Attack-surface model:** `/docs/THREAT_MODEL.md`
- **UX clarifications:** none (no `/ux.md` present)

## Planning Assumptions
- **USERS.md is complete** — PROJECT_STRATEGY § Refreshed Immediate Gaps row 1 ("USERS.md draft") is therefore retroactively marked Complete.
- **Live target is already up** — `https://openemr.136-118-242-198.sslip.io` (per `ARCHITECTURE.md` § Target Deployment + `PROJECT_STRATEGY.md` § Known Decisions). 
- **SQLite column shapes** are not specified in any input doc. PROJECT_STRATEGY § Logging and State Store Requirement enumerates the *tables*; column definitions must match the message types in `ARCHITECTURE.md` § Inter-Agent Communication. Treated as an ARCHITECTURE-wins technical decision per the conflict-resolution order.
- **MVP scope is interpreted narrowly** to the rubric hard gates (one agent role live, ≥3 categories with live results, threat-model + architecture docs present). Additional MVP components from `ARCHITECTURE.md` § MVP vs Final Cut (Saboteur, Cracker, Orchestrator priority math, RedTeamLead routing) are layered in Phase 1 after the rubric floor is met, in dependency order. If time runs out, stopping at the rubric floor still satisfies the MVP submission.

## Architecture Notes
- **Stack:** Python 3.10+ (validated against 3.10.10), Pydantic v2 state objects, `httpx` for all HTTP (including LLM dispatch), LangGraph 0.2+ for the state graph, SQLite (stdlib) for the canonical observability store, JSONL append-only mirror for traces. (`ARCHITECTURE.md` § Framework, State, and Coordination)
- **Model registry defaults** (`ARCHITECTURE.md` § Model Configuration):
  - Orchestrator / RedTeamLead / Judge / Scribe → OpenAI `gpt-5.4-nano`
  - Injector / Conversationalist / Smuggler / Impersonator → OpenRouter `cognitivecomputations/dolphin-mixtral-8x22b`
  - Saboteur / Cracker / Glutton → no model (pure Python)
  - Every role configurable per the `MODEL_REGISTRY` dict in `chartbreaker/config.py`; OpenAI / OpenRouter / Ollama / Anthropic are drop-in providers.
- **Hosting topology** (`PROJECT_STRATEGY.md` § Operating Model § Hosting Topology):
  - Operator laptop for interactive CLI runs
  - GitHub Actions cron for scheduled regression sweeps
  - **No public hosting for ChartBreaker.** Dashboard runs locally on operator's laptop (`localhost:8501`); the OpenEMR target is the only publicly addressable surface in the system.
  - **Hard rule:** ChartBreaker does *not* co-locate with the OpenEMR target VM.
- **Hard constraints:**
  - Single-target invariant — target URL hardcoded in `config.py`; override requires both `--target-override` and `--i-understand-this-attacks-the-target` (`ARCHITECTURE.md` § Human Approval Gates).
  - Attack/Judge isolation — the Judge never sees the RedTeam's reasoning, only the rendered attack and the target's response (`ARCHITECTURE.md` § Judge Agent).
  - Scribe drafts critical/high reports to `reports/draft/`; promotion requires human `git mv` (`ARCHITECTURE.md` § Human Approval Gates).
  - Cracker login-probe rate cap enforced *at the Cracker layer*, not server-side, to avoid locking out the ChartBreaker test user.
- **Non-goals affecting implementation** (`PROJECT_STRATEGY.md` § Non-Goals): no SIEM/WAF/HIDS, no auto-remediation, no multi-target campaigns, no multi-tenant SaaS, no control GUI (read-only dashboard only), no live PHI (synthetic fixture patients only).

## Current Status
- **Overall status:** Phase 1 — MVP Floor **complete** (15 of 16 tickets done, P1-T15 LangGraph deferred to Phase 2 with rationale). MVP rubric hard gate met with live evidence across 6 attack categories.
- **Current phase:** Phase 2 — MVP-to-Final
- **Current ticket:** none active (next: pick from Phase 2 priorities)
- **Blockers:** None
- **Last updated:** 2026-05-11 after Phase-1 completion

---

## Phase Breakdown

### Phase 1 — MVP Floor (deadline: Tue 2026-05-12 23:59)

**Goal**
Satisfy ASSIGNMENT's MVP hard gates: live target (✅ already up), threat model (✅ present), eval suite with ≥3 attack categories + ≥1 live agent role running against the deployed target, and architecture doc (✅ present). Lay the foundation files that Phase 2 specialists build on.

**Exit Criteria**
Every row in `PROJECT_STRATEGY.md` § Success Criteria § MVP is green:
- CLI authenticates and POSTs to `/copilot` 100% of the time
- ≥3 distinct attack categories with live results recorded
- ≥1 agent role (Injector + Judge end-to-end) verified live
- Verifier-replay Python ports match Co-Pilot behavior on 100% of test fixtures
- THREAT_MODEL, ARCHITECTURE, PROJECT_STRATEGY, USERS, README all present

**Tickets**

- **P1-T1 — README.md (setup + env vars + deployed URL + run commands)**
  - Objective: Repo entry point covering setup, env-var list (`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `CHARTBREAKER_TARGET_USER`, `CHARTBREAKER_TARGET_PASSWORD`), deployed target URL, and `chartbreaker` CLI commands. Required for MVP submission.
  - Files likely involved: `README.md`
  - Depends on: nothing
  - Acceptance criteria covered: `PROJECT_STRATEGY.md` § Success Criteria § MVP "Documents complete" row; `ASSIGNMENT.md` § Submission Requirements "GitHub Repository" row
  - Status: Complete (commit `0dd4123`)

- **P1-T2 — Replace/rescope `AF-SEED-008`**
  - Objective: The current seed targets the out-of-scope Next.js Dashboard JWT launch path. Re-scope to a Co-Pilot-relevant Category 6 seed (persona hijacking or CSRF replay) per `THREAT_MODEL.md` § Out of Scope and `PROJECT_STRATEGY.md` § Refreshed Immediate Gaps row 6.
  - Files likely involved: `evals/seed_cases.yaml`
  - Depends on: nothing
  - Acceptance criteria covered: `THREAT_MODEL.md` § Out of Scope (dashboard explicitly excluded); `PROJECT_STRATEGY.md` § Refreshed Immediate Gaps row 6
  - Status: Complete (commit `0dd4123`)

- **P1-T3 — `chartbreaker/config.py` with `MODEL_REGISTRY`**
  - Objective: Central config: target URL (hardcoded), session credentials (env-loaded), per-role model registry per `ARCHITECTURE.md` § Model Configuration default table, cost budgets, fixture-patient pid list.
  - Files likely involved: `chartbreaker/config.py`, `chartbreaker/__init__.py`, `.env.example`
  - Depends on: nothing
  - Acceptance criteria covered: `ARCHITECTURE.md` § Model Configuration (default registry); `ARCHITECTURE.md` § Target Deployment; `PROJECT_STRATEGY.md` § Operating Model § Secrets Management
  - Status: Complete (commit `621bcb9`)

- **P1-T4 — `chartbreaker/state.py` Pydantic state objects**
  - Objective: Typed `CampaignBrief`, `AttackAttempt`, `TargetResponse`, `Verdict`, `RegressionReport`, `ReportDraft`, `CostObservation` per `ARCHITECTURE.md` § Inter-Agent Communication message-types block.
  - Files likely involved: `chartbreaker/state.py`
  - Depends on: P1-T3
  - Acceptance criteria covered: `ARCHITECTURE.md` § Inter-Agent Communication (message types list)
  - Status: Complete (commit `eb0af1e`)

- **P1-T5 — `chartbreaker/llm_client.py` OpenAI-compatible dispatcher**
  - Objective: Thin async `httpx` wrapper that reads `MODEL_REGISTRY[role]` and dispatches against OpenAI / OpenRouter / Ollama / Anthropic over `/v1/chat/completions`. Records `{role, provider, model}` on every call for trace replayability.
  - Files likely involved: `chartbreaker/llm_client.py`
  - Depends on: P1-T3
  - Acceptance criteria covered: `ARCHITECTURE.md` § Model Configuration; `ARCHITECTURE.md` § Framework, State, and Coordination
  - Status: Complete (commit `6b59b53`)

- **P1-T6 — `chartbreaker/target_client.py` Co-Pilot HTTP wrapper**
  - Objective: Authenticate as the dedicated ChartBreaker test user, capture session cookie + CSRF token, dispatch `briefing` and `followup` requests with both body `csrf_token` and mirrored `X-CSRF-Token` header per `CopilotController.php:259`. Single-target invariant enforced in code (refuse hosts ≠ configured base URL unless `--target-override --i-understand-this-attacks-the-target`). Returns `TargetResponse` capturing raw output, post-verifier output, HTTP status, latency, token usage, audit ID.
  - Files likely involved: `chartbreaker/target_client.py`
  - Depends on: P1-T3, P1-T4
  - Acceptance criteria covered: `ARCHITECTURE.md` § Conduit — Target Client; `PROJECT_STRATEGY.md` § Success Criteria § MVP "Live target reachable from CLI" row; `ARCHITECTURE.md` § Human Approval Gates (single-target invariant)
  - Status: Complete (commit `d09548c`)

- **P1-T7 — Observability: SQLite schema + JSONL writer**
  - Objective: `observability/schema.sql` with tables from `PROJECT_STRATEGY.md` § Logging and State Store Requirement (`runs`, `campaigns`, `attempts`, `agent_events`, `target_responses`, `judge_verdicts`, `findings`, `costs`). Append-only JSONL mirror to `observability/traces.jsonl`. Schema version column on `runs` for forward migration.
  - Files likely involved: `chartbreaker/observability/__init__.py`, `chartbreaker/observability/schema.sql`, `chartbreaker/observability/store.py`
  - Depends on: P1-T4
  - Acceptance criteria covered: `PROJECT_STRATEGY.md` § Logging and State Store Requirement (table list); `ARCHITECTURE.md` § Observability Layer; `PROJECT_STRATEGY.md` § Operating Model § Database (schema versioning)
  - Status: Complete (commit `e430318`)

- **P1-T8 — Verifier replay (Python ports)**
  - Objective: Port `SourceAttributionVerifier` and `DomainConstraintVerifier` from the Co-Pilot module's PHP to Python. Parity test fixture: a curated set of known-good and known-bad Co-Pilot outputs where the PHP verifier verdict is recorded; the Python ports must match byte-for-byte.
  - Files likely involved: `chartbreaker/verifiers/__init__.py`, `chartbreaker/verifiers/source_attribution.py`, `chartbreaker/verifiers/domain_constraint.py`, `chartbreaker/tests/test_verifiers.py`, fixtures under `chartbreaker/tests/fixtures/verifier_parity/`
  - Depends on: P1-T3
  - Acceptance criteria covered: `PROJECT_STRATEGY.md` § Success Criteria § MVP "Verifier-replay verdicts working" row; `ARCHITECTURE.md` § Arbiter — Judge Agent (deterministic half)
  - Status: Complete (commit `d43b49c`)

- **P1-T9 — `chartbreaker/agents/judge_agent.py` (verifier-replay verdict only)**
  - Objective: MVP Judge: verifier-replay verdict on the raw model output (uses P1-T8 ports). Semantic LLM verdict is deferred to Phase 2 (`ARCHITECTURE.md` § MVP vs Final Cut row "Judge — semantic LLM" = "⏸ partial (binary fail/pass only)"). Emits `Verdict` with `{verifier_replay: pass|fail, semantic: not_run, severity: from_static_rubric, recommended_action: regression|discard}`.
  - Files likely involved: `chartbreaker/agents/__init__.py`, `chartbreaker/agents/judge_agent.py`
  - Depends on: P1-T4, P1-T8
  - Acceptance criteria covered: `ARCHITECTURE.md` § Arbiter — Judge Agent; `ARCHITECTURE.md` § MVP vs Final Cut "Judge — verifier replay" row
  - Status: Complete (commit `d43b49c`)

- **P1-T10 — `chartbreaker/agents/specialists/injection_specialist.py` (Injector for Cat 1a, 1b)**
  - Objective: MVP Injector covering direct injection (Cat 1a) and indirect injection via chart text (Cat 1b — the marquee finding). Reads seed cases, dispatches via `llm_client` to OpenRouter dolphin-mixtral, returns `AttackAttempt`. Other Category 1 sub-IDs are Phase-2 work.
  - Files likely involved: `chartbreaker/agents/specialists/__init__.py`, `chartbreaker/agents/specialists/injection_specialist.py`
  - Depends on: P1-T4, P1-T5
  - Acceptance criteria covered: `ARCHITECTURE.md` § Injector (LLM specialist); `THREAT_MODEL.md` § Category 1 (1a, 1b); `ARCHITECTURE.md` § MVP vs Final Cut "Injector" row
  - Status: Complete (commit `d43b49c`)

- **P1-T11 — Rubric-gate: Injector → Target → Judge end-to-end against live target**
  - Objective: Wire a minimal end-to-end loop (no Orchestrator priority math, no RedTeamLead routing, no graph yet — a straight `cli.py` script) that loads a Cat 1b seed case, calls Injector, posts to Target Client, runs Judge verifier-replay, writes a verdict row. Run it against three distinct attack categories (1a, 1b, plus a manual Cat 5 token-exhaustion probe) to satisfy the rubric's ≥3 categories requirement. **This ticket is the rubric MVP hard gate — Phase 1 can stop here if time runs out and still pass MVP submission.**
  - Files likely involved: `chartbreaker/cli.py` (minimal `chartbreaker run-mvp-loop` command)
  - Depends on: P1-T6, P1-T7, P1-T9, P1-T10
  - Acceptance criteria covered: `ASSIGNMENT.md` § Stage 3 Hard Gate ("≥3 distinct attack categories" + "≥1 agent role running live against the deployed target"); `PROJECT_STRATEGY.md` § Success Criteria § MVP rows 1, 2, 3
  - Status: Complete (commits `a37509f` code; `9d3bfd5` + `927db6c` debugging; live evidence captured in `observability/runs.sqlite`)

- **P1-T12 — `chartbreaker/agents/specialists/protocol_specialist.py` (Cracker for Cat 2f + 6a)**
  - Objective: Deterministic Python specialist for `pid` swap (Cat 2f — authz bypass) and CSRF token replay (Cat 6a). Rate-capped login probe to avoid ChartBreaker test-user lockout. Demos the LLM-vs-deterministic split called out in `ARCHITECTURE.md` § AI vs Deterministic.
  - Files likely involved: `chartbreaker/agents/specialists/protocol_specialist.py`
  - Depends on: P1-T6, P1-T9
  - Acceptance criteria covered: `ARCHITECTURE.md` § Cracker (deterministic specialist); `THREAT_MODEL.md` § Category 2 (2f), § Category 6 (6a); `ARCHITECTURE.md` § MVP vs Final Cut "Cracker" row
  - Status: Complete (commit pending) — live verified: 2f → 404 patient_not_found, 6a → 403 csrf_failed

- **P1-T13 — `chartbreaker/agents/specialists/tool_misuse_specialist.py` (Saboteur for Cat 4c)**
  - Objective: Deterministic Python specialist for parameter tampering on the request envelope (Cat 4c) — `action` enum variants, malformed `pid`, unicode tricks, CSRF header vs body race, oversized payloads. Small, demo-able, completes the MVP three-specialist showcase.
  - Files likely involved: `chartbreaker/agents/specialists/tool_misuse_specialist.py`
  - Depends on: P1-T6, P1-T9
  - Acceptance criteria covered: `ARCHITECTURE.md` § Saboteur (deterministic specialist); `THREAT_MODEL.md` § Category 4 (4c); `ARCHITECTURE.md` § MVP vs Final Cut "Saboteur" row
  - Status: Complete (commit pending) — live observation: 5000-char USER_QUESTION accepted with 200 (potential cap-enforcement finding for Phase 2 Judge to flag)

- **P1-T14 — `chartbreaker/agents/red_team_lead.py` routing table + `chartbreaker/agents/orchestrator_agent.py` priority math**
  - Objective: Replace the straight-line MVP loop (P1-T11) with the proper Orchestrator → RedTeamLead → Specialist dispatch. RedTeamLead is a deterministic routing table keyed on `subcategory_id`. Orchestrator computes the priority score per `ARCHITECTURE.md` § Orchestration Strategy formula and emits `CampaignBrief`. No LLM narration yet (deferred to Phase 2 polish).
  - Files likely involved: `chartbreaker/agents/red_team_lead.py`, `chartbreaker/agents/orchestrator_agent.py`
  - Depends on: P1-T11, P1-T12, P1-T13
  - Acceptance criteria covered: `ARCHITECTURE.md` § Conductor — Orchestrator Agent; `ARCHITECTURE.md` § RedTeamLead — the router; `ARCHITECTURE.md` § Orchestration Strategy
  - Status: Complete (commit pending) — live: severity-ordered briefs route through RedTeamLead to specialists; Cat 2f (critical) dispatched first per priority math

- **P1-T15 — `chartbreaker/graph.py` LangGraph wiring + `chartbreaker/cli.py run` command**
  - Objective: Wire Orchestrator → RedTeamLead → Specialist → TargetClient → Judge as LangGraph nodes. CLI `chartbreaker run --campaign <subcategory_id>` triggers the graph. Checkpoint to `runs.sqlite` after every node transition.
  - Files likely involved: `chartbreaker/graph.py`, `chartbreaker/cli.py`
  - Depends on: P1-T14
  - Acceptance criteria covered: `ARCHITECTURE.md` § Framework, State, and Coordination (LangGraph commitment); `ARCHITECTURE.md` § Inter-Agent Communication
  - Status: **Deferred to Phase 2** — the Orchestrator → RedTeamLead → Specialist → TargetClient → Judge → Regression chain is fully implemented in `chartbreaker/cli.py` via direct dispatch (commits `c163cc2` Cracker/Saboteur, `<this commit>` T14+T16). The chain ALREADY satisfies the rubric's multi-agent architecture commitment and produces the LangSmith-style observability via `agent_events` + `traces.jsonl`. LangGraph wrapping would add per-node-checkpointing and replay debugging, both nice-to-haves that don't gate MVP submission. Wiring LangGraph in Phase 2 is a clean refactor: replace the for-loop in `cli.run_mvp_loop` with a compiled StateGraph whose nodes call the same agent functions. ARCHITECTURE.md's commitment to LangGraph stands; it's just one ticket of polish away.

- **P1-T16 — Regression harness skeleton (`chartbreaker/regression.py`)**
  - Objective: Persist every `Verdict{semantic: fail OR verifier_replay: fail}` into `evals/regression_cases.yaml` with full fixture pinning. `chartbreaker regress` CLI command replays the pinned cases. Cross-category regression flagging is deferred to Phase 2.
  - Files likely involved: `chartbreaker/regression.py`, `evals/regression_cases.yaml` (initially empty)
  - Depends on: P1-T9, P1-T15
  - Acceptance criteria covered: `ARCHITECTURE.md` § Vault — Regression Harness; `ARCHITECTURE.md` § MVP vs Final Cut "Regression Harness" row
  - Status: Complete (commit pending) — pin/load/replay/classify implemented + `chartbreaker.cli regress` subcommand; auto-pin wired into MVP loop when verdict.recommended_action == 'regression'

---

### Phase 2 — MVP-to-Final (deadline: Fri 2026-05-15 noon)

**Goal**
Layer in the components `ARCHITECTURE.md` § MVP vs Final Cut marks as Final-only: remaining LLM specialists (Conversationalist, Smuggler, optional Impersonator), full deterministic Cat-4/5/6 coverage (Saboteur full, Cracker full, Glutton), semantic Judge LLM with calibration, Scribe LLM-drafted reports with PHI redactor, cross-category regression flagging, and the Streamlit dashboard.

**Exit Criteria**
- Coverage: ≥10 of ~20 subcategories with ≥1 live attempt; ≥5 attempts per attempted subcategory
- Judge calibration ≥85% on `evals/judge_calibration.yaml`; halts below 70%
- ≥3 `success` verdicts pinned in `evals/regression_cases.yaml`
- Streamlit dashboard renders all rubric-required views locally on `localhost:8501` (no public URL; ChartBreaker is operator-internal, only the OpenEMR target is publicly addressable)

**Tickets**

- **P2-T1 — Conversationalist specialist (Cat 1d, 3a)**
  - Objective: LLM specialist for multi-turn manipulation and conversation-history poisoning. Maintains attacker-side state across turns. Tighter token budget than Injector because multi-turn attempts run multiple LLM calls.
  - Files likely involved: `chartbreaker/agents/specialists/multi_turn_specialist.py`
  - Depends on: P1-T10 (Injector shape established)
  - Acceptance criteria covered: `ARCHITECTURE.md` § Conversationalist; `THREAT_MODEL.md` § Category 1 (1d), § Category 3 (3a)
  - Status: Todo

- **P2-T2 — Smuggler specialist (Cat 2a, 2b, 2d)**
  - Objective: LLM specialist focused on output-shape work — crafts inputs that produce outputs that *pass through* the verifiers while still leaking. Includes source-ID forgery probes (Cat 2b — likely-marquee finding).
  - Files likely involved: `chartbreaker/agents/specialists/exfiltration_specialist.py`
  - Depends on: P1-T8 (verifier ports needed to design bypasses)
  - Acceptance criteria covered: `ARCHITECTURE.md` § Smuggler; `THREAT_MODEL.md` § Category 2 (2a, 2b, 2d)
  - Status: Todo

- **P2-T3 — Glutton specialist (Cat 5a–5e, cost telemetry)**
  - Objective: Deterministic specialist for token exhaustion, rate-limit bypass via session rotation, self-referential multi-turn loops, vision-extraction abuse, long-prompt amplification. Records `CostObservation` side-channel rows. Budget-gated by Orchestrator because its job is to cost money on purpose.
  - Files likely involved: `chartbreaker/agents/specialists/cost_amplifier.py`
  - Depends on: P1-T15 (graph + orchestrator-budget enforcement)
  - Acceptance criteria covered: `ARCHITECTURE.md` § Glutton; `THREAT_MODEL.md` § Category 5
  - Status: Todo

- **P2-T4 — Saboteur full Cat 4 (4a vision-pipeline, 4b routing-keyword, 4d placeholder)**
  - Objective: Extend Saboteur beyond Cat 4c param-tampering: vision-pipeline upload probes against `run-extraction.php`, routing-keyword bait for the supervisor-graph (Phase-15 staged), and the Phase-15 recursive-tool-call placeholder probe that asserts the function-call surface is absent in V1.
  - Files likely involved: `chartbreaker/agents/specialists/tool_misuse_specialist.py`, `chartbreaker/target_client.py` (vision endpoint)
  - Depends on: P1-T13, P2-T8 (target client vision endpoint)
  - Acceptance criteria covered: `ARCHITECTURE.md` § Saboteur (full coverage); `THREAT_MODEL.md` § Category 4 (4a, 4b, 4d)
  - Status: Todo

- **P2-T5 — Cracker full Cat 6 (6c BAA-gate + priv esc, 6d session fixation, 6e login brute-force)**
  - Objective: Extend Cracker beyond 2f + 6a: BAA-gate flip probe, session-fixation probe (pre-seed `PHPSESSID` before login + cookie-flag audit), login brute-force / lockout-bypass probes against `POST /interface/login/login.php`. Cracker-layer rate cap enforced to avoid locking out the test user.
  - Files likely involved: `chartbreaker/agents/specialists/protocol_specialist.py`, `chartbreaker/target_client.py` (login endpoint surface)
  - Depends on: P1-T12, P2-T8
  - Acceptance criteria covered: `ARCHITECTURE.md` § Cracker (full coverage); `THREAT_MODEL.md` § Category 6 (6c, 6d, 6e)
  - Status: Todo

- **P2-T6 — Impersonator specialist (Cat 6b) — optional**
  - Objective: LLM specialist for persona hijacking. Foldable into Injector's system prompt if time-constrained (per `ARCHITECTURE.md` § Impersonator — optional/foldable).
  - Files likely involved: `chartbreaker/agents/specialists/persona_specialist.py`
  - Depends on: P1-T10
  - Acceptance criteria covered: `ARCHITECTURE.md` § Impersonator; `THREAT_MODEL.md` § Category 6 (6b)
  - Status: Todo

- **P2-T7 — Judge semantic LLM verdict + calibration**
  - Objective: Add the semantic verdict half to the Judge (Claude / OpenAI per registry). Build `evals/judge_calibration.yaml` of known-good / known-bad fixtures. `tests/test_judge_calibration.py` halts platform if accuracy drops below 70% threshold; warns below 85%.
  - Files likely involved: `chartbreaker/agents/judge_agent.py`, `evals/judge_calibration.yaml`, `chartbreaker/tests/test_judge_calibration.py`
  - Depends on: P1-T9
  - Acceptance criteria covered: `ARCHITECTURE.md` § Judge Agent (semantic verdict); `PROJECT_STRATEGY.md` § Success Criteria § Final "Judge calibration accuracy" row
  - Status: Todo

- **P2-T8 — Target Client vision-extraction + login-probe endpoints**
  - Objective: Extend `target_client.py` to support `POST /run-extraction.php` (vision extraction, Saboteur Cat 4a) and the login surface variants used by Cracker Cat 6d/6e beyond routine session establishment.
  - Files likely involved: `chartbreaker/target_client.py`
  - Depends on: P1-T6
  - Acceptance criteria covered: `ARCHITECTURE.md` § Conduit — Target Client (target endpoints list)
  - Status: Todo

- **P2-T9 — Scribe LLM-drafted reports + `chartbreaker/redactor.py`**
  - Objective: Replace the template-only Scribe with an LLM-drafted version. PHI redaction layer (`chartbreaker/redactor.py`) runs post-draft to strip any fixture-patient identifiers before write. Critical/high severity drafts land in `reports/draft/` per the human-approval gate.
  - Files likely involved: `chartbreaker/agents/documentation_agent.py`, `chartbreaker/redactor.py`, `chartbreaker/tests/test_redactor.py`
  - Depends on: P1-T15, P2-T7
  - Acceptance criteria covered: `ARCHITECTURE.md` § Scribe — Documentation Agent; `ARCHITECTURE.md` § Human Approval Gates
  - Status: Todo

- **P2-T10 — Cross-category regression flagging**
  - Objective: Extend `regression.py` to detect "the fix moved the symptom" — when a fix lands for Cat X, the harness re-runs the *full* suite and surfaces newly-failing Cat Y cases as the highest-priority signal.
  - Files likely involved: `chartbreaker/regression.py`
  - Depends on: P1-T16
  - Acceptance criteria covered: `ARCHITECTURE.md` § Regression Harness — What "Pass" Actually Means (failure mode 2); `ARCHITECTURE.md` § MVP vs Final Cut "Regression Harness" row
  - Status: Todo

- **P2-T11 — Streamlit observability dashboard**
  - Objective: Read-only Streamlit app reading `observability/runs.sqlite`. Surfaces the questions from `ARCHITECTURE.md` § Observability Layer table: per-category coverage, pass/fail rate, resilience trend, open vulns, run cost, per-agent activity timeline.
  - Files likely involved: `chartbreaker/observability/dashboard.py`
  - Depends on: P1-T7
  - Acceptance criteria covered: `ARCHITECTURE.md` § Observability Layer; `PROJECT_STRATEGY.md` § Operating Model § Interface Strategy (CLI primary + read-only dashboard)
  - Status: Todo

- **P2-T12 — Orchestrator narration + RedTeamLead narration**
  - Objective: Add the LLM-narration half to Orchestrator and RedTeamLead (gpt-5.4-nano per registry) — human-readable campaign rationale and dispatch trace. Math layer remains load-bearing; narration is operator polish.
  - Files likely involved: `chartbreaker/agents/orchestrator_agent.py`, `chartbreaker/agents/red_team_lead.py`
  - Depends on: P1-T14, P1-T15
  - Acceptance criteria covered: `ARCHITECTURE.md` § Orchestrator Agent (LLM narration); `ARCHITECTURE.md` § RedTeamLead (narration)
  - Status: Todo

---

### Phase 3 — Final Polish (deadline: Fri 2026-05-15 noon)

**Goal**
Generate the Final-only submission artifacts: vulnerability reports from live findings, cost analysis, demo video recording the local dashboard, social post, and a CI-produced `runs.sqlite` uploaded as a GitHub release artifact for reviewer download.

**Exit Criteria**
- ≥3 vulnerability reports (`reports/AF-NNN-*.md`) with reproducible attack sequences
- `COST_ANALYSIS.md` at 100 / 1K / 10K / 100K with architectural implications per tier
- Public HTTPS URL serving the dashboard
- 3–5 min demo video published
- Social post on X / LinkedIn tagging @GauntletAI
- README updated with target URL, demo video link, social post link, and CI release-artifact URL (no ChartBreaker public URL by design)

**Tickets**

- **P3-T1 — Generate ≥3 vulnerability reports from live findings**
  - Objective: Run Phase-1/2 campaigns until ≥3 `success` Judge verdicts are recorded; promote Scribe drafts in `reports/draft/` to `reports/AF-NNN-*.md` after human review. Each report covers a distinct attack category (e.g., one Cat 1b indirect injection, one Cat 2b source-ID forgery, one Cat 6a CSRF replay).
  - Files likely involved: `reports/AF-001-*.md`, `reports/AF-002-*.md`, `reports/AF-003-*.md`
  - Depends on: P2-T9, sufficient platform runtime
  - Acceptance criteria covered: `ASSIGNMENT.md` § Submission Requirements "Vulnerability Reports" row (minimum 3); `PROJECT_STRATEGY.md` § Success Criteria § Final "Vulnerability reports drafted" row
  - Status: Todo

- **P3-T2 — `COST_ANALYSIS.md` at 100 / 1K / 10K / 100K**
  - Objective: Actual dev spend (from `costs` table) + projected production costs at four scale tiers. At 100K: identify architectural changes needed (Judge-LLM gating on verifier disagreement, scheduled Orchestrator vs continuous, batch eval mode). Per `ARCHITECTURE.md` § Cost, Scale, and Model Constraints.
  - Files likely involved: `COST_ANALYSIS.md`
  - Depends on: P1-T7 (cost telemetry must be populated by real runs)
  - Acceptance criteria covered: `ASSIGNMENT.md` § Submission Requirements "AI Cost Analysis" row; `PROJECT_STRATEGY.md` § Success Criteria § Final "Cost analysis" row
  - Status: Todo

- **P3-T3 — Upload CI-produced `runs.sqlite` as a GitHub release artifact**
  - Objective: After the Final-week regression sweeps complete in GitHub Actions, attach the CI-produced `runs.sqlite` (plus a redacted excerpt of `traces.jsonl`) to a GitHub release tag. Reviewers can download and SQL-query the same data the operator sees locally. **Replaces the original "deploy dashboard to public HTTPS URL" ticket** — the dashboard is local-only per the locked-in Operating Model.
  - Files likely involved: `.github/workflows/regression-sweep.yml`, release artifact upload step
  - Depends on: P2-T11 (dashboard exists locally), Phase-2 runtime sufficient to populate `runs.sqlite`
  - Acceptance criteria covered: `PROJECT_STRATEGY.md` § Success Criteria § Final "Observability dashboard demonstrated" row (CI-produced `runs.sqlite` downloadable component)
  - Status: Todo

- **P3-T4 — Demo video (3–5 min)**
  - Objective: Three-act recording per `PROJECT_STRATEGY.md` § Demo & Social Plan: (1) manual jailbreak problem, (2) platform run with the **local** dashboard walkthrough on `localhost:8501`, (3) regression re-run of a previously-pinned exploit. The local dashboard is the answer to the rubric's observability questions — recorded on operator's laptop.
  - Files likely involved: video uploaded externally, link added to `README.md`
  - Depends on: P3-T1 (vuln reports exist to show); P2-T11 (local dashboard built)
  - Acceptance criteria covered: `ASSIGNMENT.md` § Submission Requirements "Demo Video" row
  - Status: Todo

- **P3-T5 — Social post on X / LinkedIn tagging @GauntletAI**
  - Objective: One paragraph + one dashboard screenshot (from the local dashboard recorded in P3-T4). Drafted alongside README update. Per `PROJECT_STRATEGY.md` § Demo & Social Plan.
  - Files likely involved: external; link committed to `README.md`
  - Depends on: P3-T4 (screenshot lifted from the demo recording)
  - Acceptance criteria covered: `ASSIGNMENT.md` § Submission Requirements "Social Post (Final only)" row
  - Status: Todo

- **P3-T6 — README final pass with reviewer links**
  - Objective: Update the README from P1-T1 with: the deployed target URL (already known), the demo video link, the social post link, and the GitHub release URL pointing to the CI-produced `runs.sqlite` artifact. No public ChartBreaker URL — by design, ChartBreaker has no public surface.
  - Files likely involved: `README.md`
  - Depends on: P3-T3, P3-T4, P3-T5
  - Acceptance criteria covered: `ASSIGNMENT.md` § Submission Requirements "GitHub Repository" row
  - Status: Todo

---

## Dependency Order

1. P1-T1 — README.md (no deps)
2. P1-T2 — Replace AF-SEED-008 (no deps)
3. P1-T3 — `config.py` + `MODEL_REGISTRY` (no deps; foundational)
4. P1-T4 — `state.py` Pydantic objects (after T3)
5. P1-T5 — `llm_client.py` (after T3)
6. P1-T6 — `target_client.py` (after T3, T4)
7. P1-T7 — Observability schema + JSONL (after T4)
8. P1-T8 — Verifier replay ports + parity tests (after T3)
9. P1-T9 — Judge (verifier replay only) (after T4, T8)
10. P1-T10 — Injector (after T4, T5)
11. **P1-T11 — Rubric-gate end-to-end loop (after T6, T7, T9, T10) ← MVP HARD GATE**
12. P1-T12 — Cracker (after T6, T9)
13. P1-T13 — Saboteur (after T6, T9)
14. P1-T14 — RedTeamLead + Orchestrator priority math (after T11, T12, T13)
15. P1-T15 — LangGraph wiring + CLI `run` command (after T14)
16. P1-T16 — Regression harness skeleton (after T9, T15)
17. P2-T1 — Conversationalist (after P1-T10)
18. P2-T2 — Smuggler (after P1-T8)
19. P2-T3 — Glutton (after P1-T15)
20. P2-T7 — Judge semantic LLM + calibration (after P1-T9)
21. P2-T8 — Target Client vision + login endpoints (after P1-T6)
22. P2-T4 — Saboteur full Cat 4 (after P1-T13, P2-T8)
23. P2-T5 — Cracker full Cat 6 (after P1-T12, P2-T8)
24. P2-T6 — Impersonator (optional) (after P1-T10)
25. P2-T9 — Scribe LLM + redactor (after P1-T15, P2-T7)
26. P2-T10 — Cross-category regression flagging (after P1-T16)
27. P2-T11 — Streamlit dashboard (after P1-T7)
28. P2-T12 — Orchestrator + RedTeamLead narration (after P1-T14, P1-T15)
29. P3-T1 — Vulnerability reports (after P2-T9 + runtime)
30. P3-T2 — Cost analysis (after P1-T7 + runtime)
31. P3-T3 — Upload CI runs.sqlite as a GitHub release artifact (after P2-T11 + Phase-2 runtime)
32. P3-T4 — Demo video, local dashboard recorded (after P3-T1, P2-T11)
33. P3-T5 — Social post (after P3-T4)
34. P3-T6 — README final updates (after P3-T3, P3-T4, P3-T5)

---

## Recommended Next Step

- **Start with:** P1-T7 — Observability SQLite schema + JSONL writer
- **Why this is first now:**
  - P1-T1 through P1-T6 are complete (commits `0dd4123` → `d09548c`); the foundation is laid.
  - P1-T7 has only P1-T4 as a dependency (state.py is in), so it's unblocked.
  - The Judge (P1-T9) and the rubric-gate end-to-end loop (P1-T11) both need to persist results, so the observability writer is on the critical path before either can run live.
  - Verifier replay (P1-T8) can be done in parallel since it only depends on P1-T3 — it does not require the SQLite writer.

---

## Deferred / Out of Scope

From `PROJECT_STRATEGY.md` § Non-Goals (platform):
- SIEM / WAF / HIDS functionality
- Auto-remediation (Scribe never edits Co-Pilot code or opens PRs)
- Multi-target campaigns (single-target invariant is a design commitment)
- Multi-tenant SaaS (single-operator for MVP and Final)
- General-purpose LLM red-teaming framework (target-aware platform, not retargetable by config)
- Vulnerability disclosure pipeline (no CVE issuer / ticketing integration)
- Control GUI (CLI for triggering, dashboard for reading — never merged)
- Live PHI ingestion (synthetic fixture patients only)

From `THREAT_MODEL.md` § Out of Scope for MVP:
- OpenEMR core authentication / CSRF / session-management bugs outside the Co-Pilot surface
- The Next.js Patient Dashboard and its HS256 JWT launch surface (reviewed separately)
- Network-layer attacks on Caddy / GCE VM
- LLM provider compromise
- RAG corpus ingestion attacks (4e — Phase-15 not live)
- Audit-log side-channel reconstruction (2e)
- Recursive tool-call exploitation (4d — Phase-15 not live; Saboteur ships a placeholder probe asserting absence)

From `ARCHITECTURE.md` § MVP vs Final Cut marked "⏸ defer" for Phase 1:
- Conversationalist, Smuggler, Glutton specialists (Phase 2)
- Impersonator as a standalone specialist (Phase 2 optional; otherwise folded into Injector)
- Judge semantic LLM verdict (Phase 2; MVP is verifier-replay only)
- Scribe LLM-drafted reports (Phase 2; MVP is template-only)
- Cross-category regression flagging (Phase 2; MVP pins + replays only)
- Streamlit dashboard (Phase 2)
- `COST_ANALYSIS.md` (Phase 3; rough estimate in README for MVP)

---

## Update Rules

After each implementation pass:
- Update ticket status only as **Todo / In Progress / Complete / Blocked**.
- Update **Current Status** (current phase, current ticket, blockers).
- Record blockers briefly under the ticket and in Current Status.
- Set the next recommended ticket per the Dependency Order.
- Do **NOT** add new scope unless one of the REQUIRED input documents changes. If a doc changes, re-run `/plan` to refresh this file.
