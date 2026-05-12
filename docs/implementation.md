# Implementation

## Scope Implemented

- **Requested scope:** Phase 4 — Orchestrator Approval Harness (all of P4-T1 through P4-T5, plus the P4-T6 stretch CLI subcommand). Single-pass implementation per `docs/spec.md` and `docs/BUILD_PLAN.md` § Phase 4.
- **Related phase:** Phase 4 — Orchestrator Approval Harness.
- **Related ticket(s):** P4-T1, P4-T2, P4-T3, P4-T4, P4-T5, P4-T6.

## Approach

- **High-level strategy:** Build a single self-contained vertical slice (schema → store CRUD → harness module → Streamlit tab → docs → tests → CLI). Keep dashboard.py untouched beyond the minimum tab registration (~5 lines). Reuse `plan_initial_briefs()` priority math and `_run_one_brief()` execution path verbatim — the harness is additive to those, never a parallel implementation. LLM rationale narration is a separable concern with a deterministic-template fallback so the harness keeps working when the LLM provider is unavailable.
- **Key decisions:**
  1. **`ProposedCampaign` is a frozen dataclass in the harness module**, not a Pydantic state object in `chartbreaker/state.py`. Rationale: it's a SQLite-persisted queue entity, not an inter-agent message — the existing state objects are LangGraph state inputs, this one isn't.
  2. **Store CRUD takes keyword args, not dataclasses.** Avoids a cross-package import from `observability/store.py` into the new `orchestrator/` package while keeping the surface area minimal.
  3. **Cost estimator is deterministic** — reads `_PRICING_USD_PER_1M` from `llm_client.py` and the model registry from `config.py`. No live probing. Unknown specialist or unknown model degrades to $0 to match the existing chat() fallback behavior.
  4. **LLM narration uses the existing `llm_client.chat()` for role "orchestrator"** — same registry-driven dispatch as the per-tick Orchestrator. The CostObservation returned by chat() is discarded; narration is not billed.
  5. **Schema migration is fully additive.** New table `proposed_campaigns` created by `CREATE TABLE IF NOT EXISTS` in `schema.sql`; the only DB-side migration step is bumping `schema_version` from 2 → 3 by `DELETE FROM schema_version WHERE version < 3`. Re-running the migration is a no-op (matches the v1→v2 pattern from `_migrate_to_v2`).
  6. **Streamlit edits write through to SQLite immediately** via per-callback short-lived `ObservabilityStore` contexts. `st.session_state` is used only for the ephemeral checkbox selection.
  7. **`execute_approved_batch()` accepts an optional `brief_runner` injection** so tests can verify the queue lifecycle without hitting the live target. Production calls fall through to `_default_brief_runner` which opens a `TargetClient` and dispatches via `_run_one_brief` — the single run path.
- **Assumptions:**
  1. The dashboard is always opened on the operator's laptop with read-write access to `observability/runs.sqlite`. (Documented in `PROJECT_STRATEGY.md` § Operating Model.)
  2. Streamlit callbacks run on threads without an active event loop, so `asyncio.run` is safe to call from `execute_approved_batch_sync()`. (Verified by the existing `_render_run_test_control()` pattern in `dashboard.py`.)
  3. Single-operator, single-laptop, single-target. No multi-writer locking is added.

---

## Implementation Plan

Build order (one ticket per step; each ticket independently testable):

1. **P4-T1 — schema + store CRUD.** Add `proposed_campaigns` table to `schema.sql`, bump `schema_version` to 3, add `_migrate_to_v3()` to `store.py`, add CRUD helpers (`insert_proposed_campaign`, `update_proposed_campaign_status`, `set_proposed_campaign_mutation_budget`, `list_proposed_campaigns`, `get_proposed_campaign`).
2. **P4-T2 — `proposal_harness.py` module.** Create `chartbreaker/orchestrator/__init__.py` and `chartbreaker/orchestrator/proposal_harness.py`. Define `ProposedCampaign` dataclass; implement `propose / approve / reject / list_pending / list_approved / list_all / get_proposal / set_mutation_budget / execute_approved_batch / execute_approved_batch_sync` plus `_estimate_cost`, `_template_rationale`, `_default_render_rationale`.
3. **P4-T3 — Streamlit tab.** Create `chartbreaker/observability/proposal_tab.py` with `render(db_path)` entry point. Wire into `dashboard.py` `main()` as a fourth top-level tab. `dashboard.py` change is <10 lines.
4. **P4-T4 — Doc updates.** Update `docs/ARCHITECTURE.md` § Human Approval Gates to add the inbound gate alongside the four outbound ones. Update `docs/OBSERVABILITY.md` SQLite tables list with `proposed_campaigns` and add a Layer-4 panel description for the new tab.
5. **P4-T5 — Tests.** Add `test_proposal_schema.py` (4 tests, AC-12), `test_proposal_harness.py` (17 tests, AC-1/2/3/5/6/7/8/11/13), `test_proposal_tab.py` (5 tests, AC-1/4/9/10 prerequisites). Fix pre-existing `test_observability_migration.py` assertion to expect schema_version=3.
6. **P4-T6 (stretch) — CLI subcommand.** Add `chartbreaker propose [--n] [--json]` to `cli.py`. Total addition: ~25 LOC, under the 30 LOC stretch ceiling.

