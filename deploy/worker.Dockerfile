# NQAI Voice GPU worker image — owns VoxCPM2 inference and streaming pipeline.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/nqai

RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts

RUN pip install -e "."

CMD ["python", "-m", "worker.main"]
