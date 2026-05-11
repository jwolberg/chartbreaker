# ARCHITECTURE.md — AgentForge Multi-Agent Adversarial Evaluation Platform

> **Companion docs:** [`THREAT_MODEL.md`](./THREAT_MODEL.md) (what we attack), [`USERS.md`](./USERS.md) (who we serve), [`COST_ANALYSIS.md`](./COST_ANALYSIS.md) (what it costs).
> **Target system:** OpenEMR Clinical Co-Pilot module (`interface/modules/custom_modules/oe-module-clinical-copilot/`), deployed on GCE behind Caddy. The Next.js Patient Dashboard is reviewed separately and is **not** an AgentForge target.
> **Platform code:** AgentForge Python package — lives in its own repository, separate from OpenEMR/PHP.

---

## Target Deployment

The adversarial platform exercises a single live deployment. Targets are not configurable at runtime; the URL is hardcoded in `config.py` and overridable only via an explicit CLI flag (see § Human Approval Gates).

| Setting | Value |
|---------|-------|
| **Co-Pilot base URL** | `https://openemr.136-118-242-198.sslip.io` |
| **Login endpoint** | `https://openemr.136-118-242-198.sslip.io/interface/login/login.php?site=default` |
| **Co-Pilot API endpoint** | `https://openemr.136-118-242-198.sslip.io/interface/modules/custom_modules/oe-module-clinical-copilot/public/index.php?site=default` |
| **Site identifier** | `default` |
| **Auth model** | Dedicated AgentForge test user (NOT admin), provisioned with `patients:demo` ACL and access to a fixed set of fixture patients. Credentials live in `config.py` via env vars `AGENTFORGE_TARGET_USER` + `AGENTFORGE_TARGET_PASSWORD`. |
| **Session establishment** | `POST /interface/login/login.php?site=default` with form-encoded `authUser` + `clearPass` + `authProvider`; captures session cookie + initial CSRF token from the response |
| **CSRF discipline** | Every Co-Pilot POST carries `csrf_token` in body **and** mirrors it as `X-CSRF-Token` header per `CopilotController.php:259` |
| **Fixture patients** | Per-run pid list pinned in `config.py`; the dedicated test user has explicit ACL access to each. Cross-tenant pids used in Category 2f (authz bypass) attacks belong to a *different* test user — they are not the AgentForge user's patients. |

**Why a dedicated test user, not admin:**
- Vulnerability reports must reproduce under a *realistic* clinical user, not under credentials that bypass ACL by construction.
- The Cracker specialist's Category 2f (authz bypass) and 6c (privilege escalation) probes require a *non-privileged* baseline against which to escalate.
- Admin sessions skip several authorization checks that the Co-Pilot's PHP layer relies on; testing under admin would silently mask findings.

---

## Executive Summary

AgentForge is a multi-agent adversarial evaluation platform that continuously probes the OpenEMR Clinical Co-Pilot for vulnerabilities, validates whether confirmed exploits are reproducible, and converts them into a regression suite that runs on every deploy. It is built as a multi-agent system because the work decomposes naturally along trust boundaries: an agent that *generates* attacks has a conflict of interest with one that *evaluates* them, an agent that *prioritizes* coverage has different inputs than one that *documents* findings, and the *kinds* of attacks differ enough (prompt-craft vs protocol fuzzing vs cost amplification) that one attack generator cannot do all of them well. Collapsing those roles into a single agent — or a deterministic pipeline — produces a tester that flatters its own attacks and cannot adapt as the target changes. AgentForge instead separates them into four primary agents (`Orchestrator`, `RedTeamLead`, `Judge`, `Scribe`) plus a team of attack specialists routed by the RedTeamLead, each backed by a different LLM team or, where appropriate, by deterministic Python tooling. The seven-plus components communicate via a shared LangGraph state store and a SQLite-backed observability layer.

The control loop is driven by the **Orchestrator** (`orchestrator_agent.py`, OpenAI `gpt-5.4-nano` by default for narrative + deterministic Python for priority math; every LLM-driven role reads its `{provider, model}` from a central registry — see § Model Configuration — so swapping any role is a one-row config edit). On each tick, it reads the observability store — coverage per subcategory, recent verdicts, open Scribe reports, accumulated session cost — and emits a campaign brief: which threat-model subcategory to attack next, which seed case to start from, and how aggressively to mutate. The brief is passed to the **RedTeamLead** (`red_team_lead.py`), a router that dispatches to exactly one specialist based on the subcategory: LLM specialists (Injector, Conversationalist, Smuggler, Impersonator) handle prompt-craft work using uncensored open-weights fine-tunes dispatched via OpenRouter (default `cognitivecomputations/dolphin-mixtral-8x22b`; `nousresearch/hermes-3-llama-3.1-70b` is a configured alternative) because commercially-aligned frontier APIs refuse offensive workflows inconsistently and destroy reproducibility. OpenRouter is a request router, not a model provider — the underlying weights are open, and the specialists can be repointed to a local Ollama deployment via the model registry without code changes when self-hosted inference is preferred. Deterministic specialists (Saboteur for tool-misuse and parameter tampering, Cracker for authorization bypass / CSRF / session-fixation / login brute-force / privilege escalation / trust-boundary violations, Glutton for DoS and cost amplification) handle protocol- and fuzzing-shaped work where the case study explicitly notes traditional security tooling outperforms LLMs. The chosen specialist produces an `AttackAttempt`, which the RedTeamLead forwards to the **Target Client** (`target_client.py`), a thin HTTP wrapper around the deployed Co-Pilot's `briefing` and `followup` endpoints. The Target Client enforces session/CSRF discipline, captures the full response envelope (raw model output, post-verifier output, timing, token usage, audit log ID), and writes a trace row to the observability store.

The response is then routed to the **Judge** (`judge_agent.py`, OpenAI `gpt-5.4-nano` by default + deterministic verifier replay; the Judge model is the most likely role to be escalated to a stronger OpenAI model when calibration drift warrants it, swappable via the model registry). The Judge issues a verdict in two parts: (a) a *verifier-replay verdict* — does the raw model output survive `SourceAttributionVerifier` and `DomainConstraintVerifier` re-run in our own code, and is what remains still safe? — and (b) a *semantic verdict* — did the model actually violate the DATA-ONLY rule, regardless of post-scrub output? Disagreement between the two is itself a finding worth surfacing. Verdicts fan out to two consumers. The **Regression Harness** (`regression.py`) freezes every `success` verdict into a pinned test case (prompt + context fixture + model version + verdict snapshot) in `evals/regression_cases.yaml`, and runs the full suite whenever the Orchestrator triggers a regression sweep. The **Scribe** (`documentation_agent.py`, OpenAI `gpt-5.4-nano` by default — swappable for a stronger OpenAI model when Final-quality prose is required) takes the same verdict and drafts a vulnerability report in `reports/AF-NNN-*.md`, but does *not* auto-file or auto-submit it — critical/high severity drafts require human review before they leave the repo. That human gate is the deliberate trust boundary in an otherwise autonomous loop.

