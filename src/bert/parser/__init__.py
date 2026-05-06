"""SIG profile-doc parsers (HTML preferred, PDF fallback)."""

from __future__ import annotations

import logging

from bert.ir.schema import Profile
from bert.parser.fetch import fetch
from bert.parser.html_parser import parse_html
from bert.parser.pdf_parser import parse_pdf

log = logging.getLogger(__name__)


def parse_source(source: str) -> Profile:
    """Fetch ``source`` (URL or local path) and parse into a draft Profile."""

    doc = fetch(source)
    if "html" in doc.content_type or doc.source.endswith((".html", ".htm")):
        log.info("parsing %s as HTML", doc.source)
        return parse_html(doc.content, source_url=doc.source, sha256=doc.bytes_sha256)
    if "pdf" in doc.content_type or doc.source.endswith(".pdf"):
        log.info("parsing %s as PDF", doc.source)
        return parse_pdf(doc.content, source_url=doc.source, sha256=doc.bytes_sha256)
    if doc.content[:5] == b"%PDF-":
        return parse_pdf(doc.content, source_url=doc.source, sha256=doc.bytes_sha256)
    return parse_html(doc.content, source_url=doc.source, sha256=doc.bytes_sha256)


__all__ = ["parse_html", "parse_pdf", "parse_source"]
