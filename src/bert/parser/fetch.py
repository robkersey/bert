"""Content-addressed fetch for SIG profile docs.

Caches under ``~/.cache/bert/specs/<sha256>``. Produces the SHA256 the
parser stamps into the IR's ``metadata.source_doc_sha256`` so the
extracted IR is reproducible from the same bytes.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

_DEFAULT_CACHE = Path.home() / ".cache" / "bert" / "specs"


@dataclass(frozen=True)
class FetchedDoc:
    source: str  # original URL or file path
    bytes_sha256: str
    content: bytes
    content_type: str


def fetch(source: str, *, cache_dir: Path | None = None) -> FetchedDoc:
    cache = cache_dir or Path(os.environ.get("BERT_CACHE_DIR", _DEFAULT_CACHE))
    cache.mkdir(parents=True, exist_ok=True)

    if source.startswith(("http://", "https://")):
        return _fetch_http(source, cache)
    return _fetch_local(source)


def _fetch_http(url: str, cache: Path) -> FetchedDoc:
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        resp = client.get(url)
        resp.raise_for_status()
    body = resp.content
    sha = hashlib.sha256(body).hexdigest()
    out = cache / sha
    if not out.exists():
        out.write_bytes(body)
    ct = resp.headers.get("content-type", "")
    log.info("fetched %d bytes from %s (sha256=%s)", len(body), url, sha[:16])
    return FetchedDoc(source=url, bytes_sha256=sha, content=body, content_type=ct)


def _fetch_local(path: str) -> FetchedDoc:
    p = Path(path).expanduser()
    body = p.read_bytes()
    sha = hashlib.sha256(body).hexdigest()
    ct = "text/html" if p.suffix.lower() in {".html", ".htm"} else "application/pdf"
    return FetchedDoc(source=str(p), bytes_sha256=sha, content=body, content_type=ct)
