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
