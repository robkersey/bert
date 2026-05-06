"""Hardware-in-the-loop smoke test against a real HRP device.

Opt-in via ``pytest -m hwil``. Requires:

  * an HCI dongle (nRF52840 with Zephyr ``hci_uart``) plugged in
  * a sniffer dongle (nRF52840 with Nordic sniffer firmware)
  * a known-good HRP DUT advertising under name ``--bert-dut-name`` or
    address ``BERT_HWIL_DUT_ADDR`` env var.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from bert.ir import load_packaged
from bert.runner.core import RunConfig, Runner

pytestmark = pytest.mark.hwil


def test_real_hrp_passes(tmp_path):
    addr = os.environ.get("BERT_HWIL_DUT_ADDR")
    name = os.environ.get("BERT_HWIL_DUT_NAME")
    if not (addr or name):
        pytest.skip("set BERT_HWIL_DUT_ADDR or BERT_HWIL_DUT_NAME to run HWIL test")
    cfg = RunConfig(
        profile=load_packaged("heart_rate"),
        dut_address=addr,
        dut_name=name,
        report_dir=tmp_path,
        sniffer_enabled=True,
    )
    result = asyncio.run(Runner(cfg).run())
    assert result.overall == "passed", [
        (r.test_case.id, r.status, r.failure.message if r.failure else r.error)
        for r in result.results
    ]
    assert (tmp_path / result.started_at.strftime("%Y%m%dT%H%M%SZ") / "capture.pcapng").exists()
