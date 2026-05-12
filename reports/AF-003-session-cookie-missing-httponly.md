# AF-003 — `OpenEMR` session cookie issued without `HttpOnly` flag

| Field | Value |
|---|---|
| Finding ID | AF-003 |
| Threat-model category | 6 — Identity / Role / Subcategory **6d** (session fixation + cookie-flag audit) |
| Severity | **High** — session theft via XSS becomes possible if any OpenEMR page surfaces a stored-XSS sink |
| Exploitability | Moderate — requires a paired XSS finding to weaponize; cookie weakness alone is observable, not actively exploitable |
| Discovered by | ChartBreaker Cracker specialist (`chartbreaker/agents/specialists/protocol_specialist.py` — `_session_fixation_attempt`) |
| Run / attempt | `run_id=d4a3cf7a-6d97-45f0-80a3-741e33ff30a9`, `attempt_id=85b7a0f1-9e24-44de-9d42-a2f65195ccc0` |
| Target | OpenEMR Clinical Co-Pilot host at `https://openemr.136-118-242-198.sslip.io` |
| Date observed | 2026-05-11 |
| Judge verdict | `verifier_replay=pass` (no structured output to verify; protocol probe), `semantic=fail`, `recommended_action=regression` |

## Summary

ChartBreaker's Cracker specialist probed the OpenEMR login form (`GET /interface/login/login.php?site=default`) with an attacker-chosen `PHPSESSID` cookie pre-seeded in the request. The server's behavior is **mixed**:

- ✅ **Session fixation: defended.** The server rotated the session token. The cookie returned in the response (`OpenEMR=47020776aae68d718c9c231e527d1456`) is different from the attacker-supplied value. A real session-fixation vulnerability would have echoed the attacker's value back.
- ❌ **Cookie hardening: gap.** The `OpenEMR` session cookie is set **without the `HttpOnly` flag**. The accompanying `App=OpenEMR` cookie does carry `HttpOnly`, so the implementation knows how to set the flag — it just doesn't on the session cookie.

`HttpOnly` blocks JavaScript from reading the cookie via `document.cookie`. Without it, any XSS — stored or reflected — anywhere in the OpenEMR application becomes a session-theft vulnerability. The XSS does not need to live on the Co-Pilot endpoint; the entire OpenEMR application shares the cookie origin.

The raw `Set-Cookie` headers captured at dispatch time:

```
Set-Cookie: App=OpenEMR;    expires=Wed, 12 May 2027 04:51:16 GMT; Max-Age=31536000; path=/; HttpOnly; SameSite=strict
Set-Cookie: OpenEMR=47020776aae68d718c9c231e527d1456; expires=Tue, 12 May 2026 12:37:56 GMT; Max-Age=28000; path=/; SameSite=Strict
```

`App=OpenEMR` is a static label cookie (no security value). The cookie that actually authenticates the user — `OpenEMR=<session>` — is the one missing `HttpOnly`. Both cookies are also missing the `Secure` flag (no `; Secure` directive), which is a separate hardening gap — though OpenEMR is served over HTTPS, the absence of `Secure` allows the cookie to be sent over plaintext if a downgrade ever occurs (e.g. proxy misconfiguration, certificate-pinning failure).

## Attack sequence (reproducible)

1. From an unauthenticated client, GET the login form:

   ```http
   GET /interface/login/login.php?site=default
   Host: openemr.136-118-242-198.sslip.io
   Cookie: PHPSESSID=chartbreaker-fixation-probe-attacker-chosen
   ```

2. Read the `Set-Cookie` headers in the response. Confirm:
   - The response's `OpenEMR` cookie value is **not** the attacker-supplied value (good — no fixation).
   - The `OpenEMR` cookie's flags **do not** include `HttpOnly` (finding) or `Secure` (secondary finding).

3. The probe is captured in `chartbreaker/target_client.py` via `TargetResponse.set_cookie_headers` (added in P2-T8). The full evidence is queryable:

```bash
streamlit run chartbreaker/observability/dashboard.py
# open http://localhost:8501/?attempt_id=85b7a0f1-9e24-44de-9d42-a2f65195ccc0
```

The drill-down page renders the response cookies and Set-Cookie headers under "Response cookies / Set-Cookie headers."

## Why the existing defenses missed it

This is a **server-configuration finding**, not a Co-Pilot module finding. The cookie is issued by OpenEMR's session bootstrap (`php_session_start` or equivalent), which sets cookies via `session_set_cookie_params`. The current params include `SameSite=Strict` but not `HttpOnly` for the session cookie. No verifier or AI layer can catch this — it has to be checked at the HTTP-protocol layer, which is exactly what the Cracker specialist is designed to do.

ChartBreaker's deterministic specialists (Cracker, Saboteur) exist for this reason: high-throughput protocol checks at zero LLM cost. The platform's value here is in surfacing the finding automatically on every deploy, not in clever reasoning.

## Reproducibility

Deterministic, no auth required, no rate-limit concerns. The probe issues a single GET against the public login form. The regression harness will replay it on every `chartbreaker regress` run; the dashboard's open-vulns table will surface the finding while the fix is pending.

## Threat scenario — what an attacker would actually do

1. Find any XSS sink in any OpenEMR page (legacy module markup, unescaped output of user input, a third-party module). XSS is unfortunately common in long-lived PHP healthcare applications.
2. Drop a payload that reads `document.cookie` and exfiltrates `OpenEMR=<session>` to an attacker-controlled host.
3. Use the stolen session to act as the authenticated clinician — including issuing Co-Pilot briefings that surface PHI (per AF-001), executing oversized requests (per AF-002), or whatever other authorized actions the clinician can take.

The `HttpOnly` gap alone is not exploitable; chained with any XSS, it becomes a one-shot account takeover.

## Recommendation

1. **Set `HttpOnly` on the session cookie (one-line fix).** Update the OpenEMR session-bootstrap call to include `'httponly' => true` in `session_set_cookie_params`. Verify the change does not regress any feature that intentionally reads the cookie from JavaScript (rare; usually only analytics).
2. **Set `Secure` on the session cookie.** Same change site: `'secure' => true`. The target deployment is HTTPS-only via Caddy, so this is a safe hardening.
3. **Audit the rest of the cookie surface.** Run the same check against any other Set-Cookie this deployment issues. `App=OpenEMR` already does it right; align everything else.

The fix is comfortably under a 30-minute change for an OpenEMR maintainer with access to the session-bootstrap code path.

## Related findings

- AF-001 (Cat 1b indirect injection) becomes session-theft-grade dangerous once chained with XSS + this cookie gap. Severity of AF-001 is "high" in isolation; in combination it would be "critical."
- ChartBreaker also probes Cat 6a (CSRF token suppression) and Cat 6e (login brute-force). Both returned safe verdicts on this deployment (`recommended_action=discard` for 6a; 6e's Cracker-layer rate cap halts after 2 attempts as designed).
