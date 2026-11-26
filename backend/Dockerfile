# Two images from one file:
#   app     API and migrations (light, no ML dependencies)
#   worker  background jobs, with Docling (PyTorch CPU), Tesseract OCR and the
#           layout/table models baked in, so it runs without internet access.
FROM python:3.12-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" \
    KOS_EMBEDDING_MODEL_DIR=/opt/models/multilingual-e5-small
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN useradd --system --uid 10001 kos && mkdir -p /data/storage && chown kos /data/storage

FROM base AS app
RUN uv sync --frozen --no-dev --no-install-project
COPY kos ./kos
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev
# Local embedding model (MIT), used to embed search queries; ~135 MB.
RUN kos download-models
ENV HF_HUB_OFFLINE=1
USER kos
EXPOSE 8000
# The API is reachable only on the internal compose network, behind the gateway.
CMD ["uvicorn", "kos.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM base AS worker
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-dev --extra docling --no-install-project
# Layout (Apache-2.0) and TableFormer (CDLA-Permissive-2.0 / Apache-2.0) models.
RUN HF_HUB_DISABLE_XET=1 docling-tools models download layout tableformer -o /opt/docling-models
COPY kos ./kos
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev --extra docling
RUN kos download-models
ENV KOS_DOCLING_ARTIFACTS_PATH=/opt/docling-models HF_HUB_OFFLINE=1 KOS_OCR_LANGUAGES=spa,eng
USER kos
CMD ["kos", "worker"]
