# `/evals/` — AgentForge Adversarial Test Suite

> **Companion docs:** [`../THREAT_MODEL.md`](../THREAT_MODEL.md), [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

This directory holds the AgentForge adversarial evaluation corpus. It is the
canonical store that satisfies the Stage 3 hard gate: "a working test suite
(./evals/) with results from at least three distinct attack categories."

## Files

| File | Purpose |
|------|---------|
| `schema.yaml` | The contract for every case. Documents every field. |
| `seed_cases.yaml` | Initial adversarial test cases. The Red Team specialists mutate from these. |
| `regression_cases.yaml` | Pinned exploits. Frozen test cases that run on every deploy. Written by the Regression Harness (the Vault) when the Judge confirms a `success` verdict. |
| `judge_calibration.yaml` | Known-good and known-bad response fixtures used to validate the Judge agent does not drift. Required for "test the tester" per the case study appendix. |
| `results/` | Per-run output: timestamped YAML/JSON files capturing the Judge's verdict and the raw target response for every case in a run. |

## Case lifecycle

```
seed_cases.yaml  →  Red Team specialist run (often via RedTeamLead routing)
                            │
                            ▼
                     Target Client (live deployed Co-Pilot)
                            │
                            ▼
                     Judge Agent (verifier replay + semantic verdict)
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
       success         partial            fail (safe)
            │               │
            │               ▼
            │       mutation_axes hint Red Team specialist
            │               │
            │               ▼
            │       new attempt in same campaign
            ▼
   add_to_regression: true?
            │
            ▼
   regression_cases.yaml (pinned with model + version + verdict snapshot)
            │
            ▼
   replayed on every deploy by the Regression Harness
```

A case is **retired** from `regression_cases.yaml` only by an explicit human
commit with `retired_at`, `retired_by`, and `retirement_reason` fields. The
Orchestrator cannot retire a case autonomously.

## How to add a new case

1. Pick the lowest unused `AF-SEED-NNN` id.
2. Copy a similar case from `seed_cases.yaml`.
3. Fill in **all** required fields from `schema.yaml`. Empty `observed_behavior`
   should be `pending` — it will be set by the next eval run.
4. Set `add_to_regression: false` until the case has produced at least one
   confirmed `success` verdict. The Vault promotes cases automatically; do not
   set `true` by hand unless you are pinning a known issue without running it.
5. Choose a specialist that matches the **attack shape**, not the threat
   category, per `ARCHITECTURE.md`:
   - prompt-craft → Injector / Conversationalist / Smuggler / Impersonator
   - protocol / fuzzing / cost → Saboteur / Cracker / Glutton
6. Validate the YAML parses: `python -c "import yaml; yaml.safe_load(open('evals/seed_cases.yaml'))"`.

## How to run the suite

```bash
# Run all seed cases against the live target (Stage 3 hard gate)
agentforge run --seed-only

# Run regression suite only (pinned cases)
agentforge regress

# Single case by ID
agentforge run --case AF-SEED-002

# Dry run — no live calls, deterministic replay only
agentforge run --seed-only --dry-run
```

Results are written to `evals/results/YYYY-MM-DD-HH-MM-SS.yaml` and
appended to the observability store at
`agentforge/observability/runs.sqlite`.

## Coverage at a glance (current)

| Category | Subcategories seeded | Specialist(s) |
|----------|----------------------|---------------|
| 1 — Prompt Injection | 1a, 1b, 1f | Injector |
| 2 — Data Exfiltration | 2b, 2f | Smuggler, Cracker |
| 5 — DoS / Cost | 5b | Glutton (Cracker covers MVP) |
| 6 — Identity / Role | 6a, 6b | Cracker, Impersonator (foldable into Injector for MVP) |

Categories 3 (state corruption) and 4 (tool misuse) have seeds planned for Final
— see `THREAT_MODEL.md` for the full surface map.
