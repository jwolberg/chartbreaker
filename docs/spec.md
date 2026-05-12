# Feature Spec — Orchestrator Approval Harness

> **Status:** Draft. Authored 2026-05-12. Build-plan-ready.
> **Owner:** ChartBreaker platform.
> **Companion docs:** [`ARCHITECTURE.md`](./ARCHITECTURE.md) (extend § Human Approval Gates), [`OBSERVABILITY.md`](./OBSERVABILITY.md) (new table + new tab), [`PROJECT_STRATEGY.md`](./PROJECT_STRATEGY.md) § Operating Model (Streamlit-on-laptop confirmed surface).

---

## Feature Request

**Original request:** Build a human-in-the-loop approval queue for proposed attack campaigns. After a run completes, the Orchestrator should emit a slate of proposed next campaigns; the operator reviews them in Streamlit, optionally tweaks the mutation budget per row, approves a subset, and fires the approved batch as the next run.

---

## Problem Statement

**What this solves.** Today the Orchestrator decides what to attack next entirely autonomously inside `run-mvp-loop`. There is no surface between *"this run finished"* and *"the next run starts"* where a human can review the slate, drop bad ideas, double down on near-misses, or steer compute. Every campaign either runs or doesn't — there is no partial approval.

**Who experiences it.** The ChartBreaker operator (the engineer running the platform on their laptop or via the local Streamlit dashboard). Today they have two options: (a) let the loop run unattended, or (b) hand-author CampaignBriefs by code. There is no middle ground.

**Why it matters.**
- **Steering.** Between runs, the operator has context the platform doesn't (yesterday's PR, a regression they're hunting, a near-miss they want to push on). They need a way to apply that context without code edits.
- **Cost.** A 6-campaign batch can burn $0.40+. Reviewing the slate before launch prevents wasted compute on low-value campaigns.
- **Symmetry with existing approval gate.** The architecture already requires human review of Scribe-drafted vulnerability reports going *out*. This adds the symmetric gate on Orchestrator briefs going *in* — a posture the assignment rubric explicitly rewards.

---

## Target User and Workflow

**Primary user.** ChartBreaker operator — the engineer running `streamlit run chartbreaker/observability/dashboard.py` on `localhost:8501` and `chartbreaker run-mvp-loop` from the CLI. Single user, single laptop, single target.

**Current workflow.**
1. Operator runs `chartbreaker run-mvp-loop` from CLI.
2. Loop decides + executes 6 campaigns autonomously based on `plan_initial_briefs()`.
3. Operator reviews results in dashboard after the fact.
4. To steer the next run, operator must either re-run autonomous (gets a near-duplicate slate) or write code.

**Desired workflow.**
1. Operator opens dashboard → **Plan Next Run** tab.
2. Clicks **Generate proposals** → backend writes ≥1 `ProposedCampaign` rows with `status='proposed'`.
3. Operator reads each row's rationale, optionally edits mutation budget, ticks the rows they approve.
4. Sees total estimated cost for the selected batch.
5. Clicks **Launch approved batch** → only approved rows execute. Unselected stay `proposed` (still pending). Rejected rows are tombstoned with `status='rejected'`.
6. After the launched run completes, approved rows transition to `status='executed'` with their `run_id` recorded.
7. On the next dashboard open, the queue still reflects current state — proposed rows still pending, executed rows visible as history.

---

## Success Condition

The feature is successful when the operator can, in a single dashboard session:

1. Generate a fresh slate of proposed campaigns.
2. See per-row rationale, default mutation budget, and per-row cost estimate.
3. Approve a subset (not all) with optional mutation-budget tweaks.
4. Launch exactly the approved subset.
5. Verify in the same dashboard that approved rows ran and rejected rows didn't.
6. Close and re-open the dashboard later and find pending proposals still there.

Plus: the existing autonomous `chartbreaker run-mvp-loop` CLI path continues to work unchanged (this feature is additive).

---

## Scope

### In scope

