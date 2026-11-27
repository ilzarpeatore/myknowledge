"""Versioned prompts stored as files in ``kos/ai/prompts/<key>.md``.

File format::

    ---
    version: 3
    ---
    System prompt text. ``{placeholders}`` are filled with str.format_map.

Each version used is recorded in the ``prompts`` table with the SHA-256 of its
text. Changing a prompt file without bumping its version is an error, so every
AI run points to the exact text it was given.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.models import Prompt

PROMPTS_DIR = Path(__file__).parent / "prompts"
_HEADER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class PromptFile:
    key: str
    version: int
    template: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.template.encode()).hexdigest()

    def render(self, **variables: str) -> str:
        return self.template.format_map(variables)


def load(key: str, directory: Path = PROMPTS_DIR) -> PromptFile:
    if not re.fullmatch(r"[a-z0-9_]+", key):
        raise PromptError(f"invalid prompt key {key!r}")
    path = directory / f"{key}.md"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PromptError(f"prompt {key!r} not found") from None
    m = _HEADER.match(raw)
    version = re.search(r"^version:\s*(\d+)\s*$", m.group(1), re.M) if m else None
    if not version:
        raise PromptError(f"prompt {key!r} has no 'version: N' header")
    return PromptFile(key=key, version=int(version.group(1)), template=raw[m.end() :].strip())


async def register(db: AsyncSession, prompt: PromptFile) -> Prompt:
    """Return the stored row for this prompt version, creating it on first use."""
    row = await db.scalar(
        select(Prompt).where(Prompt.key == prompt.key, Prompt.version == prompt.version)
    )
    if row is None:
        row = Prompt(
            key=prompt.key, version=prompt.version, sha256=prompt.sha256, template=prompt.template
        )
        db.add(row)
        await db.flush()
    elif row.sha256 != prompt.sha256:
        raise PromptError(f"prompt {prompt.key!r} v{prompt.version} changed without a version bump")
    return row