Everything observable — every prompt, every verdict, every cost dollar, every agent handoff — is written to `observability/runs.sqlite` and a streaming `traces.jsonl`. This is not afterthought logging; it is the substrate the Orchestrator reads on its next tick. Without it, the Orchestrator is blind and the platform devolves into random fuzzing. Cost is tracked per-agent, per-run, and per-campaign so that the Orchestrator can halt or de-prioritize when budget burns without signal. The platform's hard architectural commitment is that *every* autonomous decision is replayable from the trace store — a CISO must be able to ask "why did the platform attack this surface yesterday?" and get a deterministic answer.

---

## Agent Interaction Diagram

```
                       ┌─────────────────────┐
                       │  Orchestrator Agent  │
                       │  (Conductor)         │
                       │  OpenAI nano + Py    │
                       │  coverage + cost +   │
                       │  priority decisions  │
                       └──────────┬──────────┘
                                  │  campaign_brief (subcategory_id, mutation_budget)
                                  ▼
        ┌──────────────────────────────────────────────────────────┐
        │                    Red Team Team                          │
        │  ┌────────────────────────────────────────────────────┐   │
        │  │  RedTeamLead  (router only — no attack generation) │   │
        │  │  OpenAI nano narration + deterministic Python route│   │
        │  └────────────────────────┬──────────────────────────┘   │
        │                           │ dispatches to ONE specialist  │
        │                           ▼                               │
        │  ┌───────────────────────────┬──────────────────────────┐ │
        │  │  LLM specialists          │  Deterministic           │ │
        │  │  (OpenRouter: dolphin)    │  specialists (Python)    │ │
        │  │                           │                          │ │
        │  │  • Injector               │  • Saboteur              │ │
        │  │      Cat 1a/1b/1c/1e/1f   │      Cat 4a/4b/4c/4d     │ │
        │  │      + Cat 3e (poisoning) │      (tool misuse,       │ │
        │  │  • Conversationalist      │       param tampering)   │ │
        │  │      Cat 1d, 3a           │  • Cracker               │ │
        │  │  • Smuggler               │      Cat 2f, 6a, 6c,     │ │
        │  │      Cat 2a, 2b, 2d       │      6d, 6e (authz, CSRF,│ │
        │  │  • Impersonator (opt.)    │       session, brute-    │ │
        │  │      Cat 6b               │       force, priv esc)   │ │
        │  │                           │  • Glutton               │ │
        │  │                           │      Cat 5a–5e (DoS,     │ │
        │  │                           │       cost amplif'n)     │ │
        │  └───────────────────────────┴──────────────────────────┘ │
        └──────────────────────────────┬───────────────────────────┘
                                       │  attack_attempt
                                       ▼
                       ┌─────────────────────┐
                       │   Target Client      │
                       │   (Conduit)          │
                       │   deterministic HTTP │
                       │   live OpenEMR API   │
                       │   session + CSRF     │
                       └──────────┬──────────┘
                                  │  target_response
                                  ▼
                       ┌─────────────────────┐
                       │    Judge Agent       │
                       │    (Arbiter)         │
                       │    gpt-5.4-nano +    │
                       │    verifier replay   │
                       │    pass/fail/partial │
                       │    + severity        │
                       └──┬───────────┬───────┘
                          │           │
              verdict     │           │  verdict + report_request
                          ▼           ▼
        ┌─────────────────────┐   ┌────────────────────────┐
        │ Regression Harness  │   │ Documentation Agent     │
        │ (Vault)             │   │ (Scribe)                │
        │ deterministic Python│   │ gpt-5.4-nano            │
        │ pins exploits as    │   │ drafts AF-NNN-*.md      │
        │ regression cases    │   │ → human approval gate   │
        └──────────┬──────────┘   └───────────┬────────────┘
                   │                          │
                   │  regression_results      │  report_draft
                   ▼                          ▼
                       ┌─────────────────────┐
                       │ Observability Store │
                       │ runs.sqlite +       │
                       │ traces.jsonl +      │
                       │ LangSmith (optional)│
                       └──────────┬──────────┘
                                  │
                                  │  coverage, cost, verdicts
                                  ▼
                       ┌─────────────────────┐
                       │  Orchestrator (next │
                       │  tick reads state)  │
                       └─────────────────────┘
```

The loop closes via the observability store: Orchestrator writes the campaign brief there, Judge writes verdicts there, Regression Harness writes regression run results there, and Orchestrator reads all of it on its next tick. No agent calls another directly — they coordinate through the shared store and through the LangGraph state edges defined in `agentforge/graph.py`.

---

## Agent Roster

Each agent has a class name, a code-level handle, a model team, and explicit inputs/outputs/trust level.

### 1. Conductor — Orchestrator Agent
- **Class:** `OrchestratorAgent` in `agentforge/agents/orchestrator_agent.py`
- **Handle:** `orchestrator`
- **Model team:** OpenAI `gpt-5.4-nano` by default for narrative reasoning over coverage state + deterministic Python for the priority-score math. The math layer is the load-bearing one; the LLM layer is for human-readable campaign rationales in the observability log. Model is configurable per role via the registry (see § Model Configuration); any OpenAI-compatible endpoint (OpenAI, OpenRouter, Ollama, Anthropic) is a drop-in.
- **Inputs:**
  - `observability/runs.sqlite` — per-subcategory attempt count, success rate, last verdict, last regression timestamp
  - `evals/seed_cases.yaml` — canonical seed attacks per subcategory
  - `evals/regression_cases.yaml` — pinned exploits to re-verify
  - `config.py` — cost budget, target URL, model handles
- **Outputs:** `CampaignBrief` object → `{subcategory_id, seed_case_id, mutation_budget, rationale, max_cost_usd}`
- **Trust level:** **Trusted.** Has read-only access to all observability state. Can trigger Red Team runs and Regression Harness sweeps. Cannot file reports, cannot modify regression-case fixtures.
- **Failure modes:**
  - LLM returns malformed brief → fall back to deterministic priority math
  - Coverage gaps in every subcategory → choose by severity weight first, age second
  - Cost ceiling hit → emit `halt` brief, persist a snapshot for the next session

### 2. The Red Team Team — One Lead + Six Specialists

The Red Team is itself a multi-agent sub-system. Decomposing it along **attack shape** (not along threat-model subcategory) prevents specialist sprawl while keeping each agent's prompt focused. Specialists never call each other; the RedTeamLead routes a `CampaignBrief` to exactly one specialist, and any escalation across specialists is a *new* `CampaignBrief` from the Orchestrator on the next tick. This is what makes inter-agent decisions auditable rather than buried in a chain.

A deliberate design choice: **LLM specialists handle prompt-craft work; deterministic specialists handle protocol, fuzzing, and cost work.** This maps directly to the case study's guidance that "traditional non-AI security tooling may outperform LLMs in deterministic validation, replay testing, fuzzing, and protocol-level analysis."

