# ruff: noqa: E501  (regular expressions read better on one line)
"""Find bibliographic references in text, without AI.

Three shapes, as they appear on slides, in books and in articles:

* in-text citations: «Schoenfeld et al., 2017», «(Grgic & Schoenfeld, 2019)»,
  «Schoenfeld y col. (2016)», «(Helms, 2014; Refalo et al., 2023a)»;
* bibliography entries (APA, Vancouver and similar): «Schoenfeld, B. J., Ogborn, D.
  (2017). Dose-response… Journal…» or «Schoenfeld BJ, Ogborn D. Dose-response…
  J Sports Sci. 2017;35:1073-82.»;
* DOIs: «10.1080/02640414.2016.1210197».

Every hit says where it is in the text (character offsets), so a mention can be
shown and cited like any quote. The grouping key is the first author's surname
plus the year («schoenfeld 2017a»): the same work written in different ways counts
as one reference. Patterns favour precision: a single surname next to a year counts
only inside parentheses or as «Surname (2017)», so «Madrid, 2019» is not a reference.
"""

import re
import unicodedata
from dataclasses import dataclass, field

UPPER = "A-ZÁÉÍÓÚÑÄËÏÖÜÀÈÌÒÙÂÊÎÔÛÇ"
LOWER = "a-záéíóúñäëïöüàèìòùâêîôûçß"
PARTICLE = r"(?:(?:de|del|de la|da|dos|van|von|der|den|di|du|le|la|mac|o')\s+)"
SURNAME = rf"(?:{PARTICLE}?[{UPPER}][{LOWER}'’]+(?:[-‐][{UPPER}][{LOWER}'’]+)?)"
YEAR = r"(?P<year>(?:19[5-9]\d|20[0-4]\d))(?P<suffix>[a-z])?"
ETAL = r"(?:et\s+al\.?|y\s+col(?:aboradores|s)?\.?|y\s+cols?\.?|e\s+col\.?)"
SECOND = rf"(?:\s*(?:&|and|y|e)\s+{SURNAME})"
AUTHORS = rf"(?P<author>{SURNAME})(?P<more>\s+{ETAL}|{SECOND}(?:\s+{ETAL})?)?"

# «Schoenfeld et al., 2017» / «Schoenfeld & Grgic 2019» (no parentheses: needs a co-author)
BARE_MULTI = re.compile(rf"\b{AUTHORS},?\s+{YEAR}\b")
# «Schoenfeld (2017)», «Schoenfeld et al. (2017)»
NARRATIVE = re.compile(rf"\b{AUTHORS}\s*\(\s*{YEAR}\s*\)")
# inside parentheses: «(Schoenfeld, 2010; Grgic et al., 2018)»
PAREN = re.compile(r"\(([^()]{4,400})\)")
CUE = r"(?:(?:e\.\s?g\.|p\.\s?ej\.|ver|véase|vease|cf\.|see|i\.\s?e\.)\s*,?\s*)?"
PAREN_ITEM = re.compile(rf"^\s*{CUE}{AUTHORS},?\s+{YEAR}\b")
# institutions: «(Organización Mundial de la Salud, 2020)», «(ACSM, 2009)»
CORP_WORD = rf"(?:[{UPPER}][\w'’\-]*|de|del|la|las|los|el|of|the|and|y|for|para|on|in|en)"
PAREN_CORP = re.compile(
    rf"^\s*{CUE}(?P<author>[{UPPER}][\w'’\-]*(?:\s+{CORP_WORD}){{0,7}}),?\s+{YEAR}\s*$"
)

DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>()\[\]{}]+)", re.I)

# Bibliography lines, after stripping «1.», «[12]», «-» numbering.
LEAD = re.compile(r"^\s*(?:\[\d+\]|\d{1,3}[.)]|[-•·*])\s*")
APA = re.compile(
    rf"^(?P<authors>{SURNAME},\s+(?:[{UPPER}]\.\s?-?)+.*?)\s*\(\s*{YEAR}\s*[^)]*\)\.?\s*(?P<title>[^.?!]{{8,300}}[.?!])"
)
VANCOUVER = re.compile(
    rf"^(?P<authors>{SURNAME}\s+[{UPPER}]{{1,3}}(?:,\s*{SURNAME}\s+[{UPPER}]{{1,3}})*(?:,?\s*et\s+al)?)\.\s+"
    rf"(?P<title>[^.?!]{{8,300}}[.?!])\s+(?P<rest>.{{0,200}}?)\b{YEAR}\b"
)
# «SURNAME, Name (2017). Title» (Spanish/ISO style, names spelled out)
ISO = re.compile(
    rf"^(?P<authors>[{UPPER}]{{2,}}(?:[-\s][{UPPER}]{{2,}})*,\s+[{UPPER}][{LOWER}]+.*?)\s*\(?\s*{YEAR}\s*\)?\.\s*(?P<title>[^.?!]{{8,300}}[.?!])"
)