- New SQLite table `proposed_campaigns` with a schema migration.
- New Python module `chartbreaker/orchestrator/proposal_harness.py` providing the proposal lifecycle API.
- New Streamlit module `chartbreaker/observability/proposal_tab.py` rendering the queue (NOT inline in `dashboard.py`).
- Integration into `dashboard.py` `main()` as a new top-level tab labeled **Plan Next Run**.
- Reuse of `plan_initial_briefs()` priority math from `chartbreaker/agents/orchestrator_agent.py`.
- LLM-narrated rationale per proposal via the existing Orchestrator agent (configurable model per the central registry).
- Reuse of `_run_one_brief()` from `chartbreaker/cli.py` for batch execution — no new run path.
- Status lifecycle: `proposed → approved → executed`, or `proposed → rejected`. Terminal states are `executed` and `rejected`.
- Tests: unit tests for the harness lifecycle (propose / approve / reject / execute), schema-migration round-trip test, and a Streamlit smoke test that loads the tab without error against a seeded DB fixture.

### Out of scope (deferred follow-ups)

- Auto-generation of proposals on run-complete (manual button only for MVP).
- Per-row editing beyond mutation budget — seed swap, specialist swap, subcategory change, rationale edit.
- CLI subcommand `chartbreaker propose`. **Stretch only**: include if it falls out of the harness module trivially (≤30 LOC), otherwise defer.
- Slack / email / GitHub notifications when proposals are pending.
- Streaming live updates while the launched batch is mid-flight (use existing Live Activity tab).
- Multi-operator concurrency — single operator on single laptop is the assumed model.
- Re-proposing rejected proposals (rejection is terminal for MVP).
- Editing or un-rejecting proposals after a decision.

---

## Constraints

### Technical

- **SQLite, single-writer.** The dashboard reads `runs.sqlite` while the CLI may write to it. New table must respect the existing single-writer convention (writes only from the harness, reads from Streamlit). Use the same connection pattern as existing tables in `chartbreaker/observability/store.py`.
- **Schema migration must be additive.** No alterations to existing 8 tables. Bump `schema_version` per the existing convention.
- **`dashboard.py` is already ~1800 lines.** All new rendering logic must live in `chartbreaker/observability/proposal_tab.py`. `dashboard.py` only imports and registers the tab.
- **Streamlit re-runs the whole script on every interaction.** Approval/rejection clicks must write through to SQLite immediately — do not rely on `st.session_state` as the source of truth for decisions.
- **Existing run path must not change.** `run-mvp-loop` CLI continues to work without proposals; `_run_one_brief()` signature is unchanged.
- **Priority math is load-bearing for replayability.** Parameter selection (subcategory, specialist, seed, mutation budget) is deterministic from `plan_initial_briefs()`. The Orchestrator LLM call writes prose only — never overrides parameters.
- **Model registry compliance.** The LLM call for rationale narration reads `{provider, model}` from the existing central model registry. No hardcoded model strings.
- **Cost estimate must be deterministic.** Estimate is computed from `mutation_budget × est_tokens_per_attempt × $/token` for the specialist's configured model. No live LLM probing.

### Product

- **Single-user, local-only.** No multi-tenant concerns; no auth gate; no audit-of-the-auditor surface.
- **Approval is by the operator.** `decided_by` field is captured as `os.getenv("USER")` or `"laptop:<host>"` — no separate identity system.
- **Persistence across sessions is required.** Proposals must survive `streamlit run` restarts and `chartbreaker` CLI restarts.
- **No accidental autonomy regression.** When the operator runs `chartbreaker run-mvp-loop` from CLI, it must not silently pick up `approved` proposals from the queue. The two paths are separate. The approval queue executes only via its own "Launch approved batch" code path (or a new explicit CLI flag, deferred).

### Existing pattern constraints

- Follow the existing dataclass + SQLite row pattern from `chartbreaker/observability/store.py`.
- Tests live under `chartbreaker/tests/` and use `pytest`; aim for ~10 new tests, matching existing test density.
- Logging uses `logging.getLogger(__name__)` per the OBSERVABILITY.md convention.

---

## Edge Cases

