# `evals/results/`

Per-run output files written by the ChartBreaker eval runner.

Filename convention: `YYYY-MM-DD-HH-MM-SS.yaml` (UTC).

Each file is a `run_result` record (see `../schema.yaml` § `run_result`):
the run metadata, every case attempted, the target's raw response, the
Judge's verdict, and the cost telemetry. Files are append-only — the
platform never modifies a prior run's result.

The first real run will appear here once the agent prototype is wired
up against the live target.