#### 2.0 RedTeamLead — the router
- **Class:** `RedTeamLead` in `agentforge/agents/red_team_lead.py`
- **Handle:** `red_team_lead`
- **Model team:** Deterministic Python routing table keyed on `subcategory_id` + OpenAI `gpt-5.4-nano` (default) for one-sentence narration in the trace ("dispatching Cat 1b to Injector with mutation_budget=5"). Routing is *not* LLM-decided — it's a lookup. The LLM only narrates, never decides.
- **Inputs:** `CampaignBrief` from Orchestrator
- **Outputs:** `AttackAttempt` (after the chosen specialist returns)
- **Trust level:** **Trusted with routing only.** Cannot generate attacks itself; cannot bypass the routing table.
- **Failure modes:** Unknown `subcategory_id` → halt with an explicit error rather than guess.

#### 2.1 Injector (LLM specialist)
- **Class:** `InjectionSpecialist` in `agentforge/agents/specialists/injection_specialist.py`
- **Handle:** `injector`
- **Model team:** Uncensored open-weights fine-tune dispatched via OpenRouter. Default `cognitivecomputations/dolphin-mixtral-8x22b`; alternative `nousresearch/hermes-3-llama-3.1-70b`. Local Ollama deployment (`dolphin-mixtral:8x7b`, `llama3.1:8b`) is an in-registry fallback for air-gapped runs or environments where hosted access is constrained. **Commercially-aligned frontier models (OpenAI `gpt-*` general-purpose, Anthropic Claude, Google Gemini, Grok, base Qwen) are explicitly disallowed for this role** — they refuse offensive prompts inconsistently, which contaminates reproducibility. Uncensored fine-tunes are *trained* to remove the refusal layer; that is what makes them the right pick here, not their provenance.
- **Covers:** Categories **1a, 1b, 1c, 1e, 1f** (direct injection, indirect via chart text, vision-extracted injection, structured-output coercion, system-prompt extraction) and **3e** (context poisoning via injected chart-text premises).
- **Inputs:** `CampaignBrief` + the seed body from `evals/seed_cases.yaml`
- **Outputs:** `AttackAttempt` with `prompt` and/or `chart_text_payload` set
- **Trust level:** **Untrusted.** Output bounded to the attack envelope; all generation logged verbatim including refusals.
- **Failure modes:** Refusal → rotate model; off-topic generation → post-filter; loop → mutation budget cap.

#### 2.2 Conversationalist (LLM specialist)
- **Class:** `MultiTurnSpecialist` in `agentforge/agents/specialists/multi_turn_specialist.py`
- **Handle:** `conversationalist`
- **Model team:** Same offensive model as Injector (default `cognitivecomputations/dolphin-mixtral-8x22b` via OpenRouter), but with a different system prompt focused on planning a multi-turn arc (establish premise, build credibility, exploit late). Maintains its own *attacker-side* state across turns of a single attempt.
- **Covers:** Categories **1d** (multi-turn manipulation) and **3a** (conversation-history poisoning).
- **Inputs:** `CampaignBrief` + optional `parent_case_id` (for mutating a near-miss multi-turn arc)
- **Outputs:** `AttackAttempt` with `multi_turn_sequence` populated as an ordered list of user turns
- **Trust level:** **Untrusted.** Same constraints as Injector. Multi-turn budget capped per campaign.
- **Failure modes:** Specialist drifts off-script → narration check at each turn; turn count exceeds Co-Pilot's 10-pair cap → terminate.

#### 2.3 Smuggler (LLM specialist)
- **Class:** `ExfiltrationSpecialist` in `agentforge/agents/specialists/exfiltration_specialist.py`
- **Handle:** `smuggler`
- **Model team:** Same offensive model as Injector (OpenRouter default). System prompt focused on output-shape work: knows the `SourceAttributionVerifier` and `DomainConstraintVerifier` rules and crafts inputs that produce outputs that *pass through* them while still leaking. Distinct skill from Injector — Smuggler's goal is verifier survival, not system-prompt override.
- **Covers:** Categories **2a** (PHI in summary fields), **2b** (source-ID forgery), **2d** (vision-extracted PHI escape).
- **Inputs:** `CampaignBrief` + seed
- **Outputs:** `AttackAttempt`
- **Trust level:** **Untrusted.**
- **Failure modes:** Same as Injector.

#### 2.4 Saboteur (deterministic specialist)
- **Module:** `agentforge/agents/specialists/tool_misuse_specialist.py`
- **Handle:** `saboteur`
- **Model team:** **None — pure Python.** Parameter fuzzing and tool-pipeline probing is not prompt-craft.
- **Covers:** Categories **4a** (vision-extraction pipeline as tool-like surface), **4b** (supervisor-graph routing manipulation), **4c** (parameter tampering on the JSON envelope), **4d** (recursive tool calls — placeholder for Phase-15 surface).
- **Strategy:**
  - Envelope fuzzing: `action` enum variants, malformed `pid`, unicode tricks, CSRF header vs body race, oversized payloads
  - Vision-pipeline abuse: upload-shape probes against `run-extraction.php`
  - Routing-keyword bait: `USER_QUESTION` strings designed to force RAG path when Phase-15 ships
  - Phase-15 placeholder: assert function-call surface is absent in V1; once V2 ships, fuzz tool args
- **Inputs:** `CampaignBrief`
- **Outputs:** `AttackAttempt` carrying the specific envelope variant
- **Trust level:** **Trusted Python.** No LLM in the loop.
- **Failure modes:** New parameter shape introduced upstream → routing table must be updated; flagged as a coverage gap rather than silently skipped.

#### 2.5 Cracker (deterministic specialist)
- **Module:** `agentforge/agents/specialists/protocol_specialist.py`
- **Handle:** `cracker`
- **Model team:** **None — pure Python**, using libraries like `httpx`, `secrets`, `requests-toolbelt`.
- **Covers:** Categories **2f** (authz bypass via `pid`), **6a** (CSRF token replay / forgery against `CopilotController.php:259`), **6c** (privilege escalation: ACL bypass + BAA gate flip), **6d** (session fixation / cookie theft against the OpenEMR login flow), **6e** (login brute-force / lockout bypass).
- **Strategy:**
  - `pid` swap across authenticated sessions; deleted-pid replay; pid type confusion (Cat 2f)
  - CSRF token replay across the same session window; header-only vs body-only paths; missing-header bypass; token-rotation race during refresh (Cat 6a)
  - Session-fixation probe: pre-seed `PHPSESSID` before login, verify whether OpenEMR rotates the session ID on authentication; cookie-flag audit (`Secure`, `HttpOnly`, `SameSite`) (Cat 6d)
  - Login surface probes against `POST /interface/login/login.php?site=default`: rate-limit gaps, lockout-reset behavior, error-message timing oracle. Acts as an enabler for 6c/6d/2f — not a standalone exploit (Cat 6e)
  - BAA-gate probe: assert behavior before/after `copilot_baa_acknowledged` toggle (Cat 6c)
