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

    # Worker
    worker_poll_seconds: float = 2.0
    job_lease_seconds: int = 15 * 60
    job_retry_base_seconds: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
