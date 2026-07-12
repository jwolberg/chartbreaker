# ChartBreaker

**Multi-agent adversarial evaluation platform for the OpenEMR Clinical Co-Pilot.**

ChartBreaker continuously probes a deployed Clinical Co-Pilot for prompt-injection, PHI-exfiltration, state-corruption, tool-misuse, DoS, and identity exploits — and converts confirmed findings into a regression suite that runs on every deploy. Built for Gauntlet AI Week 3.

> The deployed thing in this project is the **OpenEMR target**, not ChartBreaker. ChartBreaker is an operator-internal security tool with no public surface by design.

## Deployed target

| What | Where |
|---|---|
| OpenEMR Clinical Co-Pilot | `https://openemr.136-118-242-198.sslip.io` |
| Co-Pilot API endpoint | `…/interface/modules/custom_modules/oe-module-clinical-copilot/public/index.php?site=default` |
| Auth | Dedicated ChartBreaker test user (NOT admin) — credentials in `.env` |

## Documentation map

| Question | Doc |
|---|---|
| What does the platform attack, and what defenses exist? | [`THREAT_MODEL.md`](THREAT_MODEL.md) |
| How is the platform built? Agent roster, model registry, file layout. | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Operating model: hosting, DB, secrets, success criteria, SLOs, non-goals. | [`docs/PROJECT_STRATEGY.md`](docs/PROJECT_STRATEGY.md) |
| Who uses ChartBreaker and how. | [`USERS.md`](USERS.md) |
| Execution plan: phased tickets, dependencies, status. | [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) |

## Setup

**Requirements:** Python 3.10+ (validated against 3.10.10), `pip`, network access to OpenAI + OpenRouter APIs.

```bash
git clone https://github.com/jwolberg/chartbreaker.git
cd chartbreaker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
$EDITOR .env   # fill in the values below
```

### Required environment variables

| Variable | Purpose |
|---|---|
| `CHARTBREAKER_TARGET_USER` | Dedicated test-user username on the Co-Pilot target (NOT admin) |
| `CHARTBREAKER_TARGET_PASSWORD` | Test-user password |
| `OPENAI_API_KEY` | OpenAI key — used by Orchestrator / RedTeamLead / Judge / Scribe (default `gpt-5.4-nano`) |
| `OPENROUTER_API_KEY` | OpenRouter key — used by offensive specialists (default `nousresearch/hermes-3-llama-3.1-70b`) |

Optional: `ANTHROPIC_API_KEY` (if `MODEL_REGISTRY` is repointed), `LANGCHAIN_API_KEY` (LangSmith traces).

Configure which model handles which role in [`chartbreaker/config.py`](chartbreaker/config.py) — every role is a one-row swap to any OpenAI-compatible provider (OpenAI, OpenRouter, Ollama, Anthropic).

## Running ChartBreaker

```bash
# Run the full MVP loop across 14 attack subcategories against the live target.
# --semantic-judge enables the OpenAI gpt-5.4-nano Judge layered on top of
# verifier replay. --trace-llm-io captures every chat() request + response
# to observability/llm-trace-<run_id>.jsonl. --log-file tees Python logs to
# observability/run-<run_id>.log.
python -m chartbreaker.cli run-mvp-loop --semantic-judge --trace-llm-io --log-file

# Re-run the pinned regression suite against the live target
python -m chartbreaker.cli regress

# Replay the judge calibration set against the semantic Judge
python -m chartbreaker.cli calibrate

# Local read-only observability dashboard (Streamlit on localhost:8501).
# Open ?attempt_id=<id> for per-attempt drill-down; "Live activity" tab
# auto-refreshes every 2s during a run.
streamlit run chartbreaker/observability/dashboard.py
```

A single live run writes to:

