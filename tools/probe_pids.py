"""One-shot probe: discover which OpenEMR pids the `phd` user can open.

Reuses ChartBreaker's existing target-login + CSRF helpers. For each
candidate pid, GETs the demographics page, reports HTTP status, whether
a `data-csrf-token` attribute is present (i.e. the chart page rendered),
and any pubpid / patient name we can pull out of the HTML.

Not part of the shipped CLI — drop in tools/, run once, delete.

Run from repo root:
    python -m tools.probe_pids 1 60
    python -m tools.probe_pids --pubpid-search DEMO
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

from dotenv import load_dotenv as _load_dotenv

_load_dotenv()  # picks up .env from repo root

import httpx  # noqa: E402

from chartbreaker import config  # noqa: E402
from chartbreaker.target_endpoints import LOGIN_SUBMIT_PATH  # noqa: E402

_PUBPID_DATA_VALUE_RE = re.compile(
    r"pubpid[^<>]*?data-value=['\"]([^'\"]+)['\"]", re.IGNORECASE
)
_TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)


async def _login(client: httpx.AsyncClient) -> bool:
    """Mirror chartbreaker.target_client._authenticate."""
    user, password = config.get_target_credentials()
    r = await client.post(
        f"{config.TARGET_BASE_URL}{LOGIN_SUBMIT_PATH}",
        data={
            "new_login_session_management": "1",
            "languageChoice": "1",
            "authUser": user,
            "clearPass": password,
        },
    )
    if r.status_code >= 400:
        print(f"login POST failed: {r.status_code}", file=sys.stderr)
        return False
    if "login_screen.php?error" in r.text or "timed_out = true" in r.text:
        print("login bounced back to login_screen — bad creds?", file=sys.stderr)
        return False
    return True


async def _probe(client: httpx.AsyncClient, pid: int) -> dict:
    """Fetch demographics.php?set_pid=<pid> and pull what we can.

    A genuine chart page ends up at the demographics URL with the chart's
    HTML body. An unauthorized pid usually silently redirects back to
    /interface/main/main_screen.php (the dashboard). We use the final URL
    after redirects to distinguish, then look for patient-page markers.
    """
    path = f"/interface/patient_file/summary/demographics.php?set_pid={pid}&site={config.TARGET_SITE}"
    r = await client.get(f"{config.TARGET_BASE_URL}{path}")
    final_url = str(r.url)
    text = r.text or ""
    landed_on_chart = "demographics.php" in final_url
    has_csrf = "data-csrf-token" in text or 'name="csrf_token"' in text
    # Real chart pages have one or more of: pubpid label, patient-name
    # element, demographic data divs. We try a handful.
    has_chart_markers = any(
        marker in text
        for marker in (
            "DEM-summary",
            'id="DEM"',
            "patient_data",
            "External ID",
            "Public ID",
        )
    )
    title = _TITLE_RE.search(text)
    pubpid_m = _PUBPID_DATA_VALUE_RE.search(text)
    return {
        "pid": pid,
        "status": r.status_code,
        "len": len(text),
        "final_url": final_url,
        "landed_on_chart": landed_on_chart,
        "has_csrf": has_csrf,
        "has_chart_markers": has_chart_markers,
        "title": title.group(1).strip() if title else None,
        "pubpid": pubpid_m.group(1).strip() if pubpid_m else None,
        "name": None,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("start", type=int, nargs="?", default=1)
    parser.add_argument("end", type=int, nargs="?", default=60)
    parser.add_argument(
        "--show-misses",
        action="store_true",
        help="Also print pids that returned errors / blank pages.",
    )
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        if not await _login(client):
            return 2
        print(f"logged in; probing pids {args.start}..{args.end}")
        results = []
        for pid in range(args.start, args.end + 1):
            row = await _probe(client, pid)
            results.append(row)
            is_hit = row["landed_on_chart"] and row["has_csrf"] and row["status"] == 200
            if is_hit or args.show_misses:
                print(
                    f"  pid={row['pid']:>5} "
                    f"status={row['status']:>3} "
                    f"chart={'Y' if row['landed_on_chart'] else 'n'} "
                    f"csrf={'Y' if row['has_csrf'] else 'n'} "
                    f"markers={'Y' if row['has_chart_markers'] else 'n'} "
                    f"pubpid={row['pubpid'] or '-'} "
                    f"name={row['name'] or '-'} "
                    f"title={(row['title'] or '')[:40]}"
                )

    hits = [
        r
        for r in results
        if r["landed_on_chart"] and r["has_csrf"] and r["status"] == 200
    ]
    print(f"\n{len(hits)}/{len(results)} pids rendered a chart page for `phd`.")
    if hits:
        print("\nIntegers usable as FIXTURE_PIDS:")
        for r in hits:
            label = r["pubpid"] or r["name"] or r["title"] or "?"
            print(f"  {r['pid']:>5}  →  {label}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
