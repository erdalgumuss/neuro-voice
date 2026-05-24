# NQAI Voice gateway image — CPU/control-plane process. VoxCPM2 inference
# lives in deploy/worker.Dockerfile; gateway owns auth, queue submit,
# job status, and B.1.5 live-session admission/token minting.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/nqai

# System libs needed by librosa/soundfile/torchaudio at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts

# Install in editable mode so volume mounts during dev pick up code changes
RUN pip install -e .

# Healthcheck hits the cheap liveness endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
