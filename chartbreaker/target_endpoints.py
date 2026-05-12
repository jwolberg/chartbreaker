"""Target endpoint path constants shared between target_client and specialists.

Centralized so a target deployment change (e.g. a module path move) is a
one-line edit. All paths are relative to TARGET_BASE_URL; target_client
refuses absolute URLs at dispatch time.
"""

from __future__ import annotations

from chartbreaker.config import TARGET_SITE

COPILOT_PATH = (
    "/interface/modules/custom_modules/oe-module-clinical-copilot"
    f"/public/index.php?site={TARGET_SITE}"
)

# The OpenEMR login form (GET renders the form, sets a fresh PHPSESSID).
# Cracker Cat 6d session-fixation probe hits this with a pre-seeded cookie.
LOGIN_FORM_PATH = f"/interface/login/login.php?site={TARGET_SITE}"

# The actual login form-POST handler (not the GET-renders-form page).
# Authentic auth happens here. Cracker Cat 6e brute-force probes target this.
LOGIN_SUBMIT_PATH = f"/interface/main/main_screen.php?auth=login&site={TARGET_SITE}"

# Vision-extraction pipeline. POST multipart upload here for Saboteur Cat 4a.
VISION_EXTRACTION_PATH = (
    "/interface/modules/custom_modules/oe-module-clinical-copilot"
    "/public/run-extraction.php"
)
