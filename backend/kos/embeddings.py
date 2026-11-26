"""Local multilingual text embeddings (no external API).

Model: intfloat/multilingual-e5-small (MIT licence), ONNX export run with
onnxruntime; 384 dimensions. E5 models expect the prefixes ``query: `` and
``passage: ``. Vectors are mean-pooled and L2-normalised, so cosine distance is
the right metric.

The model files are downloaded once with ``kos download-models`` (done at image
build time); at runtime nothing is fetched from the internet.
"""

import threading
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np

from kos.config import get_settings

EMBEDDING_DIM = 384
HF_REPO = "intfloat/multilingual-e5-small"
MAX_TOKENS = 512
BATCH_SIZE = 32


class Embedder(Protocol):
    model_id: str

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingsUnavailable(Exception):
    pass


class OnnxE5Embedder:
    def __init__(self, model_dir: Path, onnx_file: str):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx_path = model_dir / onnx_file
        tok_path = model_dir / "tokenizer.json"
        if not onnx_path.is_file() or not tok_path.is_file():
            raise EmbeddingsUnavailable(
                f"embedding model not found in {model_dir} (run `kos download-models`)"
            )
        self.model_id = f"{HF_REPO}:{Path(onnx_file).stem}"
        self._tok = Tokenizer.from_file(str(tok_path))
        self._tok.enable_truncation(MAX_TOKENS)
        self._tok.enable_padding(pad_id=self._tok.token_to_id("<pad>") or 1, pad_token="<pad>")  # noqa: S106 - a vocabulary token, not a secret
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = get_settings().embedding_threads
        self._session = ort.InferenceSession(
            str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self._session.get_inputs()}
        self._lock = threading.Lock()  # tokenizer padding state is shared

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            with self._lock:
                enc = self._tok.encode_batch(list(texts[i : i + BATCH_SIZE]))
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self._session.run(None, feeds)[0]
            pooled = (hidden * mask[..., None]).sum(1) / np.maximum(mask.sum(1, keepdims=True), 1)
            pooled /= np.linalg.norm(pooled, axis=1, keepdims=True)
            out.extend(pooled.astype(np.float32).tolist())
        return out

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed([f"passage: {t}" for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"query: {text}"])[0]


@lru_cache
def get_embedder() -> Embedder:
    """Loaded once per process. Raises EmbeddingsUnavailable if not installed."""
    s = get_settings()
    return OnnxE5Embedder(Path(s.embedding_model_dir), s.embedding_onnx_file)


def embeddings_available() -> bool:
    try:
        get_embedder()
    except EmbeddingsUnavailable:
        return False
    return True


def download_model(dest: Path, onnx_file: str) -> None:
    import os

    # Plain HTTPS downloads: the Xet transfer protocol needs extra hosts that
    # restricted networks (and some firewalls) block, and gains nothing here.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from huggingface_hub import hf_hub_download

    dest.mkdir(parents=True, exist_ok=True)
    for name in ("tokenizer.json", onnx_file):
        hf_hub_download(HF_REPO, name, local_dir=str(dest))
