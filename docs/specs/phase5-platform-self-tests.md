# Feature Spec — Phase 5: Platform Self-Tests

> **Status:** Draft. Authored 2026-05-12. Build-plan-ready.
> **Owner:** ChartBreaker platform.
> **Companion docs:** [`ARCHITECTURE.md`](../ARCHITECTURE.md) (§ Judge calibration, § Human Approval Gates), [`OBSERVABILITY.md`](../OBSERVABILITY.md) (audit findings surface), [`PROJECT_STRATEGY.md`](../PROJECT_STRATEGY.md) § Release & Change Management (regression gate).

---

## Feature Request

**Original request:** Phase 3 of the assignment asks two questions the platform does not currently answer: *how do you trust the tester* and *how do you trust the platform itself*. Ship three additive capabilities that close the most important gaps without rewriting any existing surface:

1. **`chartbreaker audit-run`** — post-run audit pass that re-reads `runs.sqlite` and flags safety / signal-quality anomalies (ACL breach attempts, budget overruns, agent looping, verdict-disagreement spikes, all-pass / all-fail runs, severity inversion).
2. **Calibration set growth to ≥50 records + per-subcategory accuracy reporting** — turns the existing 6-record calibration set into a defensible "how do you know the Judge works?" answer.
3. **`chartbreaker regress` CI gate** — exit non-zero on any regression, JSON output mode, nightly GitHub Action wired to the existing replay path.

---

## Problem Statement

**What this solves.** The Phase-1–4 platform produces verdicts but has no *meta* layer that asks whether those verdicts are trustworthy.

- The autonomous overnight loop can quietly break in ways the dashboard does not surface: an agent could loop on the same brief; the target could be down (every response is empty → every verdict trivially passes); the semantic Judge could regress and start grading everything `fail`; an attack could pierce the ACL boundary and the operator would not notice because the run "completed successfully."
- The Judge's calibration set has 6 records — enough to validate the runner, not enough to defend the platform against the question "how do you know the Judge isn't hallucinating?"
- `chartbreaker regress` exists and prints a per-case status, but exits 0 even when frozen verdicts have changed. No CI gate consumes it, so a Judge-model drift can ship to `main` undetected.

**Who experiences it.** The ChartBreaker operator (post-run review) and any reviewer/judge evaluating Phase 3 of the rubric.

**Why it matters.**
- **Rubric.** Phase 3 explicitly asks how you audit overnight runs, how you validate the Judge, and what it means for the platform to regress. These three tickets are the answer.
- **Trust posture.** The single-target invariant + dedicated-test-user ACL already prevent damage *by design*. These features detect when the design's assumptions break (e.g. an ACL bypass attempt that succeeded), which is a different and load-bearing claim.
- **CI defensibility.** A regression gate is the cheapest way to keep the Judge honest across model bumps and prompt edits.

---

## Target User and Workflow

**Primary user.** Same as Phase 4: the ChartBreaker operator running CLI locally + Streamlit dashboard. CI is a secondary user (consumes `--json` outputs and process exit codes).

**Desired workflows.**

*Audit a single run after it completes:*
1. Operator runs `chartbreaker run-mvp-loop` → run finishes, `run_id` printed.
2. Operator runs `chartbreaker audit-run <run_id>`.
3. Stdout lists each check that ran (✓ or ✗), with one-line rationale per failure.
4. If all checks pass: exit 0, print `OK`. If any check fails: exit 1, print findings.
5. `--json` flag emits a structured `AuditReport` for downstream tooling.

*Calibrate the Judge:*
1. Operator runs `chartbreaker calibrate`.
2. Output reports aggregate accuracy (existing behavior) **plus** per-subcategory accuracy as a sub-table.
3. Any subcategory with <70% accuracy is flagged inline as a halt-worthy gap, distinct from the aggregate threshold.
4. The calibration yaml itself now contains ≥50 records spanning at least 10 subcategories with ≥10 hard negatives.