1. **Operator clicks "Generate proposals" with no prior runs in `runs.sqlite`.** The priority math falls back to seed cases (the existing `plan_initial_briefs()` behavior). Proposals are still generated; rationale prose explicitly notes "no prior data — proposing seed coverage."
2. **Operator clicks "Generate proposals" multiple times.** Old `proposed` rows that haven't been decided remain; new proposals are appended. The harness must not duplicate-propose the same `(subcategory_id, specialist, seed_case_id)` triple while a prior is still `proposed` — instead skip and log.
3. **LLM rationale call fails (network, refusal, malformed JSON).** Fall back to a deterministic templated rationale string (`f"Priority {score:.2f}. Coverage gap on {subcategory_id}; specialist {specialist}; mutation budget {n}."`). Persist the proposal regardless. Log the LLM failure at WARNING.
4. **Operator approves a proposal, then clicks "Generate proposals" again before launching.** The approved row stays `approved`; new proposals are added alongside. "Launch approved batch" still fires only the `approved` rows.
5. **Operator clicks "Launch approved batch" with zero approved rows.** Button is disabled when count is 0; if somehow triggered, return early with a Streamlit warning.
6. **Approved batch fails mid-flight (target down, auth failure).** Approved rows transition to `executed` with their `run_id` regardless — execution attempts are recorded, not just successes. Outcome (success/failure/partial) lives in the existing `runs` / `judge_verdicts` tables, joined by `run_id`.
7. **Mutation budget edit out of range.** Slider/input enforces `1 ≤ budget ≤ 20` (matches existing per-campaign cap in the codebase; verify exact upper bound from `config.py` during implementation).
8. **`chartbreaker run-mvp-loop` CLI runs concurrently with a dashboard launch.** Both write to `runs.sqlite`. The existing pattern is single-writer at a time; document this constraint in the new tab's help text.
9. **Schema migration runs against an existing DB without the new table.** Migration creates the table additively. Migration runs against a DB that already has the table (idempotent re-run) is a no-op.
10. **Rejected proposal of a `(subcategory, specialist, seed)` triple.** A subsequent "Generate proposals" can re-propose the same triple — rejection is not a permanent block; the operator may want to revisit after a code change. Document this behavior explicitly.

---

## Acceptance Criteria

Each is an explicit, testable assertion.

| # | Criterion | How verified |
|---|---|---|
| AC-1 | Clicking "Generate proposals" from an empty queue produces ≥1 `proposed_campaigns` row with non-null `rationale`, `mutation_budget`, and `est_cost_usd`. | Streamlit smoke test + DB query. |
| AC-2 | Each proposed row's `priority_score` equals the value `plan_initial_briefs()` would emit for the same `(subcategory_id, seed_case_id)`. | Unit test asserting equality between harness output and direct math call. |
| AC-3 | When the LLM rationale call fails, the proposal is still persisted, and `rationale` contains the deterministic-template string. | Unit test with mocked failing LLM client. |
| AC-4 | Operator can edit a proposal's `mutation_budget` in the UI; the new value persists to SQLite immediately (not just session state). | Streamlit interaction test or manual + DB query. |
| AC-5 | Approving a proposal writes `status='approved'`, `decided_at`, `decided_by`. | Unit test. |
| AC-6 | Rejecting a proposal writes `status='rejected'`, `decided_at`, `decided_by`, `rejection_reason` (nullable string). | Unit test. |
| AC-7 | "Launch approved batch" executes exactly the rows with `status='approved'`. Unselected `proposed` rows do not execute; `rejected` rows do not execute. | Integration test with mocked `_run_one_brief()`. |
| AC-8 | After the launched batch returns, every approved row has `status='executed'` and a non-null `run_id` matching the `runs` table. | Integration test. |
| AC-9 | Total estimated cost for selected rows is visible in the UI before launch. | Streamlit smoke test asserting the cost label text is present and matches `sum(est_cost_usd)`. |
| AC-10 | Closing and re-running Streamlit shows the same `proposed` rows in the queue. | Manual + reproducible via two-step test (write to DB, instantiate fresh dashboard render, assert rows present). |
| AC-11 | `chartbreaker run-mvp-loop` runs to completion without reading from or modifying the `proposed_campaigns` table. | Unit/integration test asserting the table row count is unchanged after a CLI run. |
| AC-12 | Schema migration creates the table when absent; re-running the migration is a no-op. | Migration round-trip test. |
| AC-13 | Re-clicking "Generate proposals" with existing `proposed` rows for triple `(X, Y, Z)` does not create a duplicate `proposed` row for the same triple. | Unit test. |
| AC-14 | The architecture doc § Human Approval Gates is updated to reference the new inbound gate alongside the existing outbound Scribe gate. | Diff review. |