- `observability/runs.sqlite` — canonical state store (8 tables, per [`docs/PROJECT_STRATEGY.md`](docs/PROJECT_STRATEGY.md) § Logging and State Store Requirement)
- `observability/traces.jsonl` — append-only event log; `tail -f | jq` for live debugging
- `observability/run-<run_id>.log` — verbose Python log capture (when `--log-file` is set)
- `observability/llm-trace-<run_id>.jsonl` — full LLM request + response per `chat()` call (when `--trace-llm-io` is set)

See [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) for the full guide to the four signal layers (stdout, Python logs, SQLite+JSONL store, Streamlit dashboard) and how to investigate a specific failing attempt.

## Architectural commitments

- **Multi-agent** by design: Orchestrator, RedTeamLead, seven specialists (4 LLM + 3 deterministic Python), Judge, Scribe. A single-agent or pipeline architecture does not satisfy the rubric.
- **Attack/Judge isolation**: the Judge never sees the RedTeam's reasoning, only the rendered attack and the target's response. The verdict has two parts: deterministic verifier replay + semantic LLM judgment.
- **Single-target invariant**: target URL hardcoded in `chartbreaker/config.py`. Runtime override requires both `--target-override` AND `--i-understand-this-attacks-the-target`.
- **No public ChartBreaker surface**: the dashboard runs on `localhost:8501`; CI-produced `runs.sqlite` is the reviewer-facing artifact (uploaded as a GitHub release attachment).
- **No live PHI**: fixture patients are synthetic; `redactor.py` runs pre-insert as defense in depth.

Full agent roster + interaction diagram in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Status

**Final-ready.** Phase 1 (MVP), Phase 2 (rubric-critical specialists + dashboard), and Phase 2.5 (observability expansion) complete. See [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) for full ticket history.

Coverage: 14 attack subcategories across 6 categories (Prompt Injection / Exfiltration / State Corruption / Tool Misuse / DoS / Identity). 7 specialists wired (4 LLM: Injector, Conversationalist, Smuggler, Impersonator-folded-into-Injector; 3 deterministic: Saboteur, Cracker, Glutton-deferred). Judge: deterministic verifier-replay + semantic gpt-5.4-nano with a calibration runner.

## Submission artifacts

| What | Where |
|---|---|
| Vulnerability reports (3) | [`reports/AF-001-indirect-injection-partial-dob-leak.md`](reports/AF-001-indirect-injection-partial-dob-leak.md), [`reports/AF-002-user-question-cap-not-enforced.md`](reports/AF-002-user-question-cap-not-enforced.md), [`reports/AF-003-session-cookie-missing-httponly.md`](reports/AF-003-session-cookie-missing-httponly.md) |
| AI cost analysis | [`COST_ANALYSIS.md`](COST_ANALYSIS.md) — actual dev spend + projections at 100 / 1K / 10K / 100K |
| Observability guide | [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) |
| CI-produced `runs.sqlite` | GitHub release tag `nightly` (automated via [`.github/workflows/regression-sweep.yml`](.github/workflows/regression-sweep.yml)) |
| Demo video (3–5 min) | _TBD — recording in progress_ |
| Social post (X / LinkedIn @GauntletAI) | _TBD_ |

## Observability

Four signal layers — see [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) for the full guide.

- **Stdout** — one-line summaries per attempt + verdict as the run progresses.
- **Python logs** — `--log-file` tees DEBUG-level logs to `observability/run-<run_id>.log`.
- **SQLite + JSONL store** — `observability/runs.sqlite` (8 tables) + `traces.jsonl` mirror; full per-LLM-call payloads when `--trace-llm-io` is on.
- **Streamlit dashboard** — `streamlit run chartbreaker/observability/dashboard.py` → `localhost:8501`. Includes a per-attempt drill-down (click any `attempt_id`), a 📡 Live activity tab that auto-refreshes every 2s during a run, sidebar rationale search, expandable agent-event timeline.

## License

This repository follows the OpenEMR license (GNU General Public License v3) for inherited OpenEMR code. ChartBreaker-original code under `/chartbreaker/` is licensed the same way for consistency.