# Capitalised words that, next to a year, are not authors.
NOT_AUTHORS = {
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "setiembre",
    "octubre",
    "noviembre",
    "diciembre",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "figura",
    "figure",
    "tabla",
    "table",
    "fuente",
    "source",
    "copyright",
    "edicion",
    "edition",
    "volumen",
    "volume",
    "version",
    "verano",
    "invierno",
    "primavera",
    "otono",
    "summer",
    "winter",
    "spring",
    "autumn",
    "fall",
    "temporada",
    "season",
    "curso",
    "año",
    "ano",
    "year",
    "desde",
    "hasta",
    "since",
    "until",
    "in",
    "en",
    "the",
    "el",
    "la",
    "los",
    "las",
    "and",
    "y",
    "para",
    "for",
    "con",
    "with",
    "según",
    "segun",
    "madrid",
    "barcelona",
    "london",
    "paris",
    "londres",
    "valencia",
    "sevilla",
}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def surname_key(author: str) -> str:
    """«de la Fuente» → «fuente», «Martínez-López» → «martinez lopez»."""
    parts = fold(author).split()
    particles = {
        "de",
        "del",
        "la",
        "da",
        "dos",
        "van",
        "von",
        "der",
        "den",
        "di",
        "du",
        "le",
        "mac",
        "o",
    }
    while len(parts) > 1 and parts[0] in particles:
        parts.pop(0)
    return " ".join(parts)


@dataclass
class Found:
    kind: str  # in_text | bibliography | doi
    start: int
    end: int
    raw: str
    author: str | None = None
    year: int | None = None
    suffix: str = ""
    doi: str | None = None
    title: str | None = None
    authors_text: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str | None:
        if self.author and self.year:
            if self.extra.get("corporate"):
                name = fold(self.author)
            else:  # first surname only: «González-Badillo» = «GONZÁLEZ BADILLO» = «González»
                name = surname_key(self.author).split()[0]
            return f"{name} {self.year}{self.suffix}"
        if self.doi:
            return f"doi:{self.doi}"
        return None


def _clean_doi(raw: str) -> str:
    return raw.rstrip(".,;:").lower()


def _author_ok(author: str) -> bool:
    k = fold(author)
    return bool(k) and k not in NOT_AUTHORS and len(k) >= 2


def _in_text(text: str) -> list[Found]:
    out: list[Found] = []
    taken: list[tuple[int, int]] = []

    def add(m: re.Match, base: int = 0, corporate: bool = False) -> None:
        a, b = base + m.start("author"), base + m.end()
        if any(a < y and x < b for x, y in taken) or not _author_ok(m.group("author")):
            return
        taken.append((a, b))
        out.append(
            Found(
                "in_text",
                a,
                b,
                text[a:b],
                author=m.group("author"),
                year=int(m.group("year")),
                suffix=m.group("suffix") or "",
                extra={"et_al": bool(m.groupdict().get("more")), "corporate": corporate},
            )
        )

    for pm in PAREN.finditer(text):
        inner_start = pm.start(1)
        for part in re.finditer(r"[^;]+", pm.group(1)):
            item = part.group(0)
            if m := PAREN_ITEM.match(item):
                add(m, inner_start + part.start())
            elif (m := PAREN_CORP.match(item)) and not re.match(SURNAME + r",", item.strip()):
                add(m, inner_start + part.start(), corporate=True)
    for m in NARRATIVE.finditer(text):
        add(m)
    for m in BARE_MULTI.finditer(text):
        if m.group("more"):
            add(m)
    return out


def _bibliography(text: str) -> list[Found]:
    out: list[Found] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        start, body = pos, line.rstrip("\n")
        pos += len(line)
        lead = LEAD.match(body)
        off = lead.end() if lead else 0
        entry = body[off:]
        if len(entry) < 25:
            continue
        for pattern in (APA, VANCOUVER, ISO):
            m = pattern.match(entry)
            if not m:
                continue
            authors = m.group("authors").strip().rstrip(",")
            first = re.match(SURNAME, authors.title() if pattern is ISO else authors)
            if not first or not _author_ok(first.group(0)):
                break
            doi = DOI.search(entry)
            out.append(
                Found(
                    "bibliography",
                    start + off,
                    start + len(body),
                    entry.strip(),
                    author=first.group(0),
                    year=int(m.group("year")),
                    suffix=m.group("suffix") or "",
                    title=m.group("title").strip().rstrip("."),
                    authors_text=authors[:500],
                    doi=_clean_doi(doi.group(1)) if doi else None,
                )
            )
            break
    return out


def find(text: str) -> list[Found]:
    """All references in ``text``, in order. A bibliography entry wins over the
    in-text citations and DOI inside it (it is one mention, not three)."""
    bib = _bibliography(text)
    spans = [(f.start, f.end) for f in bib]

    def free(a: int, b: int) -> bool:
        return not any(a < y and x < b for x, y in spans)

    hits = list(bib)
    hits += [f for f in _in_text(text) if free(f.start, f.end)]
    for m in DOI.finditer(text):
        if free(m.start(), m.end()):
            doi = _clean_doi(m.group(1))
            hits.append(
                Found("doi", m.start(), m.start() + len(doi), m.group(1)[: len(doi)], doi=doi)
            )
    return sorted(hits, key=lambda f: f.start)
