# ChartBreaker dashboard — Cloud Run image.
#
# Read-only Streamlit dashboard. The runs.sqlite snapshot is baked into
# the image at build time. Update data = rebuild + redeploy.
#
# The launcher button is disabled via CHARTBREAKER_DASHBOARD_READ_ONLY=1
# because Cloud Run containers are request-scoped and a subprocess-launched
# CLI cannot survive there.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CHARTBREAKER_DASHBOARD_READ_ONLY=1

WORKDIR /app

# Install dependencies first so Docker layer cache survives source edits.
# requirements.txt covers both the agent runtime and the dashboard
# (streamlit / pandas / altair).
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Application code + sqlite snapshot. The dashboard resolves
# observability/runs.sqlite as a *relative* path (see chartbreaker/config.py),
# so WORKDIR must be /app and that directory must exist at runtime.
COPY chartbreaker /app/chartbreaker
COPY observability/runs.sqlite /app/observability/runs.sqlite

# Cloud Run injects $PORT (default 8080). Streamlit needs explicit binding
# to 0.0.0.0 and headless mode (no auto-open browser, no usage stats prompt).
ENV PORT=8080 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_ENABLE_CORS=false \
    STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false

EXPOSE 8080

# Shell form so $PORT expands at container start.
CMD streamlit run chartbreaker/observability/dashboard.py \
    --server.port=$PORT \
    --server.address=0.0.0.0
