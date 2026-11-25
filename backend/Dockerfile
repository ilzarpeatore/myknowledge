# Image shared by the api, worker and migrate services (different commands).
FROM python:3.12-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY kos ./kos
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 kos && mkdir -p /data/storage && chown kos /data/storage
USER kos
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# The API is reachable only on the internal compose network, behind the web proxy.
CMD ["uvicorn", "kos.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
