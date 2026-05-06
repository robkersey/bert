"""Profile-vs-profile semantic diff."""

from __future__ import annotations

from bert.ir import (
    Characteristic,
    CharacteristicProperty,
    Metadata,
    Profile,
    Requirement,
    Service,
)
from bert.review.diff import diff_profiles


def _profile(version: str = "1.0", with_optional_char: bool = False) -> Profile:
    chars = [
        Characteristic(
            uuid="0x2A37",
            requirement=Requirement.MANDATORY,
            properties=[CharacteristicProperty.NOTIFY],
            cccd=Requirement.MANDATORY,
        )
    ]
    if with_optional_char:
        chars.append(
            Characteristic(
                uuid="0x2A38",
                requirement=Requirement.OPTIONAL,
                properties=[CharacteristicProperty.READ],
            )
        )
    return Profile(
        metadata=Metadata(name="HRP", abbrev="HRP", version=version),
        services=[
            Service(uuid="0x180D", requirement=Requirement.MANDATORY, characteristics=chars)
        ],
    )


def test_identical_profiles_diff_empty() -> None:
    assert diff_profiles(_profile(), _profile()) == []


def test_added_optional_characteristic_shows_up() -> None:
    a = _profile()
    b = _profile(with_optional_char=True)
    out = diff_profiles(a, b)
    assert any("0x2A38" in line and line.startswith("+") for line in out)


def test_version_change_shows_up() -> None:
    out = diff_profiles(_profile("1.0"), _profile("1.1"))
    assert any("metadata.version" in line for line in out)