- **Inputs:** `CampaignBrief` + session credentials from `target_client`
- **Outputs:** `AttackAttempt` (often a sequence of HTTP requests rather than a single prompt)
- **Trust level:** **Trusted Python.** Has direct HTTP control of the Target Client — the most privileged specialist. Hardcoded to the `config.py` target URL only.
- **Failure modes:** A CSRF or session bypass succeeds in ways the Judge can't yet evaluate → escalate to human review automatically. Login brute-force is rate-capped at the Cracker layer (independent of any server-side throttle) so the platform does not accidentally lock the AgentForge test user out.

#### 2.6 Glutton (deterministic specialist)
- **Module:** `agentforge/agents/specialists/cost_amplifier.py`
- **Handle:** `glutton`
- **Model team:** **None — pure Python.** Generates pathological inputs and measures actual cost/latency impact.
- **Covers:** Categories **5a** (token exhaustion), **5b** (rate-limit bypass via session rotation), **5c** (infinite loops via recursive multi-turn), **5d** (vision-extraction abuse), **5e** (long-prompt amplification). Synthesis: **cost amplification**.
- **Strategy:**
  - Max-length `USER_QUESTION` at the 1000-char ceiling × pathological pattern
  - Parallel session rotation to defeat 30/min rate limit
  - Self-referential multi-turn that re-asks each turn
  - Large-PDF upload to the vision pipeline
  - Cost telemetry: $ per attack, latency p50/p95, tokens per response
- **Inputs:** `CampaignBrief`
- **Outputs:** `AttackAttempt` + a `CostObservation` side-channel record
- **Trust level:** **Trusted Python**, but **budget-gated** — Orchestrator caps Glutton spend per campaign because its job is to *cost money on purpose*.
- **Failure modes:** Glutton hits the platform's own cost ceiling → halts and surfaces the ratio of attacker cost to target cost as the finding.

#### 2.7 Impersonator (LLM specialist — optional, foldable)
- **Class:** `PersonaSpecialist` in `agentforge/agents/specialists/persona_specialist.py`
- **Handle:** `impersonator`
- **Model team:** Same offensive model as Injector (OpenRouter default).
- **Covers:** Category **6b** (persona hijacking).
- **Decision:** Whether to ship this separately or fold its prompt into Injector is an MVP tactical call (see table below). For Final, separate specialist gives cleaner coverage attribution; for MVP, folding is acceptable.

### 3. Conduit — Target Client (not an agent, by design)
- **Class:** `TargetClient` in `agentforge/target_client.py`
- **Handle:** `target_client`
- **Model team:** None — deterministic HTTP, intentionally.
- **Why not an agent:** The interface to the deployed Co-Pilot must be reproducible and free of nondeterministic decision-making. Wrapping it in an LLM-driven agent would obscure HTTP-layer failures and make session/CSRF debugging miserable. It is documented here so reviewers know it was a deliberate non-choice.
- **Target endpoints** (full URLs in § Target Deployment above):
  - `POST /interface/login/login.php?site=default` — session establishment
  - `POST /interface/modules/custom_modules/oe-module-clinical-copilot/public/index.php?site=default` — Co-Pilot `briefing` and `followup` actions
  - `POST /interface/modules/custom_modules/oe-module-clinical-copilot/public/run-extraction.php` — vision extraction (Saboteur Cat 4a probes)
  - `POST /interface/login/login.php?site=default` — additional login probes for Cracker Cat 6d (session fixation) and 6e (brute-force / lockout), beyond the routine session-establishment use already listed above
- **Responsibilities:**
  - Authenticate as the dedicated AgentForge test user (env-configured credentials); capture session cookie + CSRF token
  - Refresh the session when the cookie expires; the refresh is itself a logged trace event (relevant to Cat 5b rate-limit-bypass cases)
  - Dispatch Co-Pilot requests with body `csrf_token` AND mirrored `X-CSRF-Token` header per `CopilotController.php:259`
  - Capture: raw HTTP response, HTTP status, end-to-end latency, response token counts from provider headers, audit log ID if exposed in the response envelope
  - Enforce the single-target invariant: the configured base URL `https://openemr.136-118-242-198.sslip.io` is the only host the client will dispatch to (see § Human Approval Gates)
- **Failure modes:**
  - Network errors → retry with exponential backoff, then surface as a `target_unreachable` trace event (NOT as an attack verdict)
  - Session expiry → re-authenticate; cap retries so a permanently broken session does not loop
  - Rate-limit hit (HTTP 429) → logged as `rate_limited`, *NOT* retried; for Cat 5b runs this is the success signal itself
  - CSRF token rotation mid-run → re-fetch from the next response; cap retries

### 4. Arbiter — Judge Agent
- **Class:** `JudgeAgent` in `agentforge/agents/judge_agent.py`
- **Handle:** `judge`
- **Model team:** OpenAI `gpt-5.4-nano` by default for semantic verdicts + deterministic Python (regex/schema) replay of `SourceAttributionVerifier` and `DomainConstraintVerifier` lifted from the Co-Pilot module. **Caveat — known calibration risk:** the Co-Pilot target itself runs `gpt-5.4-mini` (per `THREAT_MODEL.md`); running a weaker-class Judge against a stronger-class target is a known accuracy risk on semantic verdicts. The nano default is a cost choice; operators are expected to escalate the Judge model in the registry to a stronger OpenAI model whenever the calibration set in `evals/judge_calibration.yaml` shows degraded accuracy. The verifier-replay half of the verdict is unaffected by this choice — it is pure Python.
- **Inputs:** `AttackAttempt` + `TargetResponse`
- **Outputs:** `Verdict` → `{verifier_replay: pass|fail, semantic: pass|partial|fail, severity: info|low|medium|high|critical, exploitability: trivial|easy|moderate|hard, rationale, recommended_action: regression|mutate|escalate|discard}`
- **Trust level:** **Independent and isolated from RedTeam.** The Judge never sees the RedTeam's reasoning or self-assessment — only the rendered attack and the target's response. This is the load-bearing trust boundary in the system: an attack-and-judge in the same context is compromised by design.
- **Validation of the Judge itself:** A small held-out ground-truth set (`evals/judge_calibration.yaml`) of known-good and known-bad attacks is replayed weekly; Judge accuracy below a threshold triggers a halt.
- **Failure modes:**
  - Judge agrees with everything ("yes that's a successful attack") → calibration set catches it
  - Judge drifts as target changes → verdicts pinned with target version; drift surfaces as regression noise
  - Judge uncertain → emits `partial` and recommends `mutate`; never auto-escalates to `critical` without verifier-replay agreement