---

## Implementation Outline

Minimal implementation, in build order. Each step is independently testable.

### Step 1 — Schema and store layer

- Add `proposed_campaigns` table to `chartbreaker/observability/schema.sql`.
- Columns: `proposal_id TEXT PRIMARY KEY`, `created_at TEXT NOT NULL`, `subcategory_id TEXT NOT NULL`, `specialist TEXT NOT NULL`, `seed_case_id TEXT`, `mutation_budget INTEGER NOT NULL`, `rationale TEXT NOT NULL`, `priority_score REAL NOT NULL`, `est_cost_usd REAL NOT NULL`, `parent_finding_id TEXT`, `status TEXT NOT NULL CHECK(status IN ('proposed','approved','rejected','executed'))`, `decided_at TEXT`, `decided_by TEXT`, `rejection_reason TEXT`, `run_id TEXT`.
- Index on `status` and on `(subcategory_id, specialist, seed_case_id)`.
- Bump `schema_version` per the existing migration convention in `store.py`.
- Add CRUD helpers to `store.py`: `insert_proposed_campaign`, `update_proposed_campaign_status`, `list_proposed_campaigns(status=None)`.

### Step 2 — Harness module

Create `chartbreaker/orchestrator/proposal_harness.py`:

```python
@dataclass(frozen=True)
class ProposedCampaign:
    proposal_id: str
    created_at: str
    subcategory_id: str
    specialist: str
    seed_case_id: str | None
    mutation_budget: int
    rationale: str
    priority_score: float
    est_cost_usd: float
    parent_finding_id: str | None
    status: Literal["proposed", "approved", "rejected", "executed"]
    decided_at: str | None
    decided_by: str | None
    rejection_reason: str | None
    run_id: str | None
```

Functions:
- `propose(store, n: int = 8, *, llm_client=None) -> list[ProposedCampaign]` — calls `plan_initial_briefs()` for parameter selection, calls LLM for rationale per row (graceful fallback on failure), persists with `status='proposed'`, returns the new rows.
- `approve(store, proposal_id: str, mutation_budget_override: int | None = None, decided_by: str = "") -> ProposedCampaign`.
- `reject(store, proposal_id: str, reason: str | None = None, decided_by: str = "") -> ProposedCampaign`.
- `list_pending(store) -> list[ProposedCampaign]` — wrapper for `status='proposed'`.
- `list_approved(store) -> list[ProposedCampaign]`.
- `execute_approved_batch(store, *, run_id_factory=uuid4) -> str` — converts approved rows to `CampaignBrief` objects, dispatches via the existing `_run_one_brief()` (or a new thin wrapper), updates each row to `executed` with the new `run_id`, returns the `run_id`.
- `_estimate_cost(specialist: str, mutation_budget: int) -> float` — deterministic from registry token rates.
- `_render_rationale(brief: CampaignBrief, *, llm_client) -> str` — LLM call with try/except fallback.

### Step 3 — Streamlit tab

Create `chartbreaker/observability/proposal_tab.py`:
- `render(db_path: str) -> None` — main entry point called from `dashboard.py`.
- Top section: "Generate proposals" button + count of pending.
- List section: one card per proposed row with checkbox, rationale, mutation-budget input, est cost, reject button.
- Bottom section: sticky bar with "N selected — est $X.YY total" + "Launch approved batch" button (disabled when N=0).
- History section (collapsed by default): table of `rejected` + `executed` rows for context.

### Step 4 — Dashboard wiring

In `chartbreaker/observability/dashboard.py` `main()` (~line 1893), add a fourth tab:
```python
tab_dashboard, tab_live, tab_arch, tab_plan = st.tabs(
    ["📊 Dashboard", "📡 Live activity", "🏛 Architecture", "📋 Plan Next Run"]
)
with tab_plan:
    proposal_tab.render(db_path)
```

### Step 5 — Doc updates

- `docs/ARCHITECTURE.md` § Human Approval Gates — add the inbound gate next to the existing outbound Scribe gate.
- `docs/OBSERVABILITY.md` — add `proposed_campaigns` row to the SQLite tables list; add a one-line panel description for the new tab.

