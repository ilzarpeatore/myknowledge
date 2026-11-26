"""Local speech-to-text for audio and video (faster-whisper, CPU int8).

Audio is decoded with ffmpeg (any container/codec it knows) to 16 kHz mono and
transcribed in sections of ~``transcribe_chunk_seconds``. Each section ends at
the quietest half-second near its nominal end, so words are not cut in two, and
is saved before the next one starts: a stopped job resumes after the last saved
section. The result is a timed transcript, cited like subtitles (to the cue).
"""

import shutil
import subprocess  # noqa: S404 - fixed ffmpeg/ffprobe invocations, no shell
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
from pathlib import Path

import numpy as np

from kos.config import get_settings
from kos.ingestion.extract import ExtractionError

SAMPLE_RATE = 16_000
SEARCH_TAIL_S = 30  # where to look for a quiet cut, before the nominal end
WINDOW_S = 0.5


def available() -> bool:
    try:
        metadata.version("faster-whisper")
    except metadata.PackageNotFoundError:
        return False
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _bin(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ExtractionError(f"{name} no está instalado (necesario para audio y vídeo)")
    return path


def model_installed() -> bool:
    """The configured speech model is in whisper_model_dir (downloaded once)."""
    s = get_settings()
    name = s.whisper_model.split("/")[-1]
    return any(Path(s.whisper_model_dir).glob(f"models--*{name}*/snapshots/*/model.bin"))


def model_ref() -> str:
    """Identifies the model in saved sections: a different model starts over."""
    return f"faster-whisper:{get_settings().whisper_model}"


def probe_duration_ms(path: Path) -> int:
    r = subprocess.run(  # noqa: S603
        [
            _bin("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    try:
        return int(float(r.stdout.strip()) * 1000)
    except ValueError:
        raise ExtractionError(
            "no se pudo leer el audio (¿archivo dañado o sin pista de audio?)"
        ) from None


def decode(path: Path, start_ms: int, duration_ms: int) -> np.ndarray:
    r = subprocess.run(  # noqa: S603
        [
            _bin("ffmpeg"),
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-t",
            f"{duration_ms / 1000:.3f}",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        timeout=1800,
    )
    if r.returncode != 0:
        raise ExtractionError(
            f"ffmpeg no pudo decodificar el audio: {r.stderr[-300:].decode(errors='replace')}"
        )
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def quiet_cut(audio: np.ndarray, nominal_s: float) -> int:
    """Sample index of the quietest window in the SEARCH_TAIL_S before ``nominal_s``."""
    end = min(int(nominal_s * SAMPLE_RATE), len(audio))
    start = max(end - SEARCH_TAIL_S * SAMPLE_RATE, 0)
    win = int(WINDOW_S * SAMPLE_RATE)
    if end - start < 2 * win:
        return end
    tail = audio[start:end]
    n = len(tail) // win
    energy = (tail[: n * win].reshape(n, win) ** 2).mean(axis=1)
    i = int(np.argmin(energy[::-1]))  # the latest of the quietest windows
    return start + (n - 1 - i) * win + win // 2


def _model():
    """The speech model, loaded once; the PDF layout models are released first so the
    worker never holds both (together they exceed a 4 GB worker; measured)."""
    from kos.ingestion import docling_extract

    if docling_extract._converter.cache_info().currsize:
        docling_extract.release()
    return _load_model()


def release() -> None:
    if _load_model.cache_info().currsize:
        _load_model.cache_clear()
        import gc

        gc.collect()


@lru_cache(maxsize=1)
def _load_model():
    from faster_whisper import WhisperModel

    s = get_settings()
    try:
        return WhisperModel(
            s.whisper_model,
            device="cpu",
            compute_type="int8",
            cpu_threads=s.whisper_threads,
            download_root=s.whisper_model_dir,
            local_files_only=True,
        )
    except Exception as exc:  # noqa: BLE001 - missing files surface in several ways
        raise ExtractionError(
            f"modelo de transcripción no instalado ({s.whisper_model}): "
            "ejecuta `kos download-whisper` (ver DEPLOYMENT.md)"
        ) from exc


def download_model() -> str:
    import os

    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from faster_whisper.utils import download_model as fw_download

    s = get_settings()
    Path(s.whisper_model_dir).mkdir(parents=True, exist_ok=True)
    return fw_download(s.whisper_model, cache_dir=s.whisper_model_dir)


@dataclass
class Section:
    start_ms: int
    end_ms: int
    cues: list[list]  # [start_ms, end_ms, text]
    language: str | None


def transcribe_section(path: Path, start_ms: int, total_ms: int) -> Section:
    """Transcribe from ``start_ms`` to a quiet point near start + chunk length."""
    s = get_settings()
    chunk_ms = s.transcribe_chunk_seconds * 1000
    last = start_ms + chunk_ms >= total_ms
    audio = decode(path, start_ms, total_ms - start_ms if last else chunk_ms)
    if not last:
        audio = audio[: quiet_cut(audio, chunk_ms / 1000)]
    end_ms = total_ms if last else start_ms + int(len(audio) * 1000 / SAMPLE_RATE)
    if len(audio) < SAMPLE_RATE // 10:
        return Section(start_ms, end_ms, [], None)
    segments, info = _model().transcribe(
        audio,
        language=s.whisper_language or None,
        beam_size=1,
        condition_on_previous_text=False,
        word_timestamps=True,
        # Measured (D-18): the VAD filter shifted whole sentences up to 4 s when it
        # removed pauses; without it the worst error was 0.3 s. Hallucinations in
        # long silences ("Gracias por ver el video.") are dropped by this instead.
        vad_filter=False,
        hallucination_silence_threshold=2.0,
    )
    cues = [[start_ms + a, start_ms + b, t] for a, b, t in sentence_cues(segments)]
    return Section(start_ms, end_ms, cues, info.language)


SENTENCE_END = (".", "?", "!", "…")
MAX_CUE_WORDS = 25


def sentence_cues(segments) -> list[tuple[int, int, str]]:
    """One cue per sentence (or per 25 words), timed by its first and last word, so
    a quote that starts a sentence gets that sentence's own time."""
    out: list[tuple[int, int, str]] = []
    for g in segments:
        words = getattr(g, "words", None) or []
        if not words:
            if g.text.strip():
                out.append((int(g.start * 1000), int(g.end * 1000), g.text.strip()))
            continue
        cur: list = []
        for w in words:
            cur.append(w)
            if w.word.strip().endswith(SENTENCE_END) or len(cur) >= MAX_CUE_WORDS:
                out.append(_cue(cur))
                cur = []
        if cur:
            out.append(_cue(cur))
    return [c for c in out if c[2]]


MAX_WORD_S = 1.0  # longer first words have absorbed a preceding pause
WORD_LEAD_S = 0.5


def _cue(words) -> tuple[int, int, str]:
    text = "".join(w.word for w in words).strip()
    first = words[0]
    start = first.start
    if first.end - first.start > MAX_WORD_S:
        # Whisper often stretches the first word after a silence back over it.
        start = first.end - WORD_LEAD_S
    return int(start * 1000), int(words[-1].end * 1000), " ".join(text.split())