### 5. Vault — Regression Harness (not an agent, deterministic)
- **Module:** `agentforge/regression.py` + fixtures in `evals/regression_cases.yaml`
- **Handle:** `regression`
- **Model team:** None — deterministic Python.
- **Why not an agent:** A regression test that "passes because the model's behavior changed" is worse than no test. Regression verdicts must be deterministic and version-pinned. An LLM-driven regression runner would re-introduce the drift problem we're trying to detect.
- **Responsibilities:**
  - Persist every `Verdict{semantic: fail (=successful exploit)}` into `regression_cases.yaml` with full fixture: prompt, context, target model + version, Judge verdict snapshot, observed output
  - On Orchestrator trigger (new deploy, time-based, ad-hoc): replay every regression case against the live target, run the same verifier replay, compare verdicts
  - Emit `RegressionReport` per run: which cases still exploit (vulnerability persists), which now pass (fix confirmed), which previously-passing cases now fail (a fix regressed something else — the highest-priority signal)
- **Failure modes:** A case that drifts because the model was upgraded silently is recorded but flagged for human triage, not auto-discarded.

### 6. Scribe — Documentation Agent
- **Class:** `DocumentationAgent` in `agentforge/agents/documentation_agent.py`
- **Handle:** `scribe`
- **Model team:** OpenAI `gpt-5.4-nano` by default. Operators typically escalate Scribe to a stronger OpenAI model (set in the registry) for Final-deliverable vulnerability reports where prose quality matters.
- **Inputs:** `Verdict{semantic: fail, severity: medium+}` + `AttackAttempt` + `TargetResponse` + the relevant `THREAT_MODEL.md` subcategory entry
- **Outputs:** A `reports/AF-NNN-<slug>.md` file with the required vulnerability-report sections (ID, severity, description, clinical impact, minimal reproducer, observed vs expected, remediation, status). The Scribe **drafts**, it does not **publish**.
- **Trust level:** **Drafts only.** No autonomous publishing, ticket creation, or remediation suggestion outside the report file. Critical-severity drafts are flagged for human review and held in a `reports/draft/` subdirectory until a reviewer moves them.
- **Failure modes:**
  - Confidently documents a false positive → reviewer rejects; the rejection is logged and feeds back into Judge calibration
  - Embeds real PHI from the target response → Scribe sees the live response by design; output is post-scrubbed by a deterministic redactor before write, and the redactor itself is unit-tested

---

## File Layout

The expanded layout, with annotations on additions beyond your sketch.

```
/openemr (existing repo root)
  /interface/modules/custom_modules/oe-module-clinical-copilot
  /apps/dashboard
  ...

/agentforge                          # all platform code, isolated from OpenEMR
  __init__.py
  cli.py                             # entrypoint: agentforge run|seed|regress|report
  config.py                          # target URL, model handles, budgets, secrets
  target_client.py                   # HTTP wrapper for Co-Pilot endpoints
  graph.py                           # ADDED — LangGraph state graph wiring all agents
  state.py                           # ADDED — typed state objects (CampaignBrief, AttackAttempt, TargetResponse, Verdict)
  llm_client.py                      # ADDED — OpenAI-compatible chat dispatcher; reads MODEL_REGISTRY, routes per-role
  regression.py                      # ADDED — deterministic regression runner (the Vault)
  redactor.py                        # ADDED — PHI redaction layer for Scribe output

  /agents
    __init__.py
    orchestrator_agent.py            # Conductor (OpenAI gpt-5.4-nano + Python by default)
    red_team_lead.py                 # CHANGED — routes briefs to specialists (was red_team_agent.py)
    judge_agent.py                   # Arbiter (OpenAI gpt-5.4-nano + verifier replay by default)
    documentation_agent.py           # Scribe (OpenAI gpt-5.4-nano by default)

    /specialists                     # ADDED — Red Team team
      __init__.py
      injection_specialist.py        # Injector (LLM, OpenRouter dolphin-mixtral by default)
      multi_turn_specialist.py       # Conversationalist (LLM)
      exfiltration_specialist.py     # Smuggler (LLM)
      persona_specialist.py          # Impersonator (LLM, optional/foldable)
      tool_misuse_specialist.py      # Saboteur (deterministic Python)
      protocol_specialist.py         # Cracker (deterministic Python)
      cost_amplifier.py              # Glutton (deterministic Python)

  /verifiers                         # ADDED
    source_attribution.py            # Python port of Co-Pilot's SourceAttributionVerifier
    domain_constraint.py             # Python port of Co-Pilot's DomainConstraintVerifier
    # These are the deterministic side of the Judge's verdict.

  /reports
    AF-001-phi-cross-patient.md
    AF-002-multiturn-prompt-injection.md
    AF-003-cost-amplification.md
    draft/                           # ADDED — critical/high drafts pending human review

  /observability
    runs.sqlite                      # canonical state store
    traces.jsonl                     # append-only event log
    schema.sql                       # ADDED — sqlite schema for the runs DB
    dashboard.py                     # ADDED — small Streamlit/FastAPI surface for humans

  /tests                             # ADDED
    test_verifiers.py                # the Python ports must match Co-Pilot behavior
    test_judge_calibration.py        # the Judge can't regress on the calibration set
    test_redactor.py                 # no PHI escape from the Scribe pipeline

/evals                               # MOVED to repo root to match Stage-3 hard gate
  README.md                          # how to add/run cases
  schema.yaml                        # field-by-field contract
  seed_cases.yaml                    # initial adversarial test cases (8 cases, 4 categories)
  regression_cases.yaml              # pinned exploits, written by the Vault
  judge_calibration.yaml             # known-good and known-bad fixtures for Judge accuracy
  results/                           # per-run output, written by the eval runner

/THREAT_MODEL.md                     # shipped
/ARCHITECTURE.md                     # this document
/USERS.md                            # TBD
/COST_ANALYSIS.md                    # TBD — Final deliverable
/README.md                           # TBD — setup, deployed URL, how to run AgentForge

```

**Expansions beyond your sketch** (and why each is justified):

| File | Why it's needed |
|------|-----------------|
| `graph.py` | Without an explicit LangGraph definition, agent handoff lives implicitly in `cli.py` and becomes untestable. |
| `state.py` | Typed Pydantic models for `CampaignBrief`, `AttackAttempt`, `TargetResponse`, `Verdict`. Inter-agent contracts in code, not docstrings. |
| `llm_client.py` | The model registry's only consumer. Reads `{role → provider, model}`, dispatches against OpenAI / OpenRouter / Ollama / Anthropic over the OpenAI-compatible chat-completion API. Lets us swap any role's model in one config-file edit. |
| `regression.py` | The Regression Harness is a hard requirement and is not an agent — it deserves its own module. |
| `redactor.py` | The Scribe sees live target responses, which may contain PHI from fixture patients. The post-write redactor is the safety net. |
| `/verifiers/` | The Judge's deterministic half is a Python port of the Co-Pilot's PHP verifiers. Isolating them in their own package keeps the parity testable. |
| `evals/judge_calibration.yaml` | Required to "validate the Judge itself" per the rubric. Without it, the Judge can drift silently. |
| `reports/draft/` | Trust boundary for critical/high severity — must not auto-publish. |
| `observability/schema.sql` | Make the sqlite layout explicit and version-controlled. |
| `observability/dashboard.py` | Humans need an observability surface. A small Streamlit or FastAPI app reading `runs.sqlite` is the cheapest path. |
| `/tests/` | The platform itself needs tests, especially around the Judge and the redactor. "Test the tester" per the appendix. |

