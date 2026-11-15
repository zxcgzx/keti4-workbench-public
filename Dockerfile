FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PUBLIC_DEMO=0 SNAPSHOT_DIR=data/snapshots WORKBENCH_RUNS_DIR=/tmp/workbench-runs
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --create-home appuser && mkdir -p /app/data/cache && chown -R appuser:appuser /app
USER appuser
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --limit-concurrency 16"]
