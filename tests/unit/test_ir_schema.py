"""IR schema round-trip + validation."""

from __future__ import annotations

import pytest

from bert.ir import (
    Characteristic,
    CharacteristicProperty,
    IRLoadError,
    Metadata,
    Procedure,
    Profile,
    Requirement,
    Service,
    TestCase,
    TestCaseSource,
    assert_runnable,
    dump_yaml,
    load_packaged,
)
from bert.ir.schema import IntervalBoundsMs


def _hrm_profile() -> Profile:
    return Profile(
        metadata=Metadata(name="Test", abbrev="T", version="1.0"),
        services=[
            Service(
                uuid="0x180D",
                name="Heart Rate",
                requirement=Requirement.MANDATORY,
                characteristics=[
                    Characteristic(
                        uuid="0x2A37",
                        name="Heart Rate Measurement",
                        requirement=Requirement.MANDATORY,
                        properties=[CharacteristicProperty.NOTIFY],
                        cccd=Requirement.MANDATORY,
                    )
                ],
            ),
        ],
        procedures=[
            Procedure(
                id="hrm_cad",
                description="cadence",
                bounds={"interval_ms": IntervalBoundsMs(min=250, max=2000)},
            ),
        ],
        test_cases=[
            TestCase(
                id="TC1",
                title="HRM cadence",
                procedure="subscribe_and_count",
                source=TestCaseSource.HOST,
                bound="hrm_cad",
            )
        ],
    )


def test_uuid_normalisation() -> None:
    s = Service(uuid="0x180d")
    assert s.uuid == "0x180D"


def test_round_trip_yaml() -> None:
    p = _hrm_profile()
    text = dump_yaml(p)
    assert "0x180D" in text
    assert "interval_ms" in text


def test_assert_runnable_passes_for_clean_profile() -> None:
    p = _hrm_profile()
    assert_runnable(p)  # should not raise


def test_assert_runnable_rejects_unreviewed() -> None:
    p = _hrm_profile()
    p.services[0].needs_review = True
    with pytest.raises(IRLoadError) as exc:
        assert_runnable(p)
    assert "needs_review" in str(exc.value)


def test_assert_runnable_rejects_unknown_procedure_ref() -> None:
    p = _hrm_profile()
    p.test_cases[0].bound = "no_such_procedure"
    with pytest.raises(IRLoadError) as exc:
        assert_runnable(p)
    assert "no_such_procedure" in str(exc.value)


def test_assert_runnable_rejects_duplicate_test_case_ids() -> None:
    p = _hrm_profile()
    p.test_cases.append(p.test_cases[0].model_copy())
    with pytest.raises(IRLoadError) as exc:
        assert_runnable(p)
    assert "duplicate test_case id" in str(exc.value)


def test_assert_runnable_requires_cccd_for_notify() -> None:
    p = _hrm_profile()
    p.services[0].characteristics[0].cccd = Requirement.OPTIONAL
    with pytest.raises(IRLoadError) as exc:
        assert_runnable(p)
    assert "CCCD" in str(exc.value)


def test_unreviewed_nodes_path() -> None:
    p = _hrm_profile()
    p.services[0].characteristics[0].needs_review = True
    paths = p.unreviewed_nodes()
    assert any("services" in path and "characteristics" in path for path in paths)


def test_packaged_heart_rate_loads_and_is_runnable() -> None:
    p = load_packaged("heart_rate")
    assert p.metadata.abbrev == "HRP"
    assert p.service("0x180D") is not None
    assert p.characteristic("0x180D", "0x2A37") is not None
    assert_runnable(p)


def test_packaged_battery_service_loads_and_is_runnable() -> None:
    p = load_packaged("battery_service")
    assert p.metadata.abbrev == "BAS"
    assert_runnable(p)


def test_packaged_device_information_loads_and_is_runnable() -> None:
    p = load_packaged("device_information")
    assert p.metadata.abbrev == "DIS"
    assert_runnable(p)