**Files to modify/create:** see § File Impact Guess in the spec; all paths landed as predicted.

---

## Code Changes

### File: `chartbreaker/observability/schema.sql`

- **Change summary:** Bumped schema version comment from 2 to 3, changed `INSERT OR IGNORE INTO schema_version` from `(2)` to `(3)`, appended new `proposed_campaigns` table with two indexes (`idx_proposed_campaigns_status`, `idx_proposed_campaigns_triple`).
- **Code snippet (key block):**

  ```sql
  CREATE TABLE IF NOT EXISTS proposed_campaigns (
      proposal_id        TEXT    PRIMARY KEY,
      created_at         TEXT    NOT NULL,
      subcategory_id     TEXT    NOT NULL,
      specialist         TEXT    NOT NULL,
      seed_case_id       TEXT,
      mutation_budget    INTEGER NOT NULL,
      rationale          TEXT    NOT NULL,
      priority_score     REAL    NOT NULL,
      est_cost_usd       REAL    NOT NULL,
      parent_finding_id  TEXT,
      status             TEXT    NOT NULL CHECK (status IN ('proposed','approved','rejected','executed')),
      decided_at         TEXT,
      decided_by         TEXT,
      rejection_reason   TEXT,
      run_id             TEXT
  );
  ```

### File: `chartbreaker/observability/store.py`

- **Change summary:** Added `_migrate_to_v3()` (idempotent — just retires stale `schema_version<3` rows). Added five CRUD helpers for the new table at the bottom of `ObservabilityStore`. `_ensure_schema()` now invokes both `_migrate_to_v2()` and `_migrate_to_v3()`.

### File: `chartbreaker/orchestrator/__init__.py`

- **Change summary:** New package marker; one-paragraph docstring pointing at the spec and BUILD_PLAN.

### File: `chartbreaker/orchestrator/proposal_harness.py`

- **Change summary:** New module (~420 LOC). Public surface: `ProposedCampaign` frozen dataclass, `propose / approve / reject / list_pending / list_approved / list_all / get_proposal / set_mutation_budget / execute_approved_batch / execute_approved_batch_sync`. Internal: `_estimate_cost`, `_template_rationale`, `_default_render_rationale`, `_default_brief_runner`, `_row_to_proposal`. Duplicate-skip rule (AC-13) implemented by intersecting incoming candidate triples against pending triples at propose-time. Asyncio guard around narration so pytest-asyncio tests don't blow up.

### File: `chartbreaker/observability/proposal_tab.py`

- **Change summary:** New Streamlit tab (~250 LOC). Three sections (header + generate button, pending queue with per-row controls, sticky launch footer) plus a collapsed History expander. All decisions persist via short-lived `ObservabilityStore` contexts opened inside each callback so SQLite is the source of truth, never `st.session_state`.

### File: `chartbreaker/observability/dashboard.py`

- **Change summary:** Two surgical edits totalling ~7 lines: (1) added a 4th tab to the `st.tabs([...])` call; (2) registered the new tab via a lazy `from chartbreaker.observability import proposal_tab` import inside the `with tab_plan:` block (lazy so importing dashboard.py never forces the harness module to load).

### File: `chartbreaker/cli.py`

- **Change summary:** P4-T6 stretch — added a new `propose` subparser (~10 LOC) and the matching `elif args.cmd == "propose":` branch in `main()` (~15 LOC). The existing `run-mvp-loop` / `regress` / `calibrate` branches and helpers are untouched.

### File: `docs/ARCHITECTURE.md`

- **Change summary:** Rewrote the opening of § Human Approval Gates to split the four outbound gates from the one new inbound gate. Added a 4-sentence paragraph for gate 5 (Orchestrator Approval Harness) that names the harness module, the new SQLite table, and the new Streamlit tab.

### File: `docs/OBSERVABILITY.md`

- **Change summary:** Added a row to the SQLite tables list for `proposed_campaigns` and a one-row description of the new "📋 Plan Next Run" panel under Layer 4. Bumped the table count from 8 to 9 and noted schema_version=3.

### File: `chartbreaker/tests/test_proposal_schema.py` *(new)*

- **Change summary:** 4 tests — v2-DB migration creates table, idempotent re-run, fresh DB starts at v3 with indexes present, CHECK constraint rejects unknown status values.

