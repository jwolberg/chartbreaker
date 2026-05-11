---
name: plan
description: Convert ChartBreaker's multi-doc design set (ARCHITECTURE + PROJECT_STRATEGY + USERS + THREAT_MODEL + ASSIGNMENT) into an execution-ready build plan with phases, tickets, dependencies, and status tracking
---

Plan this product strictly from the design documents already in the repo.

This project does not have a single `/spec.md`. The role of "spec" is distributed across several documents, each authoritative for a specific dimension. The plan skill reads all of them and reconciles them into one execution contract.

---

## Input documents and their authority

Read each in full. Each is the source of truth for a different dimension; treat it as authoritative within its dimension and defer to it when documents disagree.

- **/docs/ASSIGNMENT.md** (REQUIRED) — Source of truth for *what must ship* and *by when*. The rubric, the hard gates, the deliverable list. Plan tickets must trace to assignment requirements.
- **/docs/ARCHITECTURE.md** (REQUIRED) — Source of truth for *technical design*. Agent roster, model registry, file layout, framework choices, inter-agent communication, trust boundaries.
- **/docs/PROJECT_STRATEGY.md** (REQUIRED) — Source of truth for *operating model, acceptance criteria, scope boundaries, and the seed ticket list*. Hosting topology, interface choices, success thresholds (MVP and Final), non-goals, platform SLOs, and the "Refreshed Immediate Gaps" table — which is a pre-seeded ticket list the plan should build on, not duplicate.
- **/docs/USERS.md** (REQUIRED) — Source of truth for *who we are building for*. Personas, workflows, automation justification. Tickets that affect a user surface must trace to a persona workflow.
- **/docs/THREAT_MODEL.md** (REQUIRED) — Source of truth for *what the platform tests against*. Attack categories, subcategories, coverage map. Tickets that build a specialist or eval must trace to threat-model subcategories.
- **/ux.md** (OPTIONAL, clarification only) — If present, use only for clarifying interaction details; do not expand scope based on it.

If any of the REQUIRED documents is missing, stop and report what is missing rather than guessing.

---

## Conflict resolution between documents

When two documents disagree, apply this order of authority:

1. **ASSIGNMENT** wins on what must ship and by when (rubric is non-negotiable).
2. **PROJECT_STRATEGY** wins on operating model, scope boundaries, success criteria, and ticket prioritization.
3. **ARCHITECTURE** wins on technical decisions (file layout, classes, frameworks, model split).
4. **USERS** wins on persona workflows and which features serve which user.
5. **THREAT_MODEL** wins on which attack surfaces and subcategories exist.

When all five would inform the same decision (e.g., "do we build the Streamlit dashboard?"), prefer the *narrowest* MVP path that still satisfies ASSIGNMENT's hard gates.

---

## Update output in:
- /docs/BUILD_PLAN.md

---

## Goal

Translate the design-document set into a durable, execution-ready build plan that:

- Aligns to the MVP/Final deadline split documented in ASSIGNMENT and PROJECT_STRATEGY
- Reuses the seed ticket list from PROJECT_STRATEGY § Refreshed Immediate Gaps as the foundation (expand into full ticket format; do not re-enumerate from scratch)
- Reconciles that seed list against ARCHITECTURE § MVP vs Final Cut (per-component table) and PROJECT_STRATEGY § Success Criteria (per-deadline thresholds)
- Survives session resets
- Guides implementation one phase / one ticket at a time

---

## Task

1. Read all REQUIRED input documents above. Skim /ux.md if present.
2. Extract from the document set:
   - Core problem (THREAT_MODEL exec summary + PROJECT_STRATEGY § User-Facing Goal)
   - Scoped solution (ARCHITECTURE § Executive Summary + § Agent Roster)
   - MVP acceptance criteria (PROJECT_STRATEGY § Success Criteria § MVP)
   - Final acceptance criteria (PROJECT_STRATEGY § Success Criteria § Final)
   - Non-goals (PROJECT_STRATEGY § Non-Goals + THREAT_MODEL § Out of Scope)
   - Architecture constraints (ARCHITECTURE § Framework + § Model Configuration + § File Layout)
   - Seed ticket list (PROJECT_STRATEGY § Refreshed Immediate Gaps)
