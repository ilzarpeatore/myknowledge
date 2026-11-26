"""Runtime configuration, read from environment variables (prefix ``KOS_``)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KOS_", env_file=".env", extra="ignore")

    # postgresql+psycopg://user:password@host:5432/db
    database_url: str = "postgresql+psycopg://kos:kos@localhost:5432/kos"

    # Public origin of the web app (scheme://host[:port]). State-changing
    # requests that carry a session cookie must come from this origin.
    app_origin: str = "http://localhost:3000"

    session_cookie_name: str = "kos_session"
    session_ttl_hours: int = Field(default=24 * 14, ge=1)
    # Must be True when served over HTTPS (production behind Caddy).
    cookie_secure: bool = True

    # Login throttling: failed attempts allowed per window (per username and per IP).
    login_max_failures: int = 5
    login_window_minutes: int = 15

    # Library storage. Originals are content-addressed and never modified.
    storage_root: str = "./storage"
    max_upload_mb: int = Field(default=200, ge=1)

    # PDF extraction: "auto" uses Docling when installed (worker image), else pdfium.
    pdf_extractor: Literal["auto", "docling", "pdfium"] = "auto"
    ocr_languages: str = "spa,eng"  # Tesseract language codes
    docling_timeout_seconds: int = 1800
    # Directory with pre-downloaded Docling models (set in the worker image, which
    # then runs offline). None: Docling downloads them from Hugging Face on first use.
    docling_artifacts_path: str | None = None

    # Local embeddings (semantic search). Files come from `kos download-models`.
    embedding_model_dir: str = "./models/multilingual-e5-small"
    # Quantised int8 export: ~118 MB, ~2x faster, cosine 0.997 vs the fp32 model.
    # Use "onnx/model.onnx" (470 MB) if a CPU ever misbehaves with the int8 one.
    embedding_onnx_file: str = "onnx/model_qint8_avx512_vnni.onnx"
    embedding_threads: int = Field(default=2, ge=1)
    # Semantic hits below this cosine similarity are dropped (E5 scores cluster high).
    semantic_min_score: float = 0.80

    # AI (Anthropic). The API key comes from ANTHROPIC_API_KEY (read by the SDK).
    ai_monthly_budget_usd: float = Field(default=20.0, ge=0)
    ai_request_timeout_seconds: float = 600.0

    # Worker
    worker_poll_seconds: float = 2.0
    job_lease_seconds: int = 15 * 60
    job_retry_base_seconds: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
