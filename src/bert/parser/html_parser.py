"""Parse a SIG profile HTML page into draft IR.

The SIG ships a nicely-structured HTML version of recent profile specs. We
look for tables that list services / characteristics with UUIDs and
mandatory/optional/conditional flags, and extract everything we can with
attached confidence scores.

This is intentionally pragmatic — it does NOT attempt to fully understand
the prose. The human review step is the safety net.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from bs4 import BeautifulSoup

from bert.ir.schema import (
    Characteristic,
    CharacteristicProperty,
    Metadata,
    Profile,
    Requirement,
    Service,
)
from bert.parser.confidence import UUID_RE, score_requirement

log = logging.getLogger(__name__)


_REQ_MAP = {
    "m": Requirement.MANDATORY,
    "mandatory": Requirement.MANDATORY,
    "o": Requirement.OPTIONAL,
    "optional": Requirement.OPTIONAL,
    "c": Requirement.CONDITIONAL,
    "c.1": Requirement.CONDITIONAL,
    "c.2": Requirement.CONDITIONAL,
    "conditional": Requirement.CONDITIONAL,
    "x": Requirement.EXCLUDED,
    "excluded": Requirement.EXCLUDED,
}

_PROPERTY_KEYWORDS = {
    "read": CharacteristicProperty.READ,
    "write": CharacteristicProperty.WRITE,
    "write without response": CharacteristicProperty.WRITE_NO_RESP,
    "write_no_response": CharacteristicProperty.WRITE_NO_RESP,
    "notify": CharacteristicProperty.NOTIFY,
    "indicate": CharacteristicProperty.INDICATE,
    "broadcast": CharacteristicProperty.BROADCAST,
}


def parse_html(content: bytes, *, source_url: str | None = None, sha256: str | None = None) -> Profile:
    soup = BeautifulSoup(content, "lxml")

    title = (soup.title.string if soup.title else None) or "Unknown Profile"
    name, version, abbrev = _extract_metadata(title, soup)

    services = _extract_services(soup)

    metadata = Metadata(
        name=name,
        abbrev=abbrev,
        version=version,
        source_doc=source_url if source_url and source_url.startswith("http") else None,
        source_doc_sha256=sha256,
        role_under_test="peripheral",
        reviewer=None,
        reviewed_at=date.today() if False else None,
    )

    return Profile(
        schema_version=1,
        metadata=metadata,
        services=services,
    )


# --------------------------------------------------------------------------- #


def _extract_metadata(title: str, soup: BeautifulSoup) -> tuple[str, str, str]:
    name = title.split("|")[0].strip()
    # Try to find a version like "1.0", "1.1.0" near the top of the doc.
    head = soup.get_text(separator=" ", strip=True)[:2000]
    m = re.search(r"\bv?(\d+\.\d+(?:\.\d+)?)\b", head)
    version = m.group(1) if m else "unknown"
    abbrev = "".join(w[0] for w in re.findall(r"[A-Z][a-z]+", name))[:5] or "PROF"
    return name, version, abbrev


def _extract_services(soup: BeautifulSoup) -> list[Service]:
    """Look for tables that mention 'Service' + a 16-bit UUID; treat each row as a service."""

    services: list[Service] = []
    seen_uuids: set[str] = set()
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        is_service_table = any("service" in h for h in headers) or any(
            "uuid" in h for h in headers
        )
        if not is_service_table:
            continue
        for row in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if not cells:
                continue
            row_text = " ".join(cells)
            uuid_m = UUID_RE.search(row_text)
            if not uuid_m:
                continue
            uuid = uuid_m.group(0)
            if uuid in seen_uuids:
                continue
            req, req_text = _classify_requirement(row_text)
            confidence, needs_review = score_requirement(req_text or row_text)
            name = _name_from_row(cells, uuid)
            services.append(
                Service(
                    uuid=uuid,
                    name=name,
                    requirement=req,
                    characteristics=[],
                    confidence=confidence,
                    needs_review=needs_review,
                    source_anchor=_table_anchor(table),
                )
            )
            seen_uuids.add(uuid)

    # Same shape but for characteristic-style tables.
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not any("characteristic" in h for h in headers):
            continue
        for row in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if not cells:
                continue
            row_text = " ".join(cells)
            uuid_m = UUID_RE.search(row_text)
            if not uuid_m:
                continue
            uuid = uuid_m.group(0)
            req, req_text = _classify_requirement(row_text)
            confidence, needs_review = score_requirement(req_text or row_text)
            props = _extract_properties(row_text)
            name = _name_from_row(cells, uuid)
            char = Characteristic(
                uuid=uuid,
                name=name,
                requirement=req,
                properties=props,
                cccd=Requirement.MANDATORY
                if (CharacteristicProperty.NOTIFY in props or CharacteristicProperty.INDICATE in props)
                else Requirement.OPTIONAL,
                confidence=confidence,
                needs_review=needs_review or not props,
                source_anchor=_table_anchor(table),
            )
            # Attach to the first service we already extracted; the human reviewer
            # repositions if the parser guessed wrong.
            if services:
                services[0].characteristics.append(char)

    return services


def _classify_requirement(text: str) -> tuple[Requirement, str]:
    t = text.lower()
    # Look for an explicit requirement column word.
    for token in re.findall(r"\b\w[\w.]*\b", t):
        if token in _REQ_MAP:
            return _REQ_MAP[token], token
    if "shall" in t or "mandatory" in t:
        return Requirement.MANDATORY, "shall/mandatory in text"
    if "optional" in t or "may" in t:
        return Requirement.OPTIONAL, "optional/may in text"
    return Requirement.OPTIONAL, ""


def _extract_properties(text: str) -> list[CharacteristicProperty]:
    t = text.lower()
    out: list[CharacteristicProperty] = []
    for kw, prop in _PROPERTY_KEYWORDS.items():
        if kw in t and prop not in out:
            out.append(prop)
    return out


def _name_from_row(cells: list[str], uuid: str) -> str | None:
    for c in cells:
        if uuid in c:
            continue
        if len(c) > 2 and not UUID_RE.fullmatch(c):
            return c.strip().title()
    return None


def _table_anchor(table: object) -> str | None:
    el = table
    while el is not None and getattr(el, "name", None):
        h = getattr(el, "find_previous", lambda *_a, **_k: None)(["h1", "h2", "h3", "h4"])
        if h is not None:
            return h.get_text(strip=True)
        el = getattr(el, "parent", None)
    return None
