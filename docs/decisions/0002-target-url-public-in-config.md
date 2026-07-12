---
id: 0002
title: Keep the live target URL public in config.py
anchor: ADR-0002
status: accepted
date: 2026-07-12
supersedes:
superseded-by:
---

## [1] Context

`chartbreaker/config.py` hardcodes the deployment ChartBreaker attacks:

```python
TARGET_BASE_URL = "https://openemr.136-118-242-198.sslip.io"
```

When the repository was mirrored to a **public** GitHub repo
(`jwolberg/chartbreaker`), this URL — the address of the OpenEMR Clinical
Co-Pilot that the platform sends adversarial traffic at — became visible to
anyone. The question raised during the public-push cleanup: should the target
be moved to an environment variable / gitignored `.local` file so the public
repo doesn't point strangers at the victim, or left in source?

Options considered:

- **C1 — move to env var:** read `TARGET_BASE_URL` from the environment, commit
  a placeholder default. Nothing in the public repo identifies the target.
- **C2 — placeholder in code:** replace with a documented placeholder; record
  the real URL only in `docs/DEPLOY.local.md`.
- **C3 — leave it hardcoded and public.**

## [2] Decision

Chose **C3 — leave the target URL hardcoded and public.**

The deployment is a disposable, single-instance demo target stood up for the
Gauntlet AI Week 3 exercise, not a production system with real patients or
data. Its address is already referenced across the design docs
(`README.md`, `ARCHITECTURE.md`, `THREAT_MODEL.md`), and the single-target
invariant (target hardcoded, runtime override gated behind paired
`--target-override` + `--i-understand-this-attacks-the-target` flags) is a
deliberate safety property — moving the URL to env config would weaken that
invariant for no real confidentiality gain, since the target is neither secret
nor sensitive.

## [3] Consequences

- Anyone reading the public repo can see, and reach, the target deployment.
  Acceptable because it is disposable and holds only synthetic fixture data
  (no live PHI — see the "No live PHI" commitment in `README.md`).
- The single-target invariant stays intact and greppable in one place.
- **If the target is ever repointed at a non-disposable or sensitive
  deployment, this decision must be revisited** — supersede this ADR with one
  that adopts C1 (env var) before that happens.

## [4] Unchanged and still binding

- The single-target invariant and the paired-flag override gate
  (`docs/ARCHITECTURE.md § Human Approval Gates`) are unaffected.
- Real credentials remain out of source: `CHARTBREAKER_TARGET_USER` /
  `CHARTBREAKER_TARGET_PASSWORD` are read from the gitignored `.env`, never
  committed.
