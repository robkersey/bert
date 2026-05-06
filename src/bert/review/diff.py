"""Semantic diff between two profile YAMLs.

The on-disk YAML diff (e.g. ``git diff``) is noisy because list ordering and
default-value omission flip around. This produces a small list of human-meaningful
changes: services added/removed/renamed/requirement-changed, characteristics
added/removed, properties changed, etc.
"""

from __future__ import annotations

from bert.ir.schema import Profile, Service


def diff_profiles(a: Profile, b: Profile) -> list[str]:
    out: list[str] = []
    a_svcs = {s.uuid: s for s in a.services}
    b_svcs = {s.uuid: s for s in b.services}

    for uuid in sorted(set(a_svcs) | set(b_svcs)):
        sa, sb = a_svcs.get(uuid), b_svcs.get(uuid)
        if sa is None and sb is not None:
            out.append(f"+ service {uuid} {sb.name or ''} ({sb.requirement.value})")
            continue
        if sb is None and sa is not None:
            out.append(f"- service {uuid} {sa.name or ''} ({sa.requirement.value})")
            continue
        assert sa is not None and sb is not None
        out.extend(_diff_service(sa, sb))

    if a.metadata.version != b.metadata.version:
        out.append(f"~ metadata.version {a.metadata.version} → {b.metadata.version}")
    if a.advertising != b.advertising:
        out.append("~ advertising differs")
    if a.gap != b.gap:
        out.append("~ gap differs")

    a_tcs = {t.id: t for t in a.test_cases}
    b_tcs = {t.id: t for t in b.test_cases}
    for tcid in sorted(set(a_tcs) | set(b_tcs)):
        if tcid not in a_tcs:
            out.append(f"+ test_case {tcid}")
        elif tcid not in b_tcs:
            out.append(f"- test_case {tcid}")

    return out


def _diff_service(a: Service, b: Service) -> list[str]:
    out: list[str] = []
    if a.requirement != b.requirement:
        out.append(
            f"~ service {a.uuid} requirement {a.requirement.value} → {b.requirement.value}"
        )
    if (a.name or "") != (b.name or ""):
        out.append(f"~ service {a.uuid} name {a.name!r} → {b.name!r}")
    a_chars = {c.uuid: c for c in a.characteristics}
    b_chars = {c.uuid: c for c in b.characteristics}
    for uuid in sorted(set(a_chars) | set(b_chars)):
        ca, cb = a_chars.get(uuid), b_chars.get(uuid)
        if ca is None:
            out.append(f"+ {a.uuid}/{uuid}")
        elif cb is None:
            out.append(f"- {a.uuid}/{uuid}")
        else:
            if ca.requirement != cb.requirement:
                out.append(
                    f"~ {a.uuid}/{uuid} requirement {ca.requirement.value} → {cb.requirement.value}"
                )
            pa = sorted(p.value for p in ca.properties)
            pb = sorted(p.value for p in cb.properties)
            if pa != pb:
                out.append(f"~ {a.uuid}/{uuid} properties {pa} → {pb}")
    return out