---

## Inter-Agent Communication

Communication is mediated by **typed state objects** (Pydantic) flowing through a **LangGraph state graph**. No agent calls another via direct function invocation — every handoff is a graph edge, every payload is a serializable state object, every transition is logged to `traces.jsonl` and `runs.sqlite`.

**Message types** (defined in `state.py`):

```
CampaignBrief        Orchestrator → RedTeamLead
SpecialistRouting    RedTeamLead → one Specialist (deterministic dispatch)
AttackAttempt        Specialist  → TargetClient (via RedTeamLead aggregation)
TargetResponse       TargetClient → Judge
Verdict              Judge → Regression, Scribe, Observability
RegressionReport     Regression → Observability
ReportDraft          Scribe → Observability + filesystem
CostObservation      Glutton → Observability (side-channel, no Judge verdict needed)
```

**Why LangGraph (over CrewAI / AutoGen / custom):**
- The flow has cycles (Judge `partial` verdict → Orchestrator schedules mutation → RedTeam re-runs); CrewAI's role-based model fights cycles, AutoGen's group-chat model obscures them.
- LangSmith integration provides cross-agent traces, cost-per-node attribution, and replay for free. The rubric's observability requirements are otherwise hand-rolled work.
- State graph is testable in isolation — each agent's node function takes typed state and returns typed state, no global mutation.

**Why not direct function calls or message queues:**
- Direct calls couple agent implementations and make trust boundaries fuzzy.
- A real message queue (Redis, RabbitMQ) is operational overkill for MVP volume (10s–100s of attempts/hour, not thousands).

**State persistence:** LangGraph checkpoints to `observability/runs.sqlite` after every node transition. A crashed run is resumable; a completed run is replayable.

---

## Orchestration Strategy — How the Conductor Picks the Next Move

On each tick, the Orchestrator computes a priority score per `THREAT_MODEL` subcategory:

```
priority = (
    severity_weight                          # static, from THREAT_MODEL
  * (1 - coverage_ratio)                     # fewer attempts = higher priority
  * (1 + recent_target_change_signal)        # recent code change in Co-Pilot = boost
  * (1 - cost_burn_factor)                   # near-budget = de-prioritize expensive categories
  * regression_open_multiplier               # boost if a fix recently shipped
)
```

The top-scored subcategory selects a seed case; the mutation budget is set based on how many near-misses (`partial` verdicts) the subcategory has accumulated. The LLM layer narrates the choice ("focusing on Category 2b source-ID forgery because it's high-severity, 0% coverage, and a recent commit touched `SourceAttributionVerifier`") and the narration is logged as the campaign rationale.

**Trigger sources for the loop:**
- *Manual:* `agentforge run` from the CLI.
- *Time-based:* cron-triggered campaign every N hours (configurable; off by default in dev).
- *Deploy-triggered:* a webhook from the OpenEMR deploy pipeline kicks a regression-only sweep.
- *Cost-bounded:* every loop iteration checks accumulated cost against the campaign's `max_cost_usd`. Halts with a graceful summary written to the observability store.

**Coverage sufficiency:** a subcategory is considered "sufficiently covered" when (a) ≥10 distinct attack attempts have run, (b) the last 5 attempts have produced no new `success` or `partial` verdicts, and (c) at least 2 distinct mutation axes have been tried. The Orchestrator can override this in either direction and logs the override rationale.

---

## Regression Harness — What "Pass" Actually Means

Three failure modes a naive regression test misses:

1. **The model upgraded itself.** The exploit prompt now produces a different response not because the vulnerability was fixed, but because the model changed. Mitigated by pinning model + version in the regression case and re-recording verdict with explicit annotation when the model is intentionally bumped.
2. **The fix moved the symptom.** Defense X now blocks Category 1b but the same input now produces a Category 5 cost-amplification failure. Mitigated by running the *full* regression suite (not just the case under test) after every fix and surfacing newly-failing cases as the highest-priority signal.
3. **The test passes because of cosmetic output change.** The model still violates DATA-ONLY, but the post-verifier output looks safe. Mitigated by the Judge's two-part verdict — verifier-replay pass + semantic fail is a "false-confidence" verdict the harness flags explicitly.

A regression case is **retired** only by an explicit human action recorded in the case file (`retired_at`, `retired_by`, `retirement_reason`). The Orchestrator cannot retire a case autonomously.

---

## Observability Layer

The rubric demands answers to specific questions. Each one maps to a query against `runs.sqlite`:

| Question | Source |
|----------|--------|
| Which attack categories have been tested, and how many cases per category? | `attempts JOIN cases GROUP BY subcategory_id` |
| Current pass/fail rate per category and per target version? | `verdicts JOIN attempts WHERE target_version = ?` |
| Is the target becoming more or less resilient over time? | `verdicts` time-series faceted by subcategory |
| Which vulnerabilities are open / in-progress / resolved? | `reports` joined with latest regression run |
| How much did this run cost, and at what rate is cost scaling? | `cost_events` per agent per run |
| What is each agent doing, and in what order? | `traces.jsonl` replay |

`observability/dashboard.py` is a small Streamlit (or FastAPI + Jinja) surface that renders these queries for human operators. LangSmith captures the same data in its hosted UI for cross-agent trace debugging.

**Cost tracking** is per-agent, not just per-platform. Each LLM call writes a row with `agent`, `model`, `prompt_tokens`, `completion_tokens`, `usd`. The Orchestrator reads from this table to enforce per-campaign budgets.

---

## Human Approval Gates — Where the Platform Stops

The platform is autonomous up to four hard gates:

1. **Scribe critical/high drafts** land in `reports/draft/` and require a human to `git mv` them out. The CLI surfaces the diff and the reproducer for review.
2. **Regression retirement** requires a human commit with explicit reason — the Orchestrator cannot delete or skip a regression case.
3. **Target Client privileged actions** (uploading vision content, exercising admin-only endpoints, triggering BAA flag tests) require an explicit `--allow-privileged` flag on the CLI.
4. **Cross-target campaigns** are forbidden by design — the Target Client is hardcoded to a single base URL (`https://openemr.136-118-242-198.sslip.io`) in `config.py` and overrideable only via an explicit `--target-override` CLI flag that also requires `--i-understand-this-attacks-the-target` as a paired confirmation. This is what prevents the platform from being turned against systems it should not attack.

---

## AI vs Deterministic — Where Each Belongs