### Step 6 — Tests

Under `chartbreaker/tests/`:
- `test_proposal_harness.py` — covers AC-1, AC-2, AC-3, AC-5, AC-6, AC-7, AC-8, AC-11, AC-13.
- `test_proposal_schema.py` — covers AC-12.
- `test_proposal_tab.py` — Streamlit smoke render covers AC-1, AC-4, AC-9, AC-10.

### Step 7 — Optional stretch

If `chartbreaker propose [--json] [--n 8]` falls out of the harness in ≤30 LOC: ship it. Otherwise defer.

---

## File Impact Guess (estimate only)

**New files:**
- `chartbreaker/orchestrator/__init__.py` *(new package)*
- `chartbreaker/orchestrator/proposal_harness.py` *(new — core module)*
- `chartbreaker/observability/proposal_tab.py` *(new — Streamlit tab)*
- `chartbreaker/tests/test_proposal_harness.py` *(new)*
- `chartbreaker/tests/test_proposal_schema.py` *(new)*
- `chartbreaker/tests/test_proposal_tab.py` *(new)*

**Modified files:**
- `chartbreaker/observability/schema.sql` *(add table + indices)*
- `chartbreaker/observability/store.py` *(add CRUD helpers + schema-version bump)*
- `chartbreaker/observability/dashboard.py` *(add ~6 lines to register new tab)*
- `chartbreaker/cli.py` *(only if stretch CLI subcommand ships)*
- `docs/ARCHITECTURE.md` *(extend § Human Approval Gates)*
- `docs/OBSERVABILITY.md` *(add table row + tab description)*

**Untouched (must remain so):**
- `chartbreaker/cli.py` `run_mvp_loop` / `_run_one_brief` signatures.
- `chartbreaker/agents/orchestrator_agent.py` `plan_initial_briefs()` signature.
- Existing 8 SQLite tables.

---

## Validation Plan

1. **Unit tests pass.** `pytest chartbreaker/tests/test_proposal_*.py -q` is green; the existing 64+ tests are still green.
2. **Schema migration round-trip.** On a fresh checkout, `chartbreaker run-mvp-loop` creates a new DB and the `proposed_campaigns` table is present with `status` index. Re-running migration is a no-op.
3. **Manual workflow.**
   - `streamlit run chartbreaker/observability/dashboard.py`
   - Open **Plan Next Run** tab — empty queue visible.
   - Click "Generate proposals" — ≥3 rows appear with rationale and cost.
   - Edit one mutation budget; verify it persists by reloading the page (browser hard-refresh).
   - Approve 2 rows, reject 1, leave 1 alone.
   - Click "Launch approved batch" — observe the existing run-test flow execute.
   - Verify approved rows now show `executed` with the new `run_id`; the unapproved-and-undecided row is still `proposed`.
4. **Autonomous regression check.** From a fresh terminal, `chartbreaker run-mvp-loop` runs to completion without touching `proposed_campaigns` (verify by row-count delta).
5. **Cross-session persistence.** Kill `streamlit run`, restart, reopen the tab — pending proposals are still visible.
6. **Doc diff review.** `docs/ARCHITECTURE.md` § Human Approval Gates contains a paragraph referencing the inbound gate; `docs/OBSERVABILITY.md` lists the new table.
7. **Cost sanity.** Run a single approved campaign, compare `est_cost_usd` against the actual `costs` table row for the executed run. They should be within ~30% (rough estimate is acceptable; the goal is operator awareness, not billing accuracy).

---

## Open Questions for Implementation Time

These are not blockers — flag them when the build-plan author or implementer encounters them, decide cheaply, document the decision in the PR description.

1. Exact value of the upper mutation-budget cap (currently believed 20; verify against `config.py`).
2. Whether `parent_finding_id` should be auto-populated from the most recent `partial` Judge verdict in the same subcategory, or left null until a future feature wires it (recommend: null for MVP, populate when present).
3. Whether the LLM rationale prompt template lives in code or in a YAML alongside other agent prompts (recommend: code for MVP; promote to YAML if other agents adopt the same pattern).
4. Whether to expose a "force re-propose" button that ignores the duplicate-skip rule in edge case #2 (recommend: defer — keep the rule, document the deferral).
