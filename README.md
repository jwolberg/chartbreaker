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
| What does the platform attack, and what defenses exist? | [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) |
| How is the platform built? Agent roster, model registry, file layout. | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Operating model: hosting, DB, secrets, success criteria, SLOs, non-goals. | [`docs/PROJECT_STRATEGY.md`](docs/PROJECT_STRATEGY.md) |
| Who uses ChartBreaker and how. | [`docs/USERS.md`](docs/USERS.md) |
| Execution plan: phased tickets, dependencies, status. | [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) |
| Original assignment / rubric. | [`docs/ASSIGNMENT.md`](docs/ASSIGNMENT.md) |

## Setup

**Requirements:** Python 3.10+ (validated against 3.10.10), `pip`, network access to OpenAI + OpenRouter APIs.

```bash
git clone ssh://git@labs.gauntletai.com:22022/jwolberg/chartbreaker.git
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
| `OPENROUTER_API_KEY` | OpenRouter key — used by offensive specialists (default `cognitivecomputations/dolphin-mixtral-8x22b`) |

Optional: `ANTHROPIC_API_KEY` (if `MODEL_REGISTRY` is repointed), `LANGCHAIN_API_KEY` (LangSmith traces).

Configure which model handles which role in [`chartbreaker/config.py`](chartbreaker/config.py) — every role is a one-row swap to any OpenAI-compatible provider (OpenAI, OpenRouter, Ollama, Anthropic).

## Running ChartBreaker

> The CLI commands below land progressively across Phase 1. See [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) for current ticket status.

```bash
# Run a targeted campaign against one threat-model subcategory
python -m chartbreaker.cli run --campaign cat-1b-indirect-injection --mutation-budget 10

# Run the seed suite once across every category (the rubric MVP path)
python -m chartbreaker.cli run --seed-only

# Re-run the pinned regression suite against the live target
python -m chartbreaker.cli regress

# Replay a previously-recorded run deterministically against stored fixtures
python -m chartbreaker.cli run --replay <run_id>

# Local read-only observability dashboard (Streamlit on localhost:8501)
streamlit run chartbreaker/observability/dashboard.py
```

A single live run writes to:

- `observability/runs.sqlite` — canonical state store (8 tables, per [`docs/PROJECT_STRATEGY.md`](docs/PROJECT_STRATEGY.md) § Logging and State Store Requirement)
- `observability/traces.jsonl` — append-only event log; `tail -f | jq` for live debugging
- `evals/results/YYYY-MM-DD-HH-MM-SS.yaml` — per-run YAML snapshot for submission artifacts

## Architectural commitments

- **Multi-agent** by design: Orchestrator, RedTeamLead, seven specialists (4 LLM + 3 deterministic Python), Judge, Scribe. A single-agent or pipeline architecture does not satisfy the rubric.
- **Attack/Judge isolation**: the Judge never sees the RedTeam's reasoning, only the rendered attack and the target's response. The verdict has two parts: deterministic verifier replay + semantic LLM judgment.
- **Single-target invariant**: target URL hardcoded in `chartbreaker/config.py`. Runtime override requires both `--target-override` AND `--i-understand-this-attacks-the-target`.
- **No public ChartBreaker surface**: the dashboard runs on `localhost:8501`; CI-produced `runs.sqlite` is the reviewer-facing artifact (uploaded as a GitHub release attachment).
- **No live PHI**: fixture patients are synthetic; `redactor.py` runs pre-insert as defense in depth.

Full agent roster + interaction diagram in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Status

Pre-MVP. Phase 1 implementation in progress per [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md). See [`docs/PROJECT_STRATEGY.md`](docs/PROJECT_STRATEGY.md) § Refreshed Immediate Gaps for the current ticket list.

## License

This repository follows the OpenEMR license (GNU General Public License v3) for inherited OpenEMR code. ChartBreaker-original code under `/chartbreaker/` is licensed the same way for consistency.