| Capability | Decision | Justification |
|------------|----------|---------------|
| Attack generation — prompt-craft (injection, multi-turn, exfiltration, persona) | **AI (OpenRouter uncensored fine-tune)** | Novelty and mutation diversity are exactly what LLMs do well; deterministic fuzzers cover a narrower space. Uncensored fine-tunes avoid the refusal layer that contaminates frontier-API output. → Injector, Conversationalist, Smuggler, Impersonator |
| Attack generation — protocol / fuzzing / cost (authz bypass, CSRF / session / brute-force, param tampering, DoS) | **Deterministic Python** | Case study guidance: "traditional non-AI security tooling may outperform LLMs in deterministic validation, replay testing, fuzzing, and protocol-level analysis." → Saboteur, Cracker, Glutton |
| Red Team routing | **Deterministic table + LLM narration** | Routing is a `subcategory_id` lookup, not a judgment call. LLM only narrates the choice in the trace. |
| Verifier replay | **Deterministic Python** | Must match Co-Pilot's PHP behavior byte-for-byte. Any LLM substitution introduces drift. |
| Semantic verdict | **AI (OpenAI, default `gpt-5.4-nano`)** | Reading whether a response *implies* a clinical recommendation while passing regex checks requires semantic judgment. Default is set for cost; registry escalates the Judge model when the calibration set shows degraded accuracy. |
| Severity scoring | **AI with deterministic clamp** | LLM proposes a level; a deterministic rubric in `judge_agent.py` clamps based on PHI presence / verifier verdict. |
| Coverage math | **Deterministic Python** | Simple aggregation; an LLM here is pure overhead. |
| Campaign rationale narration | **AI (OpenAI `gpt-5.4-nano`)** | Operator-readable explanations; nice-to-have, low-stakes. |
| Regression replay | **Deterministic Python** | A non-deterministic regression test is a contradiction. |
| Report drafting | **AI (OpenAI, default `gpt-5.4-nano`; escalate per registry)** | Coherent prose under a tight schema is the natural LLM sweet spot. Operators escalate from nano for Final-quality drafts. Human reviews before publish. |
| Report publishing | **Human only** | Trust boundary. |

---

## Framework, State, and Coordination

- **Language:** Python 3.12, Pydantic v2 for state objects, `httpx` for the Target Client (async ready for concurrent campaigns) and for all LLM dispatch. A thin `llm_client.py` speaks the OpenAI-compatible chat-completion surface, which is the shared interface for **OpenAI, OpenRouter, Ollama, and Anthropic** — every LLM-driven role reads `{provider, model}` from `MODEL_REGISTRY` in `config.py` (see § Model Configuration).
- **Coordination framework:** LangGraph 0.2+ for state graph, LangSmith for hosted traces (optional but recommended), SQLite (stdlib) for the canonical state store.
- **State management:** Two layers. LangGraph in-memory state for an active run; SQLite + JSONL on disk as the durable log. Nothing important lives only in process memory.
- **Concurrency model:** Single-threaded loop in MVP. Async-ready Target Client so a future Orchestrator can issue concurrent campaigns against the same target without code churn. Concurrency against the live target is rate-limit-gated by the Co-Pilot itself (30/min/session) — useful adversarial signal in its own right.

---

## Model Configuration — The Control Panel

Every LLM-driven role reads its `{provider, model}` from a central `MODEL_REGISTRY` dict in `config.py`. Swapping the model for any role — escalating the Judge from `gpt-5.4-nano` to a stronger OpenAI model for a Final-quality regression sweep, repointing the Injector from OpenRouter to a local Ollama instance for an air-gapped run, or trying a different uncensored fine-tune for one specialist — is a one-row config edit, not a code change. The dispatch layer (`agentforge/llm_client.py`) is a thin async wrapper over the OpenAI-compatible `/v1/chat/completions` surface, which **OpenAI, OpenRouter, Ollama, and Anthropic** all speak (Anthropic also offers its native `/v1/messages`, which we support but do not require).

**Default registry (MVP):**

| Role | Provider | Model | Why |
|------|----------|-------|-----|
| `orchestrator` | OpenAI | `gpt-5.4-nano` | Narration only; cheap by design |
| `red_team_lead` | OpenAI | `gpt-5.4-nano` | One-sentence dispatch trace |
| `injector` | OpenRouter | `cognitivecomputations/dolphin-mixtral-8x22b` | Uncensored fine-tune for offensive prompt-craft |
| `conversationalist` | OpenRouter | `cognitivecomputations/dolphin-mixtral-8x22b` | Same — multi-turn arc generation |
| `smuggler` | OpenRouter | `cognitivecomputations/dolphin-mixtral-8x22b` | Same — verifier-bypass shape work |
| `impersonator` | OpenRouter | `cognitivecomputations/dolphin-mixtral-8x22b` | Same — persona hijacking |
| `judge_semantic` | OpenAI | `gpt-5.4-nano` | Cost default; escalate when calibration degrades |
| `scribe` | OpenAI | `gpt-5.4-nano` | Cost default; escalate for Final-quality drafts |

**Supported providers (drop-in via `{base_url, api_key_env}`):**

