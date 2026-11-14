FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PUBLIC_DEMO=1 SNAPSHOT_DIR=data/public-demo WORKBENCH_RUNS_DIR=/tmp/workbench-runs
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./backend/app
COPY config ./config
COPY data/public-demo ./data/public-demo
COPY --from=frontend /build/backend/app/static ./backend/app/static
RUN useradd --create-home appuser && mkdir -p /app/data/cache && chown -R appuser:appuser /app
USER appuser
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --limit-concurrency 24"]
