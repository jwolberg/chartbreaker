# claims.md — ChartBreaker explainer

Every claim shown or spoken in the video. Sources are repo-relative `file:line` at commit `e2a8209`, or `work/graph.json`
node/community ids (labels from `work/.graphify_labels.json`). Graph degree = number of `links` entries touching a node.

## Chapter questions

- What is it: "Multi-agent adversarial evaluation platform for the OpenEMR Clinical Co-Pilot" (`README.md:3`).
- Who for: two operator personas (AppSec / Red Team Engineer, AI Platform Engineer) and three stakeholders who sign off (`USERS.md:10`).
- Why useful: it probes the Co-Pilot continuously and turns confirmed findings into a regression suite run on every deploy (`README.md:5`); manual prompting can't do this repeatably (`USERS.md:10`).
- Main parts: graph communities 12, 10, 5, 18, 3, 2 (below), in the order a run moves through them.
- Holds it together: `ObservabilityStore` has degree 57, the highest in `graph.json`, including whole-file nodes (`cli.py` 49).

## Claims

| id | claim (as shown or spoken) | chapter.beat | source |
|---|---|---|---|
| C1 | ChartBreaker is a "Multi-agent adversarial evaluation platform for the OpenEMR Clinical Co-Pilot" (title card, spoken, end card) | 1.1, 2.9 | `README.md:1,3` |
| C2 | It keeps attacking a deployed clinical chatbot, probing for prompt injection, patient data (PHI) leaks, tool misuse and more; on screen the six categories: prompt-injection, PHI-exfiltration, state-corruption, tool-misuse, DoS, identity | 1.2 | `README.md:5`; the target is a clinical chatbot: `chartbreaker/agents/judge_agent.py:228` |
| C3 | Built for the engineers who run the red team and for the people who sign off on the fixes (remediation); on screen the five personas | 1.3 | `USERS.md:10`, `USERS.md:18` |
| C4 | When the Judge flags an exploit, it is pinned as a regression test that runs on every deploy (quoted: "converts confirmed findings into a regression suite that runs on every deploy") | 1.4 | `README.md:5`; auto-pin when the verdict's `recommended_action` is `regression`: `chartbreaker/cli.py:156-159` |
| C5 | The Judge never sees the red team's reasoning; it checks each reply with deterministic verifiers, then optionally an LLM (quoted) | 1.5 | `README.md:88`; `chartbreaker/agents/judge_agent.py:326`; optional via `--semantic-judge`: `README.md:58`, `chartbreaker/cli.py:136-152` |
| C6 | A local dashboard lists every open vulnerability with both verdicts side by side (shown: the project's own dashboard screenshot, "Open vulnerabilities" table with Verifier and Semantic columns) | 1.6 | `docs/img/overview.png`; `chartbreaker/observability/dashboard.py:920-926` (table), `940-950` (legend), `1034,1039` (Verifier and Semantic columns); `README.md:70-73` |
| C7 | Code map cluster "Orchestrator Scheduling" with nodes `Orchestrator`, `.tick_next_brief()`, `._score()` | 2.2 | `graph.json` community 12; nodes `chartbreaker_agents_orchestrator_agent_orchestrator`, `..._orchestrator_tick_next_brief`, `..._orchestrator_score` |
| C8 | Each tick, the Orchestrator scores what's left and picks the next attack brief | 2.2 | `chartbreaker/agents/orchestrator_agent.py:138`, `180-185` |
| C9 | Code map cluster "Attack Briefs & Envelopes" with nodes `CampaignBrief`, `AttackAttempt`, `HttpRequestShape` | 2.3 | `graph.json` community 10; nodes `chartbreaker_state_campaignbrief`, `chartbreaker_state_attackattempt`, `chartbreaker_state_httprequestshape` |
| C10 | A campaign brief says what to attack; a specialist turns it into an attack attempt | 2.3 | `chartbreaker/state.py:57-58`, `108-113`; `chartbreaker/agents/red_team_lead.py:1`; `chartbreaker/cli.py:108-111` |
| C11 | Code map cluster "Target Client" with nodes `target_client.py`, `TargetClient`, `.dispatch()` | 2.4 | `graph.json` community 5; nodes `chartbreaker_target_client`, `chartbreaker_target_client_targetclient`, `chartbreaker_target_client_targetclient_dispatch` |
| C12 | The target client sends each attempt to the live Co-Pilot and captures its response | 2.4 | `chartbreaker/target_client.py:244-245`; `chartbreaker/state.py:143-144` |
| C13 | Code map cluster "Judge" with nodes `TargetResponse`, `judge_agent.py`, `judge_with_semantic()` | 2.5 | `graph.json` community 18; nodes `chartbreaker_state_targetresponse`, `chartbreaker_agents_judge_agent`, `chartbreaker_agents_judge_agent_judge_with_semantic` |
| C14 | The Judge scores the response deterministically first, then, when switched on, with a semantic LLM verdict | 2.5 | `chartbreaker/agents/judge_agent.py:324-326`; `chartbreaker/cli.py:136-152`; `README.md:58` |
| C15 | Code map cluster "Observability Store" with nodes `ObservabilityStore`, `._emit_event()` | 2.6 | `graph.json` community 3; nodes `chartbreaker_observability_store_observabilitystore`, `..._observabilitystore_emit_event` |
| C16 | The observability store is the canonical record of each run: a SQLite database plus a JSONL log (other logs, when enabled, go to separate files) | 2.6 | `chartbreaker/observability/store.py:57`; `README.md:78-79` |
| C17 | Code map cluster "CLI & Regression Suite" with nodes `run_regression_sweep()`, `cli.py`, `regression.py` | 2.7 | `graph.json` community 2; nodes `chartbreaker_cli_run_regression_sweep`, `chartbreaker_cli`, `chartbreaker_regression` |
| C18 | The CLI runs the loop; the regression suite replays the pinned exploits against the live target (retired cases excluded by default) | 2.7 | `chartbreaker/cli.py:106`; `chartbreaker/cli.py:455`; `chartbreaker/regression.py:1-8`, `122`|
| C19 | The most connected node in the whole graph is `ObservabilityStore` (degree 57; next are `cli.py` 49, `AttackAttempt` 46) | 2.8 | `graph.json` node `chartbreaker_observability_store_observabilitystore` |
| C20 | Every campaign, attempt, response and verdict goes through it | 2.8 | `chartbreaker/cli.py:107,112,134,153` (`write_campaign`, `write_attempt`, `write_target_response`, `write_verdict`), and the other run paths at `chartbreaker/cli.py:173-203`, `521-532`; `chartbreaker/observability/store.py:207,236,267,304` |
| C21 | Map edges shown between nodes | 2.2-2.8 | every drawn edge is a `graph.json` link between two shown node ids; `python3 work/build_ch2.py` prints the list (17 nodes, 19 edges: 4 method, 4 uses, 3 contains, 3 references, 2 calls, 2 imports, 1 imports_from). The 4 `uses` edges are INFERRED by graphify; the other 15 are EXTRACTED. Cross-cluster links are limited to those touching `ObservabilityStore` plus `.tick_next_brief()`–`CampaignBrief`, `.dispatch()`–`AttackAttempt`, `.dispatch()`–`TargetResponse` |
