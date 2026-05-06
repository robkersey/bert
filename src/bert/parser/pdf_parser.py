"""PDF fallback when the SIG only ships a PDF profile spec.

We use ``pymupdf`` (``fitz``). The strategy mirrors the HTML parser: extract
tables that look like service / characteristic listings; everything goes
through the same confidence scoring and is marked needs_review by default
because table layout in PDF is much less reliable than HTML.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from bert.ir.schema import Metadata, Profile, Requirement, Service
from bert.parser.confidence import UUID_RE

log = logging.getLogger(__name__)


def parse_pdf(content: bytes, *, source_url: str | None = None, sha256: str | None = None) -> Profile:
    try:
        import fitz  # pymupdf
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"pymupdf is required to parse PDFs: {exc}") from exc

    doc = fitz.open(stream=content, filetype="pdf")
    full_text = "\n".join(page.get_text("text") for page in doc)

    name, version, abbrev = _metadata_from_text(full_text)

    services: list[Service] = []
    seen: set[str] = set()
    for uuid in UUID_RE.findall(full_text):
        if uuid in seen:
            continue
        seen.add(uuid)
        services.append(
            Service(
                uuid=uuid,
                name=None,
                requirement=Requirement.OPTIONAL,  # parser refuses to guess from PDF
                confidence=0.4,
                needs_review=True,
            )
        )

    return Profile(
        schema_version=1,
        metadata=Metadata(
            name=name,
            abbrev=abbrev,
            version=version,
            source_doc=source_url if source_url and source_url.startswith("http") else None,
            source_doc_sha256=sha256,
            role_under_test="peripheral",
            reviewer=None,
            reviewed_at=None,
        ),
        services=services,
    )


def _metadata_from_text(text: str) -> tuple[str, str, str]:
    head = text[:2000]
    m_v = re.search(r"\bv(\d+\.\d+(?:\.\d+)?)\b", head)
    version = m_v.group(1) if m_v else "unknown"
    m_n = re.search(r"^([A-Z][\w \-]+?(?:Profile|Service))", head, re.MULTILINE)
    name = m_n.group(1).strip() if m_n else "Unknown Profile"
    abbrev = "".join(w[0] for w in re.findall(r"[A-Z][a-z]+", name))[:5] or "PROF"
    _ = date  # kept for future timestamping
    return name, version, abbrev
