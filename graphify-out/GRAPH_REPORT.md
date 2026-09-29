# Graph Report - chartbreaker  (2026-09-28)

## Corpus Check
- 44 files · ~58,749 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 7 file(s) not represented in the graph (top: (none) 6, .ini 1)

## Summary
- 727 nodes · 1509 edges · 30 communities (27 shown, 3 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 96 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Product Docs & Threat Model
- Streamlit Dashboard
- CLI & Regression Suite
- Observability Store
- Judge Calibration & Evals
- Target Client
- LLM Client & LLM Specialists
- Run Audit
- Findings Publishing
- Documented Agent Roster
- Attack Briefs & Envelopes
- Auto-Run Loop
- Orchestrator Scheduling
- Proposal Review Tab
- Domain Constraint Verifier
- State Models
- Config & Source Attribution
- Proposal Harness
- Judge
- Glutton Specialist
- Campaign Planning & Routing
- Campaign Proposals
- Approved Batch Execution
- Dashboard Launch Controls
- Brief Runner
- Injection Output Parsing
- Login Probe Budget
- Rationale Narrator

## God Nodes (most connected - your core abstractions)
1. `ObservabilityStore` - 57 edges
2. `AttackAttempt` - 46 edges
3. `CampaignBrief` - 45 edges
4. `TargetClient` - 26 edges
5. `HttpRequestShape` - 22 edges
6. `CostObservation` - 21 edges
7. `chat()` - 19 edges
8. `main()` - 19 edges
9. `propose()` - 19 edges
10. `TargetResponse` - 18 edges

## Surprising Connections (you probably didn't know these)
- `Cracker (deterministic specialist)` --calls--> `TargetClient`  [EXTRACTED]
  ARCHITECTURE.md → chartbreaker/target_client.py
- `Judge two-part verdict (verifier replay + semantic)` --semantically_similar_to--> `Attack/Judge isolation`  [INFERRED] [semantically similar]
  THREAT_MODEL.md → README.md
- `Human approval gates` --rationale_for--> `TargetClient`  [EXTRACTED]
  ARCHITECTURE.md → chartbreaker/target_client.py
- `RedTeamLead (router)` --calls--> `TargetClient`  [EXTRACTED]
  ARCHITECTURE.md → chartbreaker/target_client.py
- `TargetClient` --shares_data_with--> `Judge Agent (Arbiter)`  [EXTRACTED]
  chartbreaker/target_client.py → ARCHITECTURE.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Core adversarial control loop** — architecture_orchestrator, architecture_red_team_lead, chartbreaker_target_client_targetclient, architecture_judge, architecture_observability_store [EXTRACTED 0.95]
- **Judge two-path verdict combination** — architecture_judge, architecture_verifier_replay, architecture_semantic_judge, architecture_disagreement_tag [EXTRACTED 0.95]
- **How the tester is verified** — architecture_calibration, architecture_regression_ci_gate, architecture_platform_self_tests, architecture_human_approval_gates [INFERRED 0.75]
- **Judge verdict: verifier replay + semantic check against soft defenses** — threat_model_judge_verdict_shape, threat_model_source_attribution_verifier, threat_model_domain_constraint_verifier, threat_model_data_only_rule [EXTRACTED 0.95]
- **Eval case lifecycle: seed to regression pin** — evals_readme_seed_cases, evals_readme_case_lifecycle, evals_readme_vault_regression_harness, evals_readme_regression_cases, threat_model_judge_verdict_shape [EXTRACTED 0.95]
- **Observability stack over runs.sqlite** — docs_observability_runs_sqlite, docs_observability_traces_jsonl, docs_observability_agent_events_timeline, docs_observability_streamlit_dashboard, docs_observability_audit_run [EXTRACTED 0.85]

## Communities (30 total, 3 thin omitted)

### Community 0 - "Product Docs & Threat Model"
Cohesion: 0.05
Nodes (61): Observability Guide, agent_events timeline table, audit-run post-run checks, Four signal layers (stdout, logging, SQLite+JSONL, dashboard), Investigate a failing attempt recipe, Live activity tab, --trace-llm-io LLM trace, Plan Next Run tab (proposal harness) (+53 more)

### Community 1 - "Streamlit Dashboard"
Cohesion: 0.07
Nodes (54): altair, cache_data, Chart, _apply_rationale_filter(), _check_api_key_prereqs(), _decorate(), _detect_in_flight_run(), _empty_state_check() (+46 more)

### Community 2 - "CLI & Regression Suite"
Cohesion: 0.06
Nodes (49): argparse, ArgumentParser, _attach_run_log(), _audit_run_cli(), _build_cat_5a_attempt(), _build_parser(), _classify_regress_summary(), _detach_run_log() (+41 more)

### Community 3 - "Observability Store"
Cohesion: 0.07
Nodes (20): _iso(), _json_dump(), ObservabilityStore, datetime, Path, Add v2 columns to pre-existing DBs created under schema v1. SQLite's CREATE…, Bring v2 DBs up to v3 (Phase-4: proposed_campaigns table). The table itself is…, Append one JSON line to traces.jsonl. Best-effort; logs but never raises. (+12 more)

### Community 4 - "Judge Calibration & Evals"
Cohesion: 0.07
Nodes (38): _aggregate_per_subcategory(), _attempt_from_record(), CalibrationResult, CalibrationSummary, _classify_accuracy(), _load_records(), Any, Path (+30 more)

### Community 5 - "Target Client"
Cohesion: 0.08
Nodes (28): Target deployment (OpenEMR Clinical Co-Pilot), AsyncClient, base64, get_target_credentials(), Read the dedicated ChartBreaker test-user credentials from env., CrossTargetError, _extract_allowed_source_ids(), _extract_csrf_token() (+20 more)

### Community 6 - "LLM Client & LLM Specialists"
Cohesion: 0.10
Nodes (34): generate(), _parse_prompt(), RuntimeError, Smuggler — LLM specialist for verifier-bypass exfiltration. Covers THREAT_MODEL…, Produce one Smuggler AttackAttempt for the brief's subcategory., LLM output could not be parsed into a Smuggler AttackAttempt., SmugglerGenerationError, generate() (+26 more)

### Community 7 - "Run Audit"
Cohesion: 0.10
Nodes (33): audit_all(), audit_run(), AuditFinding, AuditReport, check_acl_breach(), check_agent_looping(), check_budget_overrun(), check_disagreement_spike() (+25 more)

### Community 8 - "Findings Publishing"
Cohesion: 0.09
Nodes (24): _publish_findings_cli(), Implementation of ``chartbreaker publish-findings``. Late-imports the tracker…, issue_body(), issue_title(), labels_for_case(), publish_cases(), PublishResult, Any (+16 more)

### Community 9 - "Documented Agent Roster"
Cohesion: 0.08
Nodes (34): ARCHITECTURE.md (ChartBreaker), AI vs deterministic split, Judge calibration set and runner, Conversationalist (LLM specialist), COST_ANALYSIS.md, Cracker (deterministic specialist), DISAGREEMENT tag, DomainConstraintVerifier port (+26 more)

### Community 10 - "Attack Briefs & Envelopes"
Cohesion: 0.13
Nodes (32): _baa_gate_flip_attempt(), _csrf_missing_header_attempt(), generate(), _login_brute_force_attempt(), _pid_swap_attempt(), Cracker — deterministic protocol specialist. Covers THREAT_MODEL: - Cat 2f:…, Cat 6a: include csrf_token in body but suppress the X-CSRF-Token header.…, Cat 6c: post a Co-Pilot briefing with injected admin-claim headers. The… (+24 more)

### Community 11 - "Auto-Run Loop"
Cohesion: 0.13
Nodes (19): asyncio, auto_run_loop(), auto_run_loop_sync(), AutoRunState, default_stop_file_path(), is_auto_run_active(), _is_pid_alive(), Any (+11 more)

### Community 12 - "Orchestrator Scheduling"
Cohesion: 0.14
Nodes (14): Orchestrator, priority_score(), Stateful per-tick scheduling controller. Usage: orch = Orchestrator(store,…, True if there are remaining subcategories AND budget is not exhausted., Re-score remaining subcategories and return the highest-priority brief. Returns…, attempts_in_subcategory_this_run / TARGET_ATTEMPTS_PER_SUBCATEGORY. Capped at…, run_cost_so_far / budgets.max_run_usd, capped at 1.0., 1.0 if the target_version changed between the two most-recent runs. Looking… (+6 more)

### Community 13 - "Proposal Review Tab"
Cohesion: 0.15
Nodes (22): _on_budget_change(), _on_check_all(), _on_generate(), _on_reject(), _on_stop_auto_run(), _open_store(), Streamlit "Plan Next Run" tab — human-approval queue UI for the Orchestrator…, Mirror the master 'Check all' state into every per-row checkbox. Reads the new… (+14 more)

### Community 14 - "Domain Constraint Verifier"
Cohesion: 0.11
Nodes (20): _build_scan_text(), _detect_violations(), DomainConstraint, DomainVerificationResult, Python port of OpenEMR's `DomainConstraintVerifier`. PHP source:…, Verify a structured response against the V1 domain constraints., V1 constraint set mirrored from PHP enum DomainConstraint., Outcome of running DomainConstraintVerifier on a structured response. (+12 more)

### Community 15 - "State Models"
Cohesion: 0.13
Nodes (19): BaseModel, SQLite + JSONL observability store for ChartBreaker. Every agent state-…, _Frozen, MultipartFile, _new_id(), datetime, Typed Pydantic state objects passed between ChartBreaker agents. These are the…, The Judge's two-part verdict per ARCHITECTURE § Judge Agent. `verifier_replay`… (+11 more)

### Community 16 - "Config & Source Attribution"
Cohesion: 0.11
Nodes (19): get_provider_api_key(), get_role_config(), ProviderConfig, ChartBreaker configuration: target deployment, model registry, budgets. Single-…, Return the {provider, model} pair for a role., Look up the API key for a provider. None if provider needs no auth., An OpenAI-compatible chat-completion endpoint., Which provider + model handles which agent role. (+11 more)

### Community 17 - "Proposal Harness"
Cohesion: 0.20
Nodes (18): approve(), get_proposal(), list_all(), list_approved(), list_pending(), ProposedCampaign, Orchestrator Approval Harness — human-in-the-loop queue for proposed attack…, Return rows with status='proposed', most-recent first. (+10 more)

### Community 18 - "Judge"
Cohesion: 0.18
Nodes (16): judge(), judge_with_semantic(), _parse_semantic_verdict(), Judge Agent — Phase 2 (verifier replay + semantic LLM verdict). The Phase 1…, Render the AttackAttempt into the Judge's view of what was sent., Render the TargetResponse output for the Judge., Parse the Judge LLM's JSON output into (semantic, rationale)., Run the deterministic Judge, then layer on the semantic LLM verdict. Returns… (+8 more)

### Community 19 - "Glutton Specialist"
Cohesion: 0.15
Nodes (15): generate(), RuntimeError, Glutton — deterministic DoS / cost-amplification specialist. Covers…, Cat 5c: multi-turn sequence engineered to grow context superlinearly. Turn 1…, Glutton refused to emit another session-rotation probe (DoS guard)., Consume one rotation-probe token or raise. Process-local — does not persist., Test helper. Resets the process-local counter., Produce one cost-amplification probe for the brief's subcategory. (+7 more)

### Community 20 - "Campaign Planning & Routing"
Cohesion: 0.17
Nodes (11): _plan_entries(), plan_initial_briefs(), _PlanEntry, Orchestrator — per-tick scheduling controller. Per docs/ARCHITECTURE.md §…, One row of the MVP plan. Internal — public surface is CampaignBrief., Emit the MVP-loop campaign brief list, severity-only ordered. Kept for tests…, Reverse-lookup helper used by plan_initial_briefs sort key., _severity_for_subcategory() (+3 more)

### Community 21 - "Campaign Proposals"
Cohesion: 0.15
Nodes (11): Return the specialist that owns this subcategory. Raises on unknown., specialist_for(), _estimate_cost(), _new_proposal_id(), propose(), Project the LLM spend of running ``mutation_budget`` attempts. Reads the live…, Deterministic fallback used when LLM narration fails., Generate a fresh slate of up to ``n`` proposed campaigns. Parameter selection… (+3 more)

### Community 22 - "Approved Batch Execution"
Cohesion: 0.25
Nodes (9): BriefRunner, _on_launch(), _operator_label(), Approve every checked row, then execute the resulting batch., User-visible operator string written to decided_by / runs.operator., execute_approved_batch(), execute_approved_batch_sync(), Execute every currently-approved proposal as one new run. Each approved row is… (+1 more)

### Community 23 - "Dashboard Launch Controls"
Cohesion: 0.25
Nodes (8): _check_api_key_prereqs(), _on_start_auto_run(), Popen, Sticky-ish footer with total cost + launch button., Same prereq set as the existing run-test launch button., Shell out to `python -m chartbreaker.cli auto-run-loop ...`. Detached so a…, _render_launch_footer(), _spawn_auto_run()

### Community 24 - "Brief Runner"
Cohesion: 0.33
Nodes (6): dispatch(), Route a CampaignBrief to the correct specialist and return the result. Returns…, Execute one CampaignBrief end-to-end: route → dispatch → judge → record., _run_one_brief(), _default_brief_runner(), Production brief runner: open a TargetClient and dispatch one brief. Reuses…

### Community 25 - "Injection Output Parsing"
Cohesion: 0.40
Nodes (5): InjectionGenerationError, _parse_llm_output(), RuntimeError, Raised when the LLM output cannot be parsed into an AttackAttempt., Parse the LLM's JSON response into (prompt, chart_text_payload).

### Community 26 - "Login Probe Budget"
Cohesion: 0.40
Nodes (5): LoginProbeBudgetExhausted, RuntimeError, Cracker refused to emit another login probe to protect the test user., Consume one login-probe token or raise. Process-local — does not persist., _spend_login_probe_token()

## Knowledge Gaps
- **19 isolated node(s):** `Conversationalist (LLM specialist)`, `Glutton (deterministic specialist)`, `Impersonator (optional LLM specialist)`, `DomainConstraintVerifier port`, `PHI redactor` (+14 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 309 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ObservabilityStore` connect `Observability Store` to `CLI & Regression Suite`, `LLM Client & LLM Specialists`, `Attack Briefs & Envelopes`, `Auto-Run Loop`, `Proposal Review Tab`, `State Models`, `Proposal Harness`, `Judge`, `Campaign Planning & Routing`, `Campaign Proposals`, `Approved Batch Execution`, `Brief Runner`?**
  _High betweenness centrality (0.121) - this node is a cross-community bridge._
- **Why does `TargetClient` connect `Target Client` to `CLI & Regression Suite`, `Documented Agent Roster`, `Attack Briefs & Envelopes`, `Proposal Harness`, `Judge`, `Brief Runner`?**
  _High betweenness centrality (0.114) - this node is a cross-community bridge._
- **Why does `AttackAttempt` connect `Attack Briefs & Envelopes` to `CLI & Regression Suite`, `Observability Store`, `Judge Calibration & Evals`, `Target Client`, `LLM Client & LLM Specialists`, `State Models`, `Judge`, `Glutton Specialist`, `Campaign Planning & Routing`, `Brief Runner`?**
  _High betweenness centrality (0.080) - this node is a cross-community bridge._
- **Are the 19 inferred relationships involving `ObservabilityStore` (e.g. with `_run_cat_5a_probe()` and `_run_one_brief()`) actually correct?**
  _`ObservabilityStore` has 19 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `AttackAttempt` (e.g. with `judge()` and `judge_with_semantic()`) actually correct?**
  _`AttackAttempt` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 23 inferred relationships involving `CampaignBrief` (e.g. with `Orchestrator` and `dispatch()`) actually correct?**
  _`CampaignBrief` has 23 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `TargetClient` (e.g. with `_run_cat_5a_probe()` and `_run_one_brief()`) actually correct?**
  _`TargetClient` has 5 INFERRED edges - model-reasoned connections that need verification._