### File: `chartbreaker/tests/test_proposal_harness.py` *(new)*

- **Change summary:** 17 tests covering the full lifecycle. Asyncio tests for `execute_approved_batch`. Stub `_stub_rationale` and `_failing_rationale` injectors verify both happy and template-fallback paths. AC-11 covered by inserting normal-run rows and asserting `proposed_campaigns` stays empty.

### File: `chartbreaker/tests/test_proposal_tab.py` *(new)*

- **Change summary:** 5 smoke tests guarded by `pytest.importorskip("streamlit")`. Verifies tab module imports, exposes `render`, the prereq-checker surfaces missing env vars, store-opener works against arbitrary paths, and proposals persist across a simulated session restart.

### File: `chartbreaker/tests/test_observability_migration.py`

- **Change summary:** One assertion updated (`version == 2` → `version == 3`) because P4-T1 bumped the canonical schema version. No other change.

---

## Acceptance Criteria Mapping

| Criterion | Implementation | File(s) |
|---|---|---|
| **AC-1** Generate produces ≥1 row with rationale + budget + cost + checkbox | `propose()` always returns at least one row when candidates exist; tab renders each as a card | `proposal_harness.py`, `proposal_tab.py`, `test_proposal_harness.py::test_propose_creates_pending_rows` |
| **AC-2** `priority_score` equals `plan_initial_briefs()` math | propose() uses `orchestrator_agent.priority_score(orchestrator_agent._severity_for_subcategory(...))` — same call path | `proposal_harness.py`, `test_proposal_harness.py::test_propose_priority_score_matches_planner` |
| **AC-3** LLM failure → deterministic-template rationale | Per-row try/except around the gathered async narration; fallback string contains "deterministic template" | `proposal_harness.py::propose`, `_template_rationale`, `test_proposal_harness.py::test_propose_falls_back_to_template_on_llm_failure` |
| **AC-4** Mutation-budget edit persists to SQLite | `_on_budget_change` callback opens a store and calls `set_mutation_budget()` immediately | `proposal_tab.py`, `proposal_harness.py::set_mutation_budget`, `test_proposal_harness.py::test_set_mutation_budget_persists_and_re_costs` |
| **AC-5** Approve writes status + decided_at/by | `approve()` calls `store.update_proposed_campaign_status(status="approved", decided_at=..., decided_by=...)` | `proposal_harness.py::approve`, `test_proposal_harness.py::test_approve_stamps_status_and_decided_fields` |
| **AC-6** Reject writes status + decided + rejection_reason | `reject()` analog | `proposal_harness.py::reject`, `test_proposal_harness.py::test_reject_stamps_reason_and_decided_fields` |
| **AC-7** Launch executes only approved rows | `execute_approved_batch` reads `list_approved` only; rejected and pending rows are not iterated | `proposal_harness.py::execute_approved_batch`, `test_proposal_harness.py::test_execute_approved_batch_runs_only_approved` |
| **AC-8** Approved rows → executed with run_id | After each runner call, `update_proposed_campaign_status(status="executed", run_id=run_id)` | `proposal_harness.py::execute_approved_batch`, asserted in same test as AC-7 |
| **AC-9** Total est. cost visible before launch | Tab footer sums `est_cost_usd` for checked rows and displays it next to the launch button | `proposal_tab.py::_render_launch_footer`, `test_proposal_tab.py::test_cost_visible_via_get_proposal` |
| **AC-10** Pending proposals persist across `streamlit run` restarts | Each callback opens a fresh `ObservabilityStore` against the same SQLite path; no in-memory state | `proposal_tab.py`, `test_proposal_tab.py::test_proposals_persist_across_dashboard_restart` |
| **AC-11** `chartbreaker run-mvp-loop` does not read/write `proposed_campaigns` | The autonomous CLI path was not modified; harness module is never imported during `run-mvp-loop` | `cli.py`, `test_proposal_harness.py::test_proposed_campaigns_table_unchanged_by_unrelated_writes` |
| **AC-12** Schema migration creates table when absent; re-run is no-op | `_migrate_to_v3()` just deletes stale rows; table is created by `CREATE TABLE IF NOT EXISTS` in schema.sql | `store.py`, `schema.sql`, `test_proposal_schema.py::test_v2_db_gets_proposed_campaigns_table` + `::test_migration_is_idempotent` |
| **AC-13** Duplicate `(subcategory_id, specialist, seed_case_id)` triples skipped while still pending | propose() intersects candidate triples with pending triples before persisting | `proposal_harness.py::propose`, `test_proposal_harness.py::test_propose_skips_duplicate_pending_triples` + `::test_propose_re_proposes_after_rejection` |
| **AC-14** Doc updated re: new gate | ARCHITECTURE.md § Human Approval Gates updated; OBSERVABILITY.md tables list updated | `docs/ARCHITECTURE.md`, `docs/OBSERVABILITY.md` |

