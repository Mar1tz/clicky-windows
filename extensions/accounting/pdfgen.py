"""A minimal PDF writer: text pages, Helvetica, nothing else.

Customer statements, reminder letters and the demo client's sample
invoices need to be real PDFs (the extract tools read them back with
pypdf), but a PDF library is a dependency this app does not otherwise
need. Plain text on A4 pages is ~60 lines of the PDF spec, so here it is.
"""
from __future__ import annotations

import os

PAGE_W, PAGE_H = 595, 842            # A4 in points
MARGIN_X, TOP = 50, 800
LEADING = 14
LINES_PER_PAGE = 54


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def write_pdf(path: str, lines: list[str], *, title: str = "", font_size: int = 10) -> str:
    """Write `lines` (a title line first if given) as a text PDF. Returns path."""
    body = ([title, ""] if title else []) + [l.rstrip() for l in lines]
    pages = [body[i:i + LINES_PER_PAGE] for i in range(0, max(1, len(body)), LINES_PER_PAGE)] or [[]]

    objects: list[bytes] = []
    # 1 catalog, 2 pages, 3 font, then (page, content) pairs
    n_pages = len(pages)
    page_ids = [4 + 2 * i for i in range(n_pages)]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for i, page in enumerate(pages):
        stream = bytearray(f"BT /F1 {font_size} Tf {MARGIN_X} {TOP} Td {LEADING} TL\n".encode())
        for j, line in enumerate(page):
            size = font_size + 4 if (title and i == 0 and j == 0) else font_size
            stream += f"/F1 {size} Tf ".encode() + b"(" + _escape(line) + b") Tj T*\n"
        stream += b"ET"
        content_id = page_ids[i] + 1
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                       f"/Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode())
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + bytes(stream) + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(bytes(out))
    return path