*Regression gate in CI:*
1. PR opens → GitHub Action runs `chartbreaker regress --json --strict`.
2. If any case classified `regressed` or `drifted` → action fails → PR blocks.
3. Nightly cron does the same so post-merge model/target drift is caught within 24h.

---

## Success Condition

**Phase 5 succeeds when:**
- An overnight run that quietly broke (target down, agent looping, ACL probe accepted) produces an `audit-run` exit code of 1 within 5 seconds of completion.
- A reviewer asking "how do you know the Judge works?" gets a numeric per-subcategory answer from one command, not a hand-wave.
- A Judge-model bump that changes verdicts on frozen regression cases blocks the merge automatically.

**Phase 5 fails if:**
- Audit checks produce false positives often enough that the operator starts ignoring exit codes.
- Calibration accuracy reporting hides per-subcategory bad cells under a passing aggregate.
- The CI regression gate goes red for reasons unrelated to platform regression (flaky target, transient LLM error) and the operator builds a habit of `--no-verify`.

---

## Scope

### In scope

- New CLI subcommand `chartbreaker audit-run [<run_id> | --all] [--json] [--strict]` with the six audit checks defined in § Acceptance Criteria.
- Grow `evals/judge_calibration.yaml` to ≥50 records, ≥10 subcategories, ≥10 hard negatives.
- Add `chartbreaker/calibration.py` per-subcategory aggregation + reporting.
- Enhance existing `chartbreaker regress`: `--json`, `--strict` (exit non-zero on regression/drift), summary line.
- New `.github/workflows/regression.yml` CI job (PR + nightly).
- Doc updates: `OBSERVABILITY.md` (audit findings table), `ARCHITECTURE.md` (regression gate sentence), `PROJECT_STRATEGY.md` (Release & Change Management entry).

### Out of scope (deferred follow-ups)

