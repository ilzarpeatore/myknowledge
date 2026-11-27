"""Build small synthetic PDFs with real text layers for tests (no external tools)."""


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]], outline: list[tuple[str, int, int]] | None = None) -> bytes:
    """One inner list of text lines per page; an empty list makes a page without
    text (like a scanned page). Latin-1 characters are supported. ``outline``: PDF
    bookmarks as (title, 0-based page, level 0 or 1), in reading order."""
    objects: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    for i, lines in enumerate(pages):
        content_id = 5 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        ops = ["BT /F1 11 Tf 14 TL 50 740 Td"]
        for line in lines:
            ops.append(f"({_escape(line)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    if outline:
        _add_outline(objects, outline, page_ids=[4 + 2 * i for i in range(n)])
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def _add_outline(objects: list[bytes], outline: list[tuple[str, int, int]], page_ids) -> None:
    root = len(objects) + 1
    ids = [root + 1 + i for i in range(len(outline))]
    parent: list[int] = []
    children: dict[int, list[int]] = {root: []}
    top = None
    for i, (_, _, level) in enumerate(outline):
        p = ids[top] if level > 0 and top is not None else root
        if p == root:
            top = i
        parent.append(p)
        children.setdefault(p, []).append(i)
        children.setdefault(ids[i], [])

    def span(k: list[int]) -> str:
        return f"/First {ids[k[0]]} 0 R /Last {ids[k[-1]]} 0 R /Count {len(k)}"

    objects.append(f"<< /Type /Outlines {span(children[root])} >>".encode())
    for i, (title, page, _) in enumerate(outline):
        sib = children[parent[i]]
        k = sib.index(i)
        parts = [f"/Title ({_escape(title)}) /Parent {parent[i]} 0 R"]
        parts.append(f"/Dest [{page_ids[page]} 0 R /Fit]")
        if k > 0:
            parts.append(f"/Prev {ids[sib[k - 1]]} 0 R")
        if k + 1 < len(sib):
            parts.append(f"/Next {ids[sib[k + 1]]} 0 R")
        if children[ids[i]]:
            parts.append(span(children[ids[i]]))
        objects.append(("<< " + " ".join(parts) + " >>").encode("latin-1"))
    objects[0] = f"<< /Type /Catalog /Pages 2 0 R /Outlines {root} 0 R >>".encode()
