# ChartBreaker — AI Cost Analysis

Per `docs/ASSIGNMENT.md` § Submission Requirements (AI Cost Analysis row)
and `docs/PROJECT_STRATEGY.md` § Success Criteria § Final.

This document reports actual development spend captured in
`observability/runs.sqlite` and projects platform cost across four scale
tiers (100, 1 K, 10 K, 100 K attempts). It identifies the architectural
changes required at the 100 K tier where the linear extrapolation breaks
down.

All figures are in USD and assume the default model registry in
`chartbreaker/config.py`:

| Role | Provider | Model | Price (per 1 M tokens, prompt / completion) |
|---|---|---|---|
| Orchestrator / RedTeamLead / Judge (semantic) / Scribe | OpenAI | `gpt-5.4-nano` | $0.150 / $0.600 |
| Injector / Conversationalist / Smuggler / Impersonator | OpenRouter | `nousresearch/hermes-3-llama-3.1-70b` | $0.300 / $0.300 |
| Saboteur / Cracker / Glutton | (deterministic) | — | $0 |

These prices are placeholders pinned in `chartbreaker/llm_client.py`
under `_PRICING_USD_PER_1M`. They are the operator's input — update them
when provider billing changes.

---

## 1. Development spend to date

Aggregated from `observability/runs.sqlite` across Phase-1 and Phase-2
development (every Phase-2 specialist + the semantic Judge).

| Metric | Value |
|---|---|
| Distinct runs | 7 |
| Attempts dispatched | 33 |
| LLM calls (specialist + judge) | 17 |
| Total prompt tokens | 8 267 |
| Total completion tokens | 1 143 |
| **Total dev cost (USD)** | **$0.0028** |

Per-LLM-call average: ~$0.00017. Per-attempt average across all
specialists (LLM + deterministic): ~$0.000086.

The deterministic specialists (Cracker, Saboteur) and the
manually-built Cat 5a probe contributed 16 of 33 attempts at $0
LLM-side cost; only HTTP latency from the target.

**Takeaway:** building the platform end-to-end across two phases cost
less than a third of a cent. The cost is dominated by single-call
specialist generations (one LLM call per attempt).

---

## 2. Per-attempt cost model

To project across scale tiers, we decompose per-attempt cost by
attack-shape. Empirically, the LLM-specialist branch dominates:

| Attack shape | Avg prompt tok | Avg completion tok | Avg cost per attempt |
|---|---|---|---|
| Injector (Cat 1) | 525 | 75 | $0.000180 |
| Smuggler (Cat 2a/2b/2d) | 300 | 35 | $0.000100 |
| Conversationalist (Cat 1d/3a, 2–4 turns) | 600 | 200 | $0.000240 |
| Saboteur (Cat 4a–4d) | — | — | $0 |
| Cracker (Cat 2f, 6a/6c/6d/6e) | — | — | $0 |
| Cat 5a manual probe | — | — | $0 |
| **Semantic Judge (when enabled)** | 1 000 | 60 | $0.000186 |

The semantic Judge runs *per attempt* when the `--semantic-judge` flag
is on. With it on, every attempt — LLM-specialist or deterministic —
costs at least ~$0.0002 for the Judge call.

Composite per-attempt cost with the default MVP-loop plan (14
subcategories, semantic Judge on, mix of LLM + deterministic
specialists):

```
~$0.00045 per attempt × 14 attempts per loop = ~$0.006 per full loop
```

A daily regression sweep that replays 30 pinned cases costs ~$0.006
worst-case (Judge + deterministic dispatch).

---

## 3. Cost projections by scale tier

| Tier | Attempts | Notes | Projected cost (USD) |
|---|---|---|---|
| **100** | 100 | One operator-driven afternoon. Roughly 7 full MVP-loop runs. | **$0.05** |
| **1 K** | 1 000 | One week of nightly regression sweeps + 2 operator deep-dives per day. | **$0.45** |
| **10 K** | 10 000 | A full quarter of continuous nightly sweeps + 5 deep-dives per week. | **$4.50** |
| **100 K** | 100 000 | Continuous campaign mode; multiple targets or multiple operators. | **$45 — but only with the architectural changes in § 4.** |

These figures assume:
- Default model registry (Hermes-3-70B specialists, gpt-5.4-nano control plane).
- Semantic Judge enabled on every attempt.
- Average 600 prompt + 100 completion tokens per LLM-specialist call.
- Average 1 000 prompt + 60 completion tokens per Judge call.

