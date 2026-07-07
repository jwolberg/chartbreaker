# Peer-Target Retargeting (Design Sketch)

**Status:** Design only — not implemented.
**Goal:** Let ChartBreaker point at a classmate's Co-Pilot deployment using
only a base URL and a username/password, without forking the harness.

## Why

ChartBreaker is currently locked to one deployment by design (see
`docs/ARCHITECTURE.md § Human Approval Gates` and the single-target invariant
in `chartbreaker/config.py:3`). The same eval suite, however, is valuable
against peer deployments of the OpenEMR Co-Pilot module — both as a learning
tool and as a cross-team benchmark. The harness is already 90% set up for
this: every endpoint in `chartbreaker/target_endpoints.py` is a relative
path joined to `TARGET_BASE_URL` at runtime. Only the base URL truly varies.

## Scope

In scope: a minimal, opt-in path to swap the target URL while keeping the
single-target safety story intact (peer targeting is gated by an explicit
consent flag, not silent override).

Out of scope: any auto-discovery of differing Co-Pilot module paths or
non-default OpenEMR site names. Those become follow-ups.

## Change Summary

Roughly 20 lines touched across 3 files.

### 1. `chartbreaker/config.py:19` — env-driven base URL

Replace the hardcoded constant with an env-var override that keeps the
current default:

```python
_DEFAULT_TARGET_URL = "https://openemr.136-118-242-198.sslip.io"
TARGET_BASE_URL = os.environ.get(
    "CHARTBREAKER_TARGET_URL",
    _DEFAULT_TARGET_URL,
).rstrip("/")
```

Existing runs against the original deployment require no env change.

### 2. `chartbreaker/target_client.py:145-149` — relax the equality guard

Today the constructor raises if `base_url != TARGET_BASE_URL`. Replace with
a peer-target consent check that mirrors the doc'd
`--i-understand-this-attacks-the-target` pattern:

```python
def __init__(self, base_url: str = TARGET_BASE_URL) -> None:
    if base_url != _DEFAULT_TARGET_URL and not os.environ.get(
        "CHARTBREAKER_I_UNDERSTAND_PEER_TARGET"
    ):
        raise RuntimeError(
            "Peer target requires CHARTBREAKER_I_UNDERSTAND_PEER_TARGET=1."
        )
```

`_DEFAULT_TARGET_URL` is exported from `config.py` for this check.

### 3. `chartbreaker/cli.py:_build_parser` — surface a flag pair

```python
parser.add_argument("--target-url", help="Override the target base URL.")
parser.add_argument(
    "--i-understand-this-attacks-the-target",
    action="store_true",
    dest="consent",
)
```

In the entry point, *before any chartbreaker submodule is imported* (so
config picks them up), translate the flags into env vars. Refuse to start
if `--target-url` is set without `--consent`.

## Usage

```bash
export CHARTBREAKER_TARGET_USER=chartbreaker_tester
export CHARTBREAKER_TARGET_PASSWORD=...
chartbreaker mvp \
  --target-url https://peer-deployment.example.com \
  --i-understand-this-attacks-the-target
```

## Assumptions and Failure Modes

| Assumption | Breaks when… | Mitigation |
|---|---|---|
| Peer serves Co-Pilot at `/interface/modules/custom_modules/oe-module-clinical-copilot/...` | Classmate renamed the module folder | 30-line `probe_target()` at startup that GETs the module dir and asserts presence |
| Peer uses OpenEMR `site=default` | Multi-site deployment | Lift `TARGET_SITE` (config.py:20) to env, same pattern |
| `FIXTURE_PIDS = [1, 2, 3]` (config.py:49) match the peer's test user | Different pid assignments per tenant — any seed case that hardcodes pid 1/2/3 silently becomes a cross-tenant authz attack instead of a baseline | Lift `FIXTURE_PIDS` to env, or have the runner discover accessible pids via `target_client.demographics_url` at startup |
| Peer URL has a valid TLS chain | Self-signed / dev cert / sslip.io quirks | Add `CHARTBREAKER_INSECURE_TLS=1` escape hatch consumed by `target_client` |

## Risks / Follow-ups

- Loosening the `target_client.py:146` equality guard is a real policy
  change. The single-target invariant in `docs/ARCHITECTURE.md § Human
  Approval Gates` needs to be updated to match — invariant doc and code
  should not drift.
- The orchestrator approval harness logs the target URL. Audit whether
  anything else pins the original URL string (dashboard, reports,
  observability traces) before flipping.
- `FIXTURE_PIDS` is the sharpest edge: a cross-tenant authz attack
  *looks* like a baseline-access test until you read the response. Worth
  treating its lift-to-env as part of the same change, not a follow-up.
- Eval cases that embed deployment-specific patient names, encounter IDs,
  or NPIs will produce false negatives on peer targets. Audit
  `evals/seed_cases.yaml` and `evals/regression_cases.yaml` for hardcoded
  values before sharing results.

## Decision Needed

Before implementing:

1. Confirm peer targeting is a sanctioned use case (curriculum-level
   approval, not just self-approval), since the single-target invariant
   was explicit.
2. Confirm whether `FIXTURE_PIDS` lift is in or out of scope for this
   change. (Recommendation: in.)