| Provider | Base URL | Auth env var | Primary use |
|----------|----------|--------------|-------------|
| OpenAI | `https://api.openai.com/v1` | `OPENAI_API_KEY` | Judge, Scribe, Orchestrator, RedTeamLead defaults |
| OpenRouter | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | Offensive specialists (uncensored open-weights fine-tunes) |
| Ollama | `http://localhost:11434/v1` | (none) | Offensive specialists when self-hosted; air-gapped runs; cost-zero override |
| Anthropic | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` | Optional Judge/Scribe escalation if operator prefers Claude |

**OpenRouter ↔ Ollama is not a proxy relationship — it's a swap.** OpenRouter is a hosted aggregator that routes to upstream model providers; Ollama is a local inference server. Both expose OpenAI-compatible chat-completion endpoints, which is why `llm_client.py` treats them as drop-in equivalents. Switching a role from OpenRouter to Ollama is changing one `provider` field; no code path changes. Where it matters, the registry can also pin OpenRouter's `provider.order` to lock the upstream route for reproducibility.

**Why this design:**
- **Reproducibility:** every trace row writes the exact `{role, provider, model}` that produced it, so a regression case can be replayed against the same model that originally judged it. When a model changes, the regression case is flagged for human triage rather than silently re-verdicted.
- **Offensive/judge isolation:** offensive specialists never share an API key with the Judge or Scribe — they're on different providers by construction. This makes it cheaper to lock down or rotate offensive credentials independently.
- **Drop-in escalation:** if `gpt-5.4-nano` produces too many disagreements on the Judge's calibration set, the fix is a one-line registry edit, not a code change.
- **Cost discipline:** the Orchestrator's per-role cost telemetry already keys on `(role, provider, model)`; budget enforcement is unchanged when models swap.

**Why OpenRouter for offensive specialists, OpenAI for Judge / Scribe / Orchestrator:**
- OpenRouter's value here is *access to uncensored open-weights fine-tunes without operating GPU infrastructure*. We do not use OpenRouter to access frontier OpenAI / Anthropic / Google models — those route directly to their native APIs.
- OpenAI `gpt-5.4-nano` is the cheapest reasonable judge that runs on the same provider stack as the target. The Co-Pilot itself runs `gpt-5.4-mini`; co-locating Judge inference with the same provider family minimizes vendor-side behavior surprises during regression sweeps. The known weakness — a weaker-class Judge against a stronger-class target — is acknowledged on the Judge entry above and is the explicit reason the registry supports per-role escalation.
- Operators who prefer Claude for the Judge role flip one registry entry — no code change.

---

## Cost, Scale, and Model Constraints

Detailed numbers live in [`COST_ANALYSIS.md`](./COST_ANALYSIS.md). Architectural implications:

- **Local RedTeam model means RedTeam cost ≈ $0** at MVP scale (own hardware) or single-digit dollars on rented GPU. This is the single biggest cost-control decision in the design.
- **Hosted Judge LLM is the dominant variable cost** (even at the `gpt-5.4-nano` default). Mitigations: deterministic verifier-replay runs first (free), Judge LLM only fires on cases where verifier-replay disagrees with the attack's expected outcome. If operators escalate the Judge to a stronger OpenAI model in the registry for accuracy, this row grows accordingly and is the first place to look when projecting 10K+ run budgets.
- **Scribe runs only on confirmed exploits**, not on every attempt. Volume is bounded by `success`-rate, not by attempt-rate.
- **Orchestrator narration on `gpt-5.4-nano`** is cheap enough to ignore at any reasonable scale.
- **At 100K attempts:** the architecture continues to fit if (a) the Judge LLM is gated on verifier disagreement, (b) the Orchestrator runs as a scheduled job rather than continuously, (c) a small batch-evaluation mode is added to the Judge for replay-only regression sweeps.

---

## Known Tradeoffs and Open Risks

| Tradeoff | What we picked | What we gave up |
|----------|---------------|----------------|
| OpenRouter vs local Ollama for RedTeam | OpenRouter to uncensored open-weights fine-tunes (Ollama remains an in-registry fallback) | Hosted-provider availability and per-attempt cost vs free local inference (offset by zero local-GPU operational burden and the ability to flip back in one config line) |
| Frontier vs cheap Judge | `gpt-5.4-nano` default; registry-escalatable per role | Semantic-judgment accuracy when the Judge is weaker than the target (`gpt-5.4-mini`). Mitigated by verifier-replay gating, the calibration set, and one-line registry escalation when calibration degrades. |
| LangGraph vs hand-rolled | LangGraph | Vendor lock-in; learning curve. Justified by observability win. |
| SQLite vs Postgres | SQLite | Multi-process concurrency limits. Fine for MVP, swap later. |
| One target at a time | Hardcoded URL | Loss of multi-tenant testing. Deliberate safety choice. |
| Auto-publish reports | Human gate on critical/high | Slower turnaround. Defensible to a CISO. |

**Open risks the platform must surface, not hide:**
- The Judge can be wrong. The calibration set is small at MVP and grows over time.
- Uncensored fine-tunes (dolphin-mixtral-8x22b, hermes-3-llama-3.1-70b) are weaker than aligned frontier models at producing genuinely novel attacks. We accept this in exchange for the reliability of an unfiltered generation surface; we revisit if MVP coverage stalls.
- The Co-Pilot may change in ways that silently invalidate seed cases. Every seed case carries a target-version pin; mismatch fires a review.

---

## MVP vs Final Cut

The platform's component count is intentional; not every component ships by the Tuesday MVP gate. The split below is the work plan.

| Component | MVP (Tue) | Final (Fri) | Rationale |
|-----------|-----------|-------------|-----------|
| Orchestrator | ✅ priority math + minimal narration | ✅ full | Math is small; narration is cheap to add |
| RedTeamLead | ✅ routing table | ✅ + nano narration | Routing is a lookup; narration is polish |
| Injector | ✅ Cat 1a, 1b (highest demo value) | ✅ all Cat 1 + 3e | Indirect injection is the marquee finding |
| Conversationalist | ⏸ defer | ✅ Cat 1d, 3a | Multi-turn is the second-most-impressive finding; needs Judge calibration |
| Smuggler | ⏸ defer | ✅ Cat 2a, 2b, 2d | Verifier-bypass needs the verifier ports done first |
| Saboteur | ✅ Cat 4c (param tampering) — small Python | ✅ full Cat 4 | Parameter fuzzing is cheap; tool-pipeline probes later |
| Cracker | ✅ Cat 2f (pid swap) + Cat 6a (CSRF replay) | ✅ full Cat 6 (+ 6d session-fixation, 6e brute-force) | These two are short scripts and demo-able |
| Glutton | ⏸ defer | ✅ full Cat 5 | Cost telemetry needs Orchestrator budget enforcement first |
| Impersonator | ⏸ folded into Injector prompt | ⚖️ optional separate specialist | Cleanest coverage attribution if separated; not required |
| Target Client | ✅ briefing + followup endpoints | ✅ + vision + login probes | Vision-extraction path comes with Saboteur; login probes come with Cracker Cat 6d/6e |
| Judge — verifier replay | ✅ both verifiers ported | ✅ | Required for any verdict at all |
| Judge — semantic LLM | ⏸ partial (binary fail/pass only) | ✅ full (success/partial/fail + severity) | Calibration set needs time |
| Regression Harness | ✅ pin + replay | ✅ + cross-category regression flagging | The "fix moved the symptom" check |
| Scribe | ⏸ template-only drafts | ✅ LLM-drafted with PHI redaction (default `gpt-5.4-nano`; escalate via registry for Final reports) | Three vuln reports by Final is the bar |
| Observability — SQLite | ✅ schema + writers | ✅ + dashboard.py | Schema first, UI later |
| Observability — LangSmith | ✅ traces wired | ✅ + cost attribution | Free with LangGraph |
| Cost analysis | ⏸ rough estimate in README | ✅ COST_ANALYSIS.md at 100/1K/10K/100K | Final deliverable |

**MVP scope = three working specialists** (Injector LLM, Saboteur deterministic, Cracker deterministic) running through the full Orchestrator → RedTeamLead → Specialist → Target → Judge → Regression/Scribe loop with deterministic verifier-replay verdicts. This satisfies all four MVP hard gates while demonstrating the LLM-vs-deterministic split, the multi-agent architecture, and a real loop closing through the observability store.

---

## What This Architecture Buys You vs. A Pipeline

A linear pipeline (`generate → call target → check → report`) gets you a one-shot test runner. AgentForge gets you a *learning loop*: every verdict feeds priority math, every priority decision shapes the next attack, every confirmed exploit pins itself into the regression suite so a future fix has to clear it. The agent separation isn't ornamental — it is what prevents the most common failure modes (judge-drift, attack/judge collusion, attack-shape monoculture from a one-size generator, runaway cost, false-positive reports) that destroy single-agent or pipeline platforms when they're asked to run autonomously for weeks at a time. Splitting the Red Team into prompt-craft specialists and deterministic specialists, then routing through a deliberate lead, is what makes each finding traceable to the right kind of tooling — and is what lets a CISO defend the choice of when AI is in the loop and when it isn't.

That is the platform a hospital CISO is choosing whether to trust. This document is the case for it.