3. Group the seed tickets into phases. Phase boundaries should align with deadline gates:
   - **Phase 1 — MVP Floor** (everything required to satisfy ASSIGNMENT's MVP hard gates by Tue 2026-05-12)
   - **Phase 2 — MVP-to-Final** (the components ARCHITECTURE § MVP vs Final marks as Final-only)
   - **Phase 3 — Final Polish** (demo, social, README, vulnerability reports, cost analysis)
4. Within each phase, order tickets by dependency. Mark the recommended starting ticket.
5. For every ticket, populate Objective / Files / Dependencies / Acceptance criteria covered / Status.
6. Acceptance criteria for each ticket must reference a specific clause from PROJECT_STRATEGY § Success Criteria, ASSIGNMENT § hard gates, or ARCHITECTURE § specific section.
7. Write a durable status-tracking plan.

---

## Required Output Format (/docs/BUILD_PLAN.md)

# Build Plan

## Project
- Name: ChartBreaker — Multi-Agent Adversarial Evaluation Platform
- Summary: <1-2 sentences from THREAT_MODEL exec summary + PROJECT_STRATEGY § User-Facing Goal>

## Source of Truth
- Assignment: /docs/ASSIGNMENT.md
- Strategy / acceptance criteria / non-goals: /docs/PROJECT_STRATEGY.md
- Technical design: /docs/ARCHITECTURE.md
- Personas / workflows: /docs/USERS.md
- Attack-surface model: /docs/THREAT_MODEL.md
- UX clarifications (if used): /ux.md

## Planning Assumptions
- Any place a document was ambiguous and a minimal assumption was made
- Any place two documents disagreed and the conflict-resolution rule was applied

## Architecture Notes
- Stack assumptions from ARCHITECTURE § Framework, State, and Coordination
- Model registry defaults from ARCHITECTURE § Model Configuration
- Hosting topology from PROJECT_STRATEGY § Operating Model § Hosting Topology
- Important constraints (single-target invariant, trust boundaries, human approval gates)
- Explicit non-goals that affect implementation

## Current Status
- Overall status: Not Started
- Current phase:
- Current ticket:
- Blockers: None

---

## Phase Breakdown

### Phase 1 — MVP Floor (deadline: Tue 2026-05-12 23:59)
**Goal**
- Satisfy ASSIGNMENT's MVP hard gates (live target, threat model, eval suite + ≥1 live agent role, architecture doc)

**Exit Criteria**
- Every MVP-row criterion in PROJECT_STRATEGY § Success Criteria § MVP is green

**Tickets**
- P1-T1 — <ticket name>
  - Objective:
  - Files likely involved:
  - Depends on:
  - Acceptance criteria covered: (cite the specific PROJECT_STRATEGY / ASSIGNMENT clause)
  - Status: Todo

(continue with P1-T2 …)

### Phase 2 — MVP-to-Final (deadline: Fri 2026-05-15 noon)
(same structure; tickets sourced from ARCHITECTURE § MVP vs Final Cut "Final" column)

### Phase 3 — Final Polish (same deadline)
(demo video, social post, README, vulnerability reports, cost analysis)

---

## Dependency Order
1. P1-T1
2. P1-T2
3. P2-T1
...

## Recommended Next Step
- Start with: <ticket id + name>
- Why this is first:

## Deferred / Out of Scope
- Items explicitly marked non-goal in PROJECT_STRATEGY § Non-Goals
- Items explicitly out-of-scope in THREAT_MODEL § Out of Scope
- Items marked "⏸ defer" in ARCHITECTURE § MVP vs Final Cut for the current phase

## Update Rules
After each implementation pass:
- Update ticket status only as Todo / In Progress / Complete / Blocked
- Update Current Status
- Record blockers briefly
- Set the next recommended ticket
- Do NOT add new scope unless one of the REQUIRED input documents changes

---

## Rules

- Plan ONLY from the REQUIRED input documents listed above
- Apply the conflict-resolution order when documents disagree
- Do NOT expand product scope beyond what the input documents authorize
- Reuse the seed ticket list in PROJECT_STRATEGY § Refreshed Immediate Gaps as the foundation; expand into full ticket format rather than re-enumerating
- Keep phases aligned to the MVP/Final deadline gates
- Keep tickets small and implementation-ready
- Prefer the smallest viable sequence to a working product
- Do NOT rely on prior conversation
- Be explicit about dependencies
- Preserve traceability: every ticket's acceptance criteria must cite a specific clause from ASSIGNMENT, PROJECT_STRATEGY, ARCHITECTURE, USERS, or THREAT_MODEL

---

## Behavior

- If PROJECT_STRATEGY § Refreshed Immediate Gaps already exists, treat it as the seed and expand each row into a full ticket. Do not duplicate work that table already enumerates.
- If a /docs/BUILD_PLAN.md already exists, update it only if explicitly asked; otherwise create from scratch.
- If a REQUIRED input document is missing, stop and list what is missing.
- If an input document is unclear or two documents disagree, apply the conflict-resolution order, state the assumption in § Planning Assumptions, and proceed. Do not silently choose.
- If ARCHITECTURE specifies a class, file path, or framework, use it verbatim in ticket "Files likely involved" — do not guess alternatives.

---

After writing:
- Confirm file created: /docs/BUILD_PLAN.md
- STOP
