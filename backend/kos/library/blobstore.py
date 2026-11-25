"""Content-addressed, write-once file storage for original sources.

Files are stored under ``<root>/raw/<sha[:2]>/<sha>``. A stored file is never
modified or overwritten; writing the same content twice is a no-op. The
interface is small so an S3-compatible implementation can replace it later.
"""

import asyncio
import hashlib
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from kos.config import get_settings

CHUNK = 1024 * 1024


class TooLarge(Exception):
    pass


@dataclass
class StagedFile:
    """An upload written to a temporary file, hashed, not yet committed."""

    path: Path
    sha256: str
    size: int
    head: bytes  # first bytes, for format sniffing

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)


class LocalBlobStore:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).resolve()
        self._tmp = self.root / "tmp"
        self._raw = self.root / "raw"

    def key_for(self, sha256: str) -> str:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError("invalid sha256")
        return f"raw/{sha256[:2]}/{sha256}"

    def path_for(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("key escapes storage root")
        return path

    async def stage(self, chunks: AsyncIterator[bytes], max_bytes: int) -> StagedFile:
        """Stream data to a temp file while hashing; abort past ``max_bytes``.
        Disk writes run in a worker thread so large uploads don't block the event loop."""
        fd, name = await asyncio.to_thread(self._mkstemp)
        path = Path(name)
        out = os.fdopen(fd, "wb")
        digest = hashlib.sha256()
        size = 0
        head = b""
        try:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise TooLarge()
                if len(head) < 4096:
                    head += chunk[: 4096 - len(head)]
                digest.update(chunk)
                await asyncio.to_thread(out.write, chunk)
            await asyncio.to_thread(out.close)
        except BaseException:
            out.close()
            # Synchronous on purpose: an await here could be cancelled and leak the file.
            path.unlink(missing_ok=True)  # noqa: ASYNC240
            raise
        return StagedFile(path=path, sha256=digest.hexdigest(), size=size, head=head)

    def _mkstemp(self) -> tuple[int, str]:
        self._tmp.mkdir(parents=True, exist_ok=True)
        return tempfile.mkstemp(dir=self._tmp)

    def commit(self, staged: StagedFile) -> str:
        """Move a staged file into place (atomic rename). Returns its storage key."""
        key = self.key_for(staged.sha256)
        dest = self.path_for(key)
        if dest.exists():
            staged.discard()
            return key
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(staged.path, 0o444)
        os.replace(staged.path, dest)
        return key

    def exists(self, key: str) -> bool:
        return self.path_for(key).is_file()


@lru_cache
def get_store() -> LocalBlobStore:
    return LocalBlobStore(get_settings().storage_root)
