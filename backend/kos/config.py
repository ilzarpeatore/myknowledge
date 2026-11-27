"""Runtime configuration, read from environment variables (prefix ``KOS_``)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KOS_", env_file=".env", extra="ignore")

    # postgresql+psycopg://user:password@host:5432/db
    database_url: str = "postgresql+psycopg://kos:kos@localhost:5432/kos"

    @field_validator("database_url")
    @classmethod
    def _psycopg3(cls, v: str) -> str:
        # Accept plain libpq URLs too (as pg_dump/psql use them): the driver is psycopg 3.
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix) :]
        return v

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
    # PDFs are converted in batches of this many pages, each one (by default) in a
    # new process: Docling's memory grows with the pages converted and is only fully
    # returned when its process ends. 20 pages peaked at ~2.2 GB on the VPS.
    # OCR: "auto" only on pages without a text layer (scans, covers); "all" also reads
    # text inside images of pages that have text (diagrams in exported slides), at a
    # much higher CPU cost.
    pdf_ocr: Literal["auto", "all"] = "auto"
    docling_batch_pages: int = Field(default=20, ge=1)
    docling_in_subprocess: bool = True
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

    # AI (Anthropic). The API key comes from ANTHROPIC_API_KEY (read by the SDK) or, if
    # that is empty, from the key set in the «IA» page, stored encrypted with this
    # master key (32+ random characters; `openssl rand -hex 32`). Without it the page
    # cannot store a key.
    secret_key: str | None = None
    ai_monthly_budget_usd: float = Field(default=20.0, ge=0)
    ai_request_timeout_seconds: float = 600.0

    # Speech-to-text (worker, `media` extra). Model files come from
    # `kos download-whisper` into whisper_model_dir.
    # "large-v3-turbo": best Spanish quality, ~2.3x real time on 4 cores (1 h -> ~26 min);
    # "small": ~3.4x real time but misses words. Measured in D-18.
    whisper_model: str = "large-v3-turbo"
    whisper_model_dir: str = "./models/whisper"
    whisper_threads: int = Field(default=4, ge=1)
    # ISO code to force one language for every file, or empty (default) to detect it
    # per section. A source's own language (a YouTube video's) always takes precedence.
    whisper_language: str = ""
    # Audio is transcribed in sections of about this length; each one is saved, so an
    # interrupted job resumes from the last finished section.
    transcribe_chunk_seconds: int = Field(default=600, ge=60)
    # Transcribe in a separate process so the model's memory is returned after each job.
    transcribe_in_subprocess: bool = True

    # Study metadata by DOI (Crossref). Only the DOI is sent. An optional contact
    # address gets the "polite pool" (faster, more reliable); empty sends none.
    crossref_base_url: str = "https://api.crossref.org"
    crossref_mailto: str | None = None
    crossref_timeout_seconds: float = 15.0

    # Spaced repetition (FSRS): target probability of remembering a card when it is due.
    study_desired_retention: float = Field(default=0.9, ge=0.7, le=0.99)

    # Alerts (`kos monitor`, `kos remind`): a URL to POST messages to. An ntfy topic
    # (https://ntfy.sh/<topic> or your own server) gets plain text + title; anything else
    # gets JSON {"text", "content"} (Slack and Discord webhooks understand it). Empty: off.
    alert_url: str | None = None
    # Public URL of the app, for links in alerts (defaults to app_origin).
    public_url: str | None = None

    # Worker
    worker_poll_seconds: float = 2.0
    job_lease_seconds: int = 15 * 60
    job_retry_base_seconds: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