- Streamlit "Audit" tab — CLI is enough for MVP; UI is a Phase 6 idea.
- Persisting `AuditFinding` rows to sqlite — audit-run is currently stateless (reads sqlite, writes stdout). Persistence is additive and can be a follow-up.
- Anomaly detection that requires baseline statistics (e.g. "this run's mean cost is 3σ above the trailing 30-day mean"). Phase 5 checks are absolute-threshold only.
- Novelty / clustering of attacks (the third Phase-3 brainstorm bucket). Tracked separately.
- Approval-before-critical-report gate (Scribe agent doesn't exist yet — bundle with Scribe when it lands).
- Attack-authorization tokens. Honor-system target-lock is currently sufficient; revisit when the platform leaves the single-target invariant.

---

## Constraints

### Technical

- **Stateless audit reads.** `audit-run` reads the existing 9-table sqlite store; no schema changes.
- **No new runtime dependencies** beyond what `requirements.txt` already pins. `audit-run` is plain sqlite3 + stdlib; calibration reporting is pandas (already used by the dashboard).
- **Idempotent.** Re-running `audit-run` on the same `run_id` must produce the same output. Re-running `regress` is already idempotent because the regression yaml is the source of truth.
- **CI cost.** The regression CI job calls the live target. Cap at ≤21 cases (current pinned count) and ≤$0.05 each → ≤$1/run worst case. Skip if `CHARTBREAKER_SKIP_LIVE_TESTS=1` is set (e.g. for forks without target credentials).

### Product

- **Operator-first defaults.** Plain `chartbreaker audit-run <run_id>` (no flags) produces human-readable stdout. CI flags (`--json`, `--strict`) are explicit opt-ins.
- **No silent halts.** A `<70%` per-subcategory accuracy bucket flagged in calibration output is informational; only the aggregate threshold halts the platform (existing behavior preserved).
- **Backwards compatibility.** `chartbreaker regress` without flags must emit the same stdout it does today.

### Existing pattern constraints

- New CLI subcommands follow the `argparse` shape already used in `cli.py` (`mvp = sub.add_parser(...)` style).
- New eval records follow the existing `evals/schema.yaml` § `calibration_record` shape — no schema migration.
- New `chartbreaker/audit.py` module mirrors `chartbreaker/calibration.py` (dataclass results + a runner function), so the dashboard can consume `AuditReport` later without refactor.

---

## Edge Cases

- **`audit-run` with an unknown `run_id`** → exit 2, print `unknown run_id` (distinct from "found findings" exit 1).
- **`audit-run --all` against an empty sqlite** → exit 0, print `0 runs audited`.
- **Calibration runner with the current 6-record yaml** must still pass after the yaml grows — additive only, no breakage.
- **Regression yaml grows from 21 → 100 cases** during normal operations — `regress --strict` runtime should stay under the 5-minute CI timeout; if it doesn't, partition by subcategory and parallelize.
- **Target down during CI** → all replays fail uniformly → `audit-run` would flag "all-fail run" but `regress --strict` would mass-fail. CI must distinguish target outage (skip with warning) from real regression (block PR). Use `--require-target-healthcheck` flag that probes `/interface/login/login.php` before the sweep.
- **Hard-negative calibration records** that contain *language* a naïve grep would flag (e.g. "warfarin dose" inside a refusal) must be carefully labeled — the response IS the refusal, the expected verdict IS `pass`. This is the whole point of hard negatives.

---

## Acceptance Criteria

### P5-T1 — `chartbreaker audit-run`

- **AC-1.1** `chartbreaker audit-run <run_id>` reads the run from `runs.sqlite` and executes six audit checks (defined below).
- **AC-1.2** Exit code: `0` if all checks pass; `1` if any check produces a finding; `2` for usage errors (unknown `run_id`, malformed args).
- **AC-1.3** `--json` emits a single JSON object `{run_id, started_at, findings: [...], checks_run: N, checks_failed: M}`.
- **AC-1.4** Audit checks implemented:
  1. **ACL breach probe** — any attempt with `body.pid ∉ FIXTURE_PIDS` is flagged regardless of target response. (Defense-in-depth: ACL should block at target, but log if the platform *tried*.)
  2. **Budget overrun** — sum of costs for the run exceeds `BUDGETS.per_run_usd`.
  3. **Agent looping** — any `(specialist, subcategory_id, seed_case_id)` triple appears in ≥10 attempts within the same run.
  4. **Verdict disagreement spike** — `verifier_replay` ≠ `semantic` on >30% of verdicts in the run (excluding `not_run`).
  5. **All-pass / all-fail run** — every verdict in the run has the same `verifier_replay` value AND attempt count ≥ 6 (rules out trivial 1-attempt runs).
  6. **Severity inversion** — Judge marked `critical` AND `verifier_replay = pass` AND rationale matches one of three known canned strings (templated fallback indicator).
- **AC-1.5** `chartbreaker audit-run --all` runs every audit check across every run in the store; aggregate exit code is the OR of per-run exit codes.
- **AC-1.6** Clean run (zero findings) prints exactly `OK — N checks passed` and exits 0.

### P5-T2 — Calibration set growth + per-subcategory reporting

- **AC-2.1** `evals/judge_calibration.yaml` contains ≥50 records spanning ≥10 distinct subcategories.
- **AC-2.2** ≥10 records are explicit *hard negatives* (responses that contain risky-sounding language but are actually compliant; expected verdict is `pass`). Each is annotated `kind: hard_negative` in the record.
- **AC-2.3** `chartbreaker calibrate` output gains a "Per-subcategory accuracy" table:
  ```
  subcategory  records  correct  accuracy
  1a           7        7        100%
  1b           6        4        66%   ← BELOW 70%, halt-worthy
  ...
  ```
- **AC-2.4** Any per-subcategory accuracy `<70%` is highlighted inline (text marker + non-zero return from a new `report.below_threshold_subcategories` accessor); aggregate threshold gating is unchanged.
- **AC-2.5** Existing aggregate thresholds (≥85% pass, 70–85% warn, <70% halt) and existing exit-code contract preserved.
- **AC-2.6** `evals/schema.yaml § calibration_record` unchanged — schema is additive only via the new optional `kind` field.

### P5-T3 — Regression CI gate

- **AC-3.1** `chartbreaker regress --strict` exits non-zero on any case classified `regressed` or `drifted` by `regression.classify_replay`.
- **AC-3.2** `chartbreaker regress --json` emits `{run_id, cases: [{id, subcategory, frozen_verdict, current_verdict, status}, ...], summary: {passed, regressed, drifted, error}}` on stdout.
- **AC-3.3** Default (no flags) stdout output is byte-identical to today's behavior plus a single new summary line at the end: `Summary: P passed · R regressed · D drifted · E error`.
- **AC-3.4** New `.github/workflows/regression.yml`: triggers on `pull_request` to `main` and nightly `cron: '0 7 * * *'`; runs `chartbreaker regress --strict --json` and uploads the JSON as an artifact.
- **AC-3.5** `--require-target-healthcheck` short-circuits with exit 0 + warning if `TARGET_BASE_URL` is unreachable; CI uses this flag to distinguish outage from regression.
- **AC-3.6** `docs/PROJECT_STRATEGY.md § Release & Change Management` gains one sentence describing the gate.

---

## Implementation Outline

### Step 1 — `chartbreaker/audit.py`

- New module. Frozen dataclass `AuditFinding(check: str, severity: Literal["warn", "fail"], rationale: str, evidence: dict)` and `AuditReport(run_id, started_at, findings, checks_run)`.
- Six check functions, each `(store, run_id) -> list[AuditFinding]`. Pure: read sqlite, return findings, no side effects.
- `audit_run(run_id, store) -> AuditReport` aggregates all six.
- ~250 LOC including all six checks.

### Step 2 — CLI wiring for `audit-run`

- Add `audit_parser = sub.add_parser("audit-run", ...)` in `_build_parser()`.
- Argparse: positional `run_id` (optional), `--all`, `--json`, `--strict` (alias for default; reserved for future warn-vs-fail distinction).
- New `def audit_run_cli(args) -> int` dispatches to `audit.audit_run` or iterates over all runs; returns 0/1/2.

### Step 3 — Grow `evals/judge_calibration.yaml`

- Add ≥44 new records (current is 6). Coverage targets:
  - At least 2 records per subcategory across categories 1, 2, 3, 6 (the four highest-impact categories per THREAT_MODEL).
  - ≥10 `kind: hard_negative` records: responses where the chatbot *correctly refused* but using language that contains tripwires (e.g. mentions "metformin" in a refusal, mentions a source ID format in plaintext, etc.). Expected verdict: `pass`.
  - ≥5 ambiguous "partial" records to exercise the three-way classifier.
- Bump `metadata.total_records` to actual count.

### Step 4 — Per-subcategory calibration reporting

- In `chartbreaker/calibration.py`: extend `CalibrationReport` dataclass with `per_subcategory_accuracy: dict[str, tuple[int, int, float]]` (records, correct, accuracy).
- New `below_threshold_subcategories(threshold=0.70) -> list[str]` method on the report.
- `print_report` extended with the new table.

### Step 5 — Enhance `chartbreaker regress`

- Modify `run_regression_sweep` to collect per-case status into a list instead of streaming to stdout only.
- Add `--json`, `--strict`, `--require-target-healthcheck` flags to existing `regress` subparser.
- When `--strict` and any status ∈ `{regressed, drifted}` → exit 1.
- Summary line emitted at end of stdout in all modes (default + JSON).

### Step 6 — CI workflow

- `.github/workflows/regression.yml`:
  - Triggers: PR to `main`, nightly cron.
  - Secret check: skip if `CHARTBREAKER_TARGET_USER` not set in repo secrets.
  - Steps: install deps, run `chartbreaker regress --strict --json --require-target-healthcheck > regression.json`, upload as artifact.

### Step 7 — Doc updates

- `docs/OBSERVABILITY.md`: new "§ Audit findings" subsection — name the checks, link to the CLI command, note that findings are stdout-only today (not persisted).
- `docs/ARCHITECTURE.md` § Judge: bump calibration set size mention; § Human Approval Gates: cross-link to the audit command as the post-run review surface.
- `docs/PROJECT_STRATEGY.md` § Release & Change Management: regression gate description (one sentence).

### Step 8 — Tests

- `chartbreaker/tests/test_audit.py` (~12 tests): one per check (passing case + failing case), plus `audit_run` aggregator, plus CLI exit-code matrix.
- `chartbreaker/tests/test_calibration.py` (extend if exists; create otherwise): per-subcategory aggregation, below-threshold accessor, additive schema unchanged.
- `chartbreaker/tests/test_regression.py` (extend): `--json` shape, `--strict` exit codes, healthcheck short-circuit.
- Target: ~20 new tests; existing 139 stay green.

---

## File Impact Guess (estimate only)

| File | Change | LOC est. |
|---|---|---|
| `chartbreaker/audit.py` *(new)* | Six checks + report dataclasses + runner | ~250 |
| `chartbreaker/cli.py` | Add `audit-run` subparser + dispatch; tweak `regress` subparser | ~60 |
| `chartbreaker/calibration.py` | Per-subcategory aggregation + reporting | ~50 |
| `chartbreaker/regression.py` | JSON output, strict mode, healthcheck | ~40 |
| `evals/judge_calibration.yaml` | +44 records | ~600 (yaml) |
| `.github/workflows/regression.yml` *(new)* | CI job | ~40 |
| `chartbreaker/tests/test_audit.py` *(new)* | Six-check coverage | ~250 |
| `chartbreaker/tests/test_calibration.py` *(extend or new)* | Per-subcategory + threshold tests | ~80 |
| `chartbreaker/tests/test_regression.py` *(extend or new)* | JSON + strict + healthcheck | ~80 |
| `docs/OBSERVABILITY.md`, `docs/ARCHITECTURE.md`, `docs/PROJECT_STRATEGY.md` | Section additions | ~30 each |

Total ≈ 1500 LOC including yaml + tests; the executable Python footprint is small (~400 LOC).

---

## Validation Plan

- **Unit-level:** Pytest coverage per ticket as listed in Step 8.
- **End-to-end audit:** Run `chartbreaker run-mvp-loop` against the live target, then `chartbreaker audit-run <run_id>` — confirm clean run gets `OK`, exit 0. Intentionally break it (e.g. point target at unreachable host, run with `--mutation-budget 0`) and confirm checks fire.
- **End-to-end calibration:** Run `chartbreaker calibrate` after yaml growth — confirm aggregate still ≥85%, per-subcategory table appears, any sub-70% bucket is flagged.
- **End-to-end regression:** Manually edit one `frozen_verdict` in `regression_cases.yaml` to a wrong value, run `chartbreaker regress --strict` — confirm exit 1; revert; confirm exit 0.
- **CI dry-run:** Push a branch with the workflow; confirm action runs end-to-end against the live target.

---

## Open Questions for Implementation Time

1. **Audit-finding persistence.** Today findings go to stdout only. Worth adding an `audit_findings` table now (cheap) or deferring until the dashboard wants to render them?
2. **Healthcheck endpoint.** `interface/login/login.php` returns 200 even when the Co-Pilot module is down. Worth a dedicated lightweight ping endpoint, or is "target reachable at all" enough for CI?
3. **Hard-negative provenance.** Should each hard-negative record cite the real run that produced it (so we can prove these aren't synthetic)? Optional `provenance` field on the record.
4. **CI on forks.** Forks won't have `CHARTBREAKER_TARGET_USER` — workflow should `if: ${{ secrets.CHARTBREAKER_TARGET_USER != '' }}` to silently skip rather than fail.
