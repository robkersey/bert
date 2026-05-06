"""Semantic validation of a loaded profile, beyond pydantic schema checks.

The runner invokes :func:`assert_runnable` before executing any test case; it
will refuse to run an IR that still has ``needs_review`` flags or that has
internal inconsistencies the schema can't express (e.g. a test case
referencing a procedure id that doesn't exist).
"""

from __future__ import annotations

from bert.ir.loader import IRLoadError
from bert.ir.schema import (
    CharacteristicProperty,
    Profile,
    Requirement,
    TestCaseSource,
)


def assert_runnable(profile: Profile, *, allow_draft: bool = False) -> None:
    """Raise :class:`IRLoadError` if the profile is not safe to execute."""

    problems: list[str] = []

    if not allow_draft:
        unreviewed = profile.unreviewed_nodes()
        if unreviewed:
            preview = ", ".join(unreviewed[:5])
            more = f" (+{len(unreviewed) - 5} more)" if len(unreviewed) > 5 else ""
            problems.append(
                f"{len(unreviewed)} node(s) still flagged needs_review: {preview}{more}. "
                f"Run `bert ir review` to address them, or pass --allow-draft to override."
            )

    procedure_ids = {p.id for p in profile.procedures}
    for tc in profile.test_cases:
        if tc.bound and tc.bound not in procedure_ids:
            problems.append(
                f"test_case {tc.id!r} references unknown procedure {tc.bound!r}"
            )

    seen_ids: set[str] = set()
    for tc in profile.test_cases:
        if tc.id in seen_ids:
            problems.append(f"duplicate test_case id {tc.id!r}")
        seen_ids.add(tc.id)

    for svc in profile.services:
        for char in svc.characteristics:
            needs_cccd = (
                CharacteristicProperty.NOTIFY in char.properties
                or CharacteristicProperty.INDICATE in char.properties
            )
            if needs_cccd and char.cccd not in {Requirement.MANDATORY, Requirement.CONDITIONAL}:
                problems.append(
                    f"{svc.uuid}/{char.uuid}: notify/indicate property requires CCCD requirement to be mandatory or conditional"
                )

    ota_cases = [tc for tc in profile.test_cases if tc.source == TestCaseSource.OTA]
    if ota_cases and profile.advertising is None and not any(
        tc.bound and (proc := profile.procedure(tc.bound)) and proc.bounds
        for tc in ota_cases
    ):
        problems.append(
            "profile has OTA-sourced test cases but no advertising/procedure bounds defined; "
            "the sniffer assertions will have nothing to check against."
        )

    if problems:
        raise IRLoadError("Profile not runnable:\n  - " + "\n  - ".join(problems))
