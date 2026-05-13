# DEPLOY.md — ChartBreaker dashboard → Google Cloud Run

> **Scope.** This guide deploys the read-only Streamlit observability
> dashboard to a public Cloud Run URL. The platform itself (Red Team
> agents, Judge, Orchestrator) is *not* deployed — it always runs locally
> per `docs/PROJECT_STRATEGY.md § Hosting Topology`. The Cloud Run image
> bakes a snapshot of `observability/runs.sqlite` and serves it read-only.

## TL;DR — shipping a new snapshot

Once everything in [§ One-time setup](#one-time-setup) is done, every
subsequent deploy is two commands from the repo root:

```bash
gcloud builds submit \
    --tag us-central1-docker.pkg.dev/chartbreaker/chartbreaker/dashboard:latest

gcloud run deploy chartbreaker-dashboard \
    --image us-central1-docker.pkg.dev/chartbreaker/chartbreaker/dashboard:latest
```

End-to-end ~2 minutes. The current `observability/runs.sqlite` is baked
into the image at build time, so the deployed dashboard always reflects
the snapshot you last built.

---

## One-time setup

### Console alternative

If you'd rather click through the GCP console (Cloud Shell + UI) for
first-time setup, follow the longer walkthrough in `docs/specs/`'s
console guide. The CLI path below is the same end state in less time.

### 1. Install + authenticate gcloud

```bash
brew install --cask google-cloud-sdk
gcloud auth login
gcloud auth configure-docker us-central1-docker.pkg.dev
```

### 2. Create / select the project

```bash
gcloud projects create chartbreaker --name="ChartBreaker"   # skip if it exists
gcloud config set project chartbreaker
gcloud config set run/region us-central1
```

You also need a billing account linked to the project. Cloud Run sits
comfortably in the free tier, but billing must be enabled regardless.
Easiest path: GCP console → **Billing** → **Link a billing account**.

### 3. Enable the three required APIs

```bash
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com
```

### 4. Create the Artifact Registry repo

This is where the built image lives. Region must match the Cloud Run
region you use.

```bash
gcloud artifacts repositories create chartbreaker \
    --repository-format=docker \
    --location=us-central1 \
    --description="ChartBreaker dashboard images"
```

---

## Build + deploy (each time you want to ship a new snapshot)

```bash
cd /Users/jmwolberg/workspace/chartbreaker

# Sanity-check the data that's about to be baked in:
ls -lh observability/runs.sqlite

# Build the image in Cloud Build, push to Artifact Registry.
gcloud builds submit \
    --tag us-central1-docker.pkg.dev/chartbreaker/chartbreaker/dashboard:latest

# Deploy to Cloud Run, public.
gcloud run deploy chartbreaker-dashboard \
    --image us-central1-docker.pkg.dev/chartbreaker/chartbreaker/dashboard:latest \
    --platform managed \
    --allow-unauthenticated \
    --port 8080 \
    --memory 1Gi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 2 \
    --timeout 3600 \
    --session-affinity
```

### What each flag does

| Flag | Why |
|---|---|
| `--allow-unauthenticated` | Public access (anyone with the URL). Drop to lock down. |
| `--port 8080` | Matches the `EXPOSE` line in the Dockerfile and the `$PORT` default. |
| `--memory 1Gi` / `--cpu 1` | Streamlit + pandas + altair fit comfortably here. Halve `--memory` only if you trim deps. |
| `--min-instances 0` | Scales to zero when idle (no charge). Bump to `1` to kill cold-start lag for live demos. |
| `--max-instances 2` | Caps cost during unexpected traffic. Increase for production-style demos. |
| `--timeout 3600` | Maximum HTTP/1.1 timeout (1 hour). Streamlit's WebSocket lives for the session length. |
| `--session-affinity` | Pins a given client to one instance so the WebSocket stays alive across reloads. Required for Streamlit. |

---

## Verify

```bash
# Print the URL.
gcloud run services describe chartbreaker-dashboard \
    --format='value(status.url)'

# Smoke-test the endpoint.
curl -I "$(gcloud run services describe chartbreaker-dashboard --format='value(status.url)')"
```

Open the URL in a browser. Expected behavior:

- Sidebar header **🚀 Run test** with a blue info banner: *"Read-only
  deployment — the run launcher is disabled here. Use the local dashboard
  (`streamlit run chartbreaker/observability/dashboard.py`) to start a
  new run."*
- The **Filters** dropdown defaults to the most recent run from the
  baked snapshot, formatted as `YYYY-MM-DD HH:MM PT — laptop:<user>`.
- Selecting any run renders the four-tab dashboard (📊 Dashboard / 📡
  Live activity / 🗺 Architecture / 📋 Plan Next Run).

---

## Updating data

The sqlite snapshot lives inside the image, so refreshing data means a
rebuild + redeploy:

```bash
# 1. Generate fresh data locally.
python -m chartbreaker.cli run-mvp-loop --semantic-judge

# 2. (Optional) Audit it first so you don't ship a broken run.
python -m chartbreaker.cli audit-run <run_id>

# 3. Rebuild + redeploy.
gcloud builds submit \
    --tag us-central1-docker.pkg.dev/chartbreaker/chartbreaker/dashboard:latest
gcloud run deploy chartbreaker-dashboard \
    --image us-central1-docker.pkg.dev/chartbreaker/chartbreaker/dashboard:latest
```

Around 2 minutes end-to-end.

---

## Local sanity check before pushing

```bash
docker build -t chartbreaker-dashboard:local .
docker run --rm -p 8080:8080 -e PORT=8080 chartbreaker-dashboard:local
# → http://localhost:8080
```

Useful for confirming a Dockerfile change before paying for a Cloud
Build. The image is ~600 MB; the first build takes ~90 seconds,
subsequent builds are faster due to layer caching.

---

## How the image is built (the short version)

The `Dockerfile` at the repo root:

1. Starts from `python:3.11-slim`.
2. Installs `requirements.txt` plus the dashboard extras (`streamlit`,
   `pandas`, `altair`) — pinned inline because the agent runtime doesn't
   need them.
3. Copies `chartbreaker/` and `observability/runs.sqlite` into `/app`.
4. Sets `CHARTBREAKER_DASHBOARD_READ_ONLY=1` so the sidebar launcher
   shows the read-only banner instead of trying to spawn a subprocess.
   (Cloud Run containers are request-scoped — a CLI subprocess cannot
   survive across requests, so the button is disabled by design here.)
5. Runs `streamlit run chartbreaker/observability/dashboard.py
   --server.port=$PORT --server.address=0.0.0.0` on container start.

`.dockerignore` excludes `.git`, the virtualenv, the local `.env`,
per-run `observability/*.log` and `*.jsonl` files, and the local
`reports/` / `docs/` directories so the image stays small and never
bakes in secrets.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Permission denied` pushing the image | Cloud Build's service account lacks Artifact Registry Writer | First `gcloud builds submit` prompts to grant — click Yes. Or manually: IAM → `<project-number>@cloudbuild.gserviceaccount.com` → grant **Artifact Registry Writer**. |
| Cloud Run service shows "Container failed to start" | Container bound to the wrong port | The Dockerfile uses `$PORT` (defaults to 8080). Confirm deploy used `--port 8080`. Check **Cloud Run → chartbreaker-dashboard → Logs** for the startup line. |
| Page loads then says "Please wait..." forever | Session affinity not on, or `--timeout` too low | Re-deploy with `--session-affinity --timeout 3600`. |
| `403 Forbidden` on the URL | Service is private | Re-deploy with `--allow-unauthenticated`, or use `gcloud run services proxy` to view locally. |
| Dashboard renders but every run dropdown is empty | The bake-time `runs.sqlite` was empty | Run `chartbreaker run-mvp-loop` locally first, confirm `observability/runs.sqlite` has rows, then rebuild + redeploy. |
| Cold starts feel slow on demo day | `--min-instances 0` scales to zero between visits | Bump to `--min-instances 1`. Costs a few dollars per month but eliminates the ~5–10s warmup. |

---

## Security posture

- **Public read-only.** Anyone with the URL can view every run's attack
  rationales, raw model output, and Judge verdicts. If a finding is
  sensitive enough that disclosure to a stranger would be a problem,
  don't deploy that snapshot. The data baked into the image is the data
  you accept publishing.
- **No write surface.** The run launcher is disabled (`CHARTBREAKER_DASHBOARD_READ_ONLY=1`).
  No form on any tab mutates server state. XSRF and CORS are disabled
  on the Streamlit server because Cloud Run's proxy already terminates
  TLS and there are no state-changing endpoints to defend.
- **No secrets in the image.** `.env` is in `.dockerignore`, the
  Dockerfile sets no API keys, and the dashboard never makes outbound
  LLM calls — it only reads the baked sqlite. The image contains
  `observability/runs.sqlite` (which may include rendered attack
  prompts and model outputs) and nothing else operationally sensitive.

If you ever need authenticated access (sensitive findings, pre-publish
review, etc.):

```bash
gcloud run deploy chartbreaker-dashboard \
    --image us-central1-docker.pkg.dev/chartbreaker/chartbreaker/dashboard:latest \
    --no-allow-unauthenticated
# Then either grant specific identities Cloud Run Invoker, or use:
gcloud run services proxy chartbreaker-dashboard --port 8080
# → http://localhost:8080 with your gcloud auth pre-applied.
```
