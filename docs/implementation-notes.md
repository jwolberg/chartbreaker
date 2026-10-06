# Implementation notes

Running log of decisions, deviations, and tradeoffs made while implementing
backlog tickets. Newest entries at the bottom.

## 2026-10-06 — #0001 Declare all Python deps

- **Decision (Jay):** stay on pip + requirements files; no pyproject/uv lockfile.
  The pip-tools/pip-audit promise in `docs/PROJECT_STRATEGY.md` § Release &
  Change Management is still unmet; left as-is.
- Split into `requirements.txt` (runtime, now including streamlit/pandas/altair)
  and `requirements-dev.txt` (`-r requirements.txt` + pytest + pytest-asyncio).
- **Tradeoff:** dashboard deps use `~=` (patch-only) on the versions the
  Dockerfile previously pinned exactly (streamlit 1.47.1, pandas 2.2.2, altair
  5.5.0). Open ranges resolved streamlit 1.65.0, an unreviewed jump for the
  deployed image. Image now gets patch updates (e.g. pandas 2.2.3) on rebuild
  rather than byte-identical versions.
- regression-gate.yml now installs `requirements.txt` (runtime only). It runs
  the CLI, not pytest, so the pytest it used to install was dropped.
- Python version split left documented rather than aligned: CI/.venv run 3.10,
  the Dockerfile runs 3.11. Changing the deployed image's Python was out of scope.
- Not updated: `docs/implementation.md` (dated build log; its old
  `pip install streamlit pandas altair` step is historical record).
- **Not verified:** the Docker image build. The Docker daemon wasn't running
  locally. Rebuild before the next dashboard deploy.

## 2026-10-06 — #0002 Adopt ruff

- Config lives in `ruff.toml`, since the repo has no pyproject.toml (pip-only, per #0001).
  ruff pinned at 0.16.10 in requirements-dev.txt.
- `line-length = 88`: least format churn (671 changed lines vs 771 at 100 and
  1027 at 120), so the code was most likely formatted at 88 originally. E501 isn't selected; the
  formatter owns line length, leaving ~200 long strings/comments alone.
- RUF001–003 ignored: the en-dashes and `×` in prose are deliberate.
- SLF001 ignored under `chartbreaker/tests/**`.
- **Deviation from the ticket:** 22 `noqa` directives were dead under ruff 0.16.10
  (BLE001/SLF001 don't fire on those lines; three E402s sit after
  `pytest.importorskip`, which ruff allows). I didn't use `--fix` for RUF100, since it
  would delete the rationale text too. I scripted removal of just the directive
  and kept the reason as a plain comment (e.g. `# bound to brief boundary`).
- Gotcha: running `ruff --select RUF100` alone marks *every* other rule's noqa as
  unused (E402 included). Always evaluate RUF100 under the full config.
- 32 findings remain after safe fixes (the ticket estimated 18; the explicit rule set adds
  E402 ×9, ASYNC240 ×3, B904, B905, SIM108). These move to #0003.
