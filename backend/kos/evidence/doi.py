"""Bibliographic metadata by DOI from Crossref.

The response is external data: it is only parsed into plain fields (tags stripped,
lengths capped) and shown to the user; it never reaches a prompt as instructions.
Methodological quality is never taken from here: only the user assesses it.
"""

import html
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

import httpx

from kos.config import get_settings

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
USER_AGENT = "KnowledgeOS/0.1 (personal knowledge base)"
# Tests replace this with an httpx.MockTransport.
TRANSPORT: httpx.AsyncBaseTransport | None = None


class DOIError(Exception):
    pass


class InvalidDOI(DOIError):
    pass


class DOINotFound(DOIError):
    pass


class RegistryUnavailable(DOIError):
    pass


def normalize_doi(value: str) -> str:
    """Accepts '10.x/y', 'doi:10.x/y' or a doi.org URL; returns the lower-cased DOI."""
    v = value.strip()
    v = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", v, flags=re.I)
    v = re.sub(r"^doi:\s*", "", v, flags=re.I).strip().lower()
    if not DOI_RE.match(v) or len(v) > 200:
        raise InvalidDOI("DOI no válido (debe empezar por 10.)")
    return v


def _text(value: Any, limit: int) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, str):
        return None
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit] or None


@dataclass
class WorkMetadata:
    doi: str
    title: str
    year: int | None
    journal: str | None
    authors_text: str | None
    abstract: str | None
    url: str | None
    record_type: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_work(message: dict[str, Any]) -> WorkMetadata:
    year = None
    for key in ("issued", "published-print", "published-online", "published"):
        parts = (message.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and isinstance(parts[0][0], int):
            year = parts[0][0]
            break
    authors = []
    for a in message.get("author") or []:
        if not isinstance(a, dict):
            continue
        name = ", ".join(x for x in (a.get("family"), a.get("given")) if isinstance(x, str))
        name = name or (a.get("name") if isinstance(a.get("name"), str) else "")
        if name:
            authors.append(name)
    authors_text = "; ".join(authors[:30]) + ("; et al." if len(authors) > 30 else "")
    abstract = _text(message.get("abstract"), 10_000)
    if abstract and abstract.lower().startswith("abstract "):
        abstract = abstract[len("abstract ") :]
    return WorkMetadata(
        doi=normalize_doi(str(message.get("DOI", ""))),
        title=_text(message.get("title"), 2000) or "(sin título)",
        year=year,
        journal=_text(message.get("container-title"), 500),
        authors_text=_text(authors_text, 5000),
        abstract=abstract,
        url=_text(message.get("URL"), 500),
        record_type=_text(message.get("type"), 60),
    )


async def fetch(doi: str, transport: httpx.AsyncBaseTransport | None = None) -> WorkMetadata:
    s = get_settings()
    doi = normalize_doi(doi)
    params = {"mailto": s.crossref_mailto} if s.crossref_mailto else None
    try:
        async with httpx.AsyncClient(
            base_url=s.crossref_base_url,
            timeout=s.crossref_timeout_seconds,
            headers={"User-Agent": USER_AGENT},
            transport=transport or TRANSPORT,
        ) as client:
            r = await client.get(f"/works/{quote(doi, safe='/')}", params=params)
    except httpx.HTTPError as exc:
        raise RegistryUnavailable(f"Crossref no responde: {type(exc).__name__}") from None
    if r.status_code == 404:
        raise DOINotFound("Crossref no conoce ese DOI")
    if r.status_code != 200:
        raise RegistryUnavailable(f"Crossref respondió {r.status_code}")
    try:
        message = r.json()["message"]
    except (ValueError, KeyError, TypeError):
        raise RegistryUnavailable("respuesta de Crossref no válida") from None
    return parse_work(message)