**Target-side cost (the Co-Pilot's own LLM bill) is NOT included** —
it's billed to the OpenEMR deployment, not ChartBreaker. At the 100 K
tier the target's bill matters more than ChartBreaker's. A separate
analysis on the OpenEMR side should pair with this one.

---

## 4. Architectural implications at 100 K

Linear extrapolation gives $45 for 100 K attempts, but two failure modes
break the assumption:

### 4a. Judge LLM cost dominates if not gated

At 100 K attempts, the Judge alone accounts for ~$18 (40 % of total).
The Judge is most useful precisely when the verifier-replay path is
*ambiguous* — when it disagrees with the PHP verifier or when raw
output is unstructured. Gating semantic-Judge dispatch on those
conditions reduces Judge cost by an estimated 60–80 %:

> Gate rule: dispatch the semantic Judge only when
> `verifier_replay == "fail"` **or** the PHP verifier reports `rewrite`
> or `reject` **or** the raw output failed to parse as JSON.

Estimated 100 K cost with Judge gating: **~$25**.

### 4b. Continuous Orchestrator becomes expensive without telemetry

The Orchestrator's priority math is currently deterministic (severity-
only). The roadmap (`PROJECT_STRATEGY.md` § Refreshed Immediate Gaps)
adds an LLM-narration half (P2-T12 — deferred). At 100 K attempts the
narration LLM call would land 100 K times — a meaningful $5–10 add.

Mitigation: emit narration only on *campaign transition* (subcategory
change), not per attempt. That drops narration calls by an estimated 10x.

### 4c. SQLite stops being sufficient

The current observability store is SQLite (single-writer). Around
~50 K attempts the WAL file becomes large enough that read latency on
the dashboard slows past interactive. `docs/PROJECT_STRATEGY.md`
§ Deliberately Deferred already calls out the Postgres migration; at
100 K the migration is no longer optional.

### 4d. Batch eval mode for nightly sweeps

At 100 K, running individual `chat()` calls leaves the OpenRouter /
OpenAI batch-mode discount on the table (50 % off on async batches).
Implementation: a `--batch-mode` flag that buffers per-campaign
generations and dispatches them as a single batch request per provider.

Estimated 100 K cost with batch mode + Judge gating: **~$15**.

---

## 5. What the operator should watch

The `chartbreaker calibrate` halt threshold (Judge accuracy < 70 %)
protects against drift, but cost-wise the operator should track:

| Signal | Where | Action if exceeded |
|---|---|---|
| Per-campaign budget | `chartbreaker/config.py` `BUDGETS.max_campaign_usd` (default $1.00) | Orchestrator already halts; no manual action |
| Per-run budget | `BUDGETS.max_run_usd` (default $5.00) | Orchestrator halts |
| Per-attempt cost spike | `costs.usd` rows in `runs.sqlite` | Investigate; usually a runaway multi-turn Conversationalist or a Glutton probe (P2-T3, deferred) doing its job too well |
| Calibration accuracy slip | `chartbreaker calibrate` output | Bump Judge model before scaling — at 100 K, a 1 % accuracy slip costs $0.50 in re-runs |

Live cost is queryable from the dashboard's "Cost by agent" panel
(P2-T11) and via SQL:

```sql
SELECT agent, COUNT(*) AS calls, ROUND(SUM(usd), 4) AS usd
FROM costs
GROUP BY agent
ORDER BY usd DESC;
```

---

## 6. Bottom line

| Question | Answer |
|---|---|
| What did building the platform cost? | $0.0028 across 7 runs / 33 attempts |
| What does one full MVP loop cost today? | ~$0.006 (~$0.00045 per attempt) |
| What does 100 K attempts cost? | $25 with Judge gating; $15 with Judge gating + batch mode |
| What's the cheapest meaningful improvement? | Gate the semantic Judge on verifier disagreement only (60–80 % Judge cost cut) |
| When does SQLite become a bottleneck? | ~50 K attempts; migrate to Postgres before then |

ChartBreaker is **cost-efficient by construction** — the LLM-vs-
deterministic split called out in `ARCHITECTURE.md` § AI vs
Deterministic means high-throughput protocol probes (Cracker, Saboteur)
cost nothing per attempt. The LLM specialists are reserved for the
attack surfaces that genuinely need creative generation.
