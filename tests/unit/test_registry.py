"""Procedure registry: built-in + HRP procedures resolve."""

from __future__ import annotations

from bert.runner import registry


def test_generic_procedures_registered() -> None:
    names = registry.all_names()
    assert "discover_mandatory_services" in names
    assert "read_characteristic" in names
    assert "subscribe_and_count" in names


def test_hrp_procedures_registered_via_entry_point() -> None:
    # Entry-point load is lazy. all_names() triggers it.
    names = registry.all_names()
    assert "subscribe_and_measure_cadence" in names
    assert "hrcp_reset_behaviour" in names
    assert "ota_advertising_interval" in names