---

## Build Plan Mapping

| Ticket | Status | What was completed | Remaining work |
|---|---|---|---|
| **P4-T1** | Complete | `proposed_campaigns` table + indexes in `schema.sql`; `schema_version` bumped to 3; `_migrate_to_v3` migration; five CRUD helpers added to `ObservabilityStore`. | — |
| **P4-T2** | Complete | New `chartbreaker/orchestrator/` package; `proposal_harness.py` exposes the full lifecycle API; reuses `plan_initial_briefs()` math; LLM narration with template fallback; deterministic cost estimator; duplicate-skip rule. | — |
| **P4-T3** | Complete | New `chartbreaker/observability/proposal_tab.py` with `render(db_path)`; wired as the 4th Streamlit tab; all edits persist to SQLite immediately. | — |
| **P4-T4** | Complete | `docs/ARCHITECTURE.md` § Human Approval Gates extended with the inbound gate; `docs/OBSERVABILITY.md` SQLite tables list and dashboard panel description updated. | — |
| **P4-T5** | Complete | 26 new tests (4 schema + 17 harness + 5 tab); all green in both `.venv` (5 tab tests skip when streamlit absent) and system Python (all 26 pass). | — |
| **P4-T6** (stretch) | Complete | `chartbreaker propose [--n N] [--json]` CLI subcommand added (~25 LOC, under the 30 LOC ceiling). | — |

---

## Validation

- **How the feature was tested:** Unit tests for the harness lifecycle, schema migration round-trips, and tab persistence. CLI subcommand verified via `--help` parse and module import.
- **Lint/test results:**
  - `.venv` (no streamlit): **139 passed, 2 skipped** (5 streamlit-gated tab tests skip; 1 pre-existing live-API skip).
  - System Python (with streamlit): **all 26 new tests passing** in the proposal-suite invocation.
  - Pre-existing test_observability_migration assertion updated to match the new schema_version=3 expectation.
- **Manual verification steps (for the operator):**
  1. `source .venv/bin/activate && pip install streamlit pandas altair` (one-time, if not yet installed)
  2. `streamlit run chartbreaker/observability/dashboard.py` and open `localhost:8501`.
  3. Click the new **📋 Plan Next Run** tab; click **✨ Generate proposals**.
  4. Edit one mutation budget via the number input — refresh the page (Cmd-R) and confirm the new value persists.
  5. Check two rows; the footer should show "2 selected — est. total $X.XXXX".
  6. Click **🚫 Reject** on one of the unchecked rows — it should disappear from the queue and appear under History.
  7. Click **🚀 Launch approved batch** — observe the harness launch a new run; verify approved rows transition to `executed` in the History panel.
  8. Run `python -m chartbreaker.cli propose --n 5` to validate the CLI mirror.
  9. Run `python -m chartbreaker.cli run-mvp-loop` to confirm the autonomous path is unchanged and `proposed_campaigns` is not touched.
- **Visible user outcome:** The dashboard now has a fourth tab dedicated to reviewing the Orchestrator's next-run slate before it executes. The operator can steer compute, decline expensive batches, and reject low-value campaigns without code edits. The autonomous `chartbreaker run-mvp-loop` continues to work exactly as before.

---

## Open Issues

- **Known limitation:** When `propose()` is invoked from within an active event loop (e.g., pytest-asyncio test), it falls back to deterministic-template rationales instead of calling the LLM. Production callers (Streamlit, CLI) are sync so this only affects tests, but it does mean the async-test path doesn't exercise the LLM narration code. Future work could replace the asyncio.run-from-sync pattern with a thread-pool offload.
- **Streamlit test gating:** `test_proposal_tab.py` is guarded by `pytest.importorskip("streamlit")`. The same gating principle should arguably be applied to the pre-existing `test_dashboard.py`, which also imports pandas/streamlit at module level — out of scope for this ticket. (`requirements.txt` does not yet list streamlit; that is a known inconsistency predating Phase 4.)
- **CLI propose subcommand exit codes:** The new `chartbreaker propose` command exits 0 unconditionally. CI integrations that want to gate on "did we generate at least N proposals" would need to grep the output or use the JSON form. Adding a `--require-n` flag is deferred.

---

## BUILD_PLAN Update

Statuses updated in `docs/BUILD_PLAN.md` § Phase 4:

- P4-T1, P4-T2, P4-T3, P4-T4, P4-T5, P4-T6 — all **Complete**.

No blockers. Phase 4 is fully delivered. Recommended next step: nothing — Phase 4 is a leaf in the dependency graph. If the operator wants to extend, the natural follow-ups are listed in the spec § Out of scope (auto-generate on run-complete; full per-row editing; CI notifications).
