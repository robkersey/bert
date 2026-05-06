# Bert — BLE Profile Compliance Tester

Verify that a physical Bluetooth Low Energy device adheres to a published
Bluetooth SIG profile (e.g. Heart Rate Profile 1.0). Bert acts as the
central / Collector, exercises the Device Under Test (DUT), and checks both
host-side observations and over-the-air traffic captured by a separate
sniffer — then emits a JUnit/Markdown/HTML report.

```
                    ┌───────────────┐    HCI over USB-CDC   ┌──────────────┐
                    │  Bert (this   │◀────────────────────▶│ nRF52840 #1  │
                    │  Python tool) │                      │  (HCI)       │
                    │   on Bumble   │                      └──────┬───────┘
                    │  pure-Python  │                             │ 2.4 GHz
                    │   BT stack    │                             ▼
                    └───────┬───────┘                      ┌──────────────┐
                            │ extcap subprocess           ⤷│ Device Under │
                            │ → PCAP-NG                    │   Test       │
                            ▼                              │  (your HW)   │
                    ┌───────────────┐    OTA radio  ┌──────┴──────────────┘
                    │ nRF52840 #2   │◀──────────────┘
                    │  (Sniffer)    │
                    └───────────────┘
```

## Table of contents

- [Why this design](#why-this-design)
- [Hardware required](#hardware-required)
- [Installation](#installation)
- [Quick start](#quick-start)
- [CLI reference](#cli-reference)
  - [`bert run`](#bert-run)
  - [`bert doctor`](#bert-doctor)
  - [`bert profiles`](#bert-profiles)
  - [`bert dongles` / `bert init-dongles` / `bert flash-firmware`](#bert-dongles)
  - [`bert firmware`](#bert-firmware)
  - [`bert ir parse / review / validate / diff`](#bert-ir)
  - [`bert pcap analyse`](#bert-pcap)
- [Bundled profiles](#bundled-profiles)
- [Authoring a new profile spec](#authoring-a-new-profile-spec)
- [How a run works (step by step)](#how-a-run-works-step-by-step)
- [Reading the report](#reading-the-report)
- [Adding a custom test procedure](#adding-a-custom-test-procedure)
- [Project layout](#project-layout)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Architecture deep-dive](#architecture-deep-dive)
- [License](#license)

---

## Why this design

Off-the-shelf BLE testing on a developer laptop usually means using the host
OS Bluetooth stack (CoreBluetooth on macOS, BlueZ on Linux), which:

- hides HCI-level detail (MTU exchange, pairing internals, link-layer events),
- behaves differently from machine to machine,
- fights with whatever Bluetooth peripherals the OS has already paired,
- and on macOS will sometimes outright claim a USB BT controller before your
  test framework can.

Bert sidesteps the host stack entirely:

- **[Bumble](https://github.com/google/bumble)** (Google's pure-Python BT stack)
  implements GAP/ATT/GATT/SMP/L2CAP in our Python process and speaks HCI
  directly to a controller dongle.
- **nRF52840 USB dongle (HCI)** — flashed with Zephyr's `hci_uart` sample so
  it presents itself as a USB-CDC virtual serial port. The OS sees a generic
  COM device, **not** a Bluetooth controller, so it doesn't try to claim it.
- **nRF52840 USB dongle (sniffer)** — flashed with Nordic's nRF Sniffer for
  Bluetooth LE firmware, driven by Bert via the vendored Nordic extcap plugin
  (Wireshark not required).

Result: one `pip install`, two ~$10 dongles, identical behaviour on macOS,
Linux and Windows.

---

## Hardware required

| Item | Quantity | Notes |
|---|---|---|
| Nordic nRF52840 USB Dongle (`nRF52840-DONGLE`) | 2 | ~$10 each at DigiKey/Mouser. One becomes the HCI controller, the other the sniffer. |
| Your DUT | 1 | The BLE peripheral you want to verify. |
| (optional) USB hub | 1 | Convenient if your laptop only has one or two ports. |

> [!WARNING]
> Do **not** flash the Zephyr `hci_usb` sample on the HCI dongle. It presents
> USB class 0xE0 which macOS's `bluetoothd` will grab and the dongle will
> stop responding to Bert. Bert uses Zephyr's `hci_uart` sample over USB-CDC
> for exactly this reason.

---

## Installation

```bash
# requires Python 3.11+
pip install bert-ble-tester
```

For development (editable install from a clone):

```bash
git clone https://github.com/yourname/bert.git
cd bert
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Verify the install:

```bash
bert doctor
```

This checks Python, dependencies, attached dongles, and the vendored sniffer
plugin. It exits non-zero with an actionable message for anything missing.

---

## Quick start

Plug in two **fresh** nRF52840 dongles and run:

```bash
# 1. Flash both dongles (one-time setup; idempotent)
bert flash-firmware

# 2. Confirm both dongles appear with their roles
bert init-dongles

# 3. Run the Heart Rate Profile suite against your DUT
bert run --profile heart-rate --dut-name HRM01
```

Each run writes:

```
runs/<UTC-timestamp>/
├── capture.pcapng         # Wireshark-openable over-the-air capture
├── report.junit.xml       # CI-consumable
├── report.html            # human-readable, timeline waterfall + packet detail
└── report.md              # paste-into-PR friendly
```

Exit code: `0` on pass, `1` on any failure or error.

---

## CLI reference

Get help on any command with `--help`, e.g. `bert run --help`.

### `bert run`

Run a profile against a DUT.

```bash
bert run --profile heart-rate --dut-name HRM01
bert run --profile battery-service --dut-addr AA:BB:CC:DD:EE:FF
bert run --profile ./my-custom-profile.yaml --dut-name MyDUT --no-sniffer
```

Common options:

| flag | default | purpose |
|---|---|---|
| `--profile NAME-OR-PATH` | required | Bundled name (`heart-rate`, `battery-service`, `device-information`) **or** path to a `profile.yaml`. |
| `--dut-addr XX:XX:XX:XX:XX:XX` | — | Filter scan results by BLE address. One of `--dut-addr` or `--dut-name` is required. |
| `--dut-name NAME` | — | Filter scan results by advertised local name. |
| `--passkey N` | — | Pairing passkey if the DUT requires one. JustWorks pairing needs no flag. |
| `--report-dir PATH` | `runs` | Where to write each run's artefacts. |
| `--report-format LIST` | `junit,markdown,html` | Comma-separated subset. |
| `--repeats N` / `--quorum K` | `1` / `1` | Repeat each test N times; pass if K succeed. Useful in noisy RF environments. |
| `--sniffer / --no-sniffer` | on | Capture OTA traffic. Disable for headless smoke-tests. |
| `--hci-transport STRING` | auto-detected | Override Bumble's HCI transport (e.g. `serial:/dev/cu.usbmodemX,1000000`). |
| `--allow-draft` | off | Allow profile YAML with `needs_review:true` items. **Never** use in CI. |
| `--verbose / -v` | off | DEBUG logging. |

If two devices match the scan filter, the run aborts and asks you to tighten
the filter — Bert will not silently pick one.

### `bert doctor`

Verify the local install. Checks Python version, every required dependency,
attached dongles + their burned roles, and presence of the vendored Nordic
extcap plugin. Useful as a first step on a new machine and as a CI smoke check.

```bash
bert doctor
```

### `bert profiles`

```bash
bert profiles list                 # table of bundled profiles
bert profiles show heart-rate      # metadata + every test case
```

### `bert dongles`

```bash
bert dongles list                  # show attached, registered dongles
bert init-dongles                  # confirm both roles came up after flashing
bert flash-firmware                # interactively flash both dongles
bert flash-firmware --dongle hci   # flash one role at a time
bert flash-firmware --firmware-hci ./hci_uart.hex --firmware-sniffer ./sniffer.hex
```

`flash-firmware` is fully interactive. For each role it prompts you to plug
in the dongle and press its small **RESET** button (the side button, not the
white user button) to enter Nordic's USB DFU bootloader. Bert detects the
bootloader on USB, runs `nrfutil pkg generate` + `nrfutil dfu usb-serial` to
write the firmware, then waits for the dongle to re-enumerate as the
application device. After a successful flash it records the dongle's factory
USB serial number against the role in `~/.config/bert/dongles.json` (or
`%USERPROFILE%\.config\bert\dongles.json` on Windows) so subsequent
`bert run` invocations can find it without any further configuration.

#### Where firmware comes from

Bert looks for firmware in this order:

1. The `--firmware-hci` / `--firmware-sniffer` CLI flag.
2. A bundled image inside the `bert` wheel under `src/bert/firmware/` (rare).
3. **(sniffer only)** Nordic's own ble-sniffer install at:
   - macOS / Linux: `~/.nrfutil/share/nrfutil-ble-sniffer/firmware/`
   - Windows: `%USERPROFILE%\.nrfutil\share\nrfutil-ble-sniffer\firmware\`
   - Override with the `NRFUTIL_HOME` env var.

   The dongle variant ships there as a pre-packaged DFU bundle:
   `sniffer_nrf52840dongle_nrf52840_<ver>.zip`. Bert picks the highest
   version and flashes it directly (skipping `pkg generate` since it's
   already a DFU package).
4. **GitHub Releases**, downloaded on demand. Bert ships a manifest
   (`src/bert/firmware/manifest.json`) pinning the URL + SHA256 of each
   firmware image. On first flash, the file is fetched from the matching
   GitHub Release, hash-verified, and cached under
   `~/.cache/bert/firmware/` (configurable via `$BERT_FIRMWARE_CACHE` /
   `$XDG_CACHE_HOME`). Subsequent flashes use the cached copy.

To install Nordic's sniffer firmware bundle:

```bash
nrfutil install ble-sniffer
```

The **HCI controller** firmware is built by Bert's `build-firmware` GitHub
Action and published to a GitHub Release; `bert flash-firmware --dongle hci`
downloads it on demand. To pre-fetch (e.g. before going offline):

```bash
bert firmware download
```

If you'd rather build it yourself, point at the resulting hex with the
override flag:

```bash
west init -m https://github.com/zephyrproject-rtos/zephyr --mr v3.7.0 zephyrproject
cd zephyrproject && west update
west build -b nrf52840dongle/nrf52840 zephyr/samples/bluetooth/hci_uart
bert flash-firmware --dongle hci --firmware-hci build/zephyr/zephyr.hex
```

#### Required tooling

Bert shells out to **two** different `nrfutil`s — both are needed:

| tool | install with | used for |
|---|---|---|
| Legacy Python `nrfutil` | `pip install nrfutil` | `pkg generate`, `dfu usb-serial` (the only path that flashes the dongle's Open Bootloader). |
| Rust-based `nrfutil` | [download from Nordic](https://www.nordicsemi.com/Products/Development-tools/nRF-Util) → `nrfutil install ble-sniffer` | Ships the sniffer firmware bundle to `~/.nrfutil/share/`. Optional if you supply `--firmware-sniffer` yourself. |

If you only install the Rust-based one, `bert doctor` will tell you and
point you at `pip install nrfutil`.

#### Platform notes

| | macOS | Windows | Linux |
|---|---|---|---|
| Dongle in DFU mode appears as | `/dev/cu.usbmodem…` | `COM<n>` (inbox CDC driver, no extra install on Win10+) | `/dev/ttyACM<n>` (user must be in `dialout` group) |
| nrfutil home | `~/.nrfutil` | `%USERPROFILE%\.nrfutil` | `~/.nrfutil` |
| Bert config | `~/.config/bert/` | `%USERPROFILE%\.config\bert\` | `~/.config/bert/` |
| Override config dir | `BERT_CONFIG_DIR=…` | `BERT_CONFIG_DIR=…` | `BERT_CONFIG_DIR=…` |

### `bert firmware`

Manage the prebuilt-firmware download cache.

```bash
bert firmware list             # show manifest entries + cache state
bert firmware download         # pre-fetch all firmware (e.g. before offline)
bert firmware download --force # ignore cache; redownload + reverify
bert firmware verify           # rehash every cached file vs the manifest
bert firmware clear-cache      # nuke the local cache
```

The manifest (`src/bert/firmware/manifest.json`) is shipped inside the
Bert wheel and points at a specific GitHub Release tag. SHA256 hashes are
pinned, so an unexpected change in the release file is caught before it
reaches a dongle. See [`docs/firmware-release.md`](docs/firmware-release.md)
for the maintainer-side workflow that builds and publishes new firmware.

### `bert ir`

Author and review profile IR specs.

```bash
# Auto-extract a draft IR from a SIG profile document (HTML preferred, PDF fallback)
bert ir parse https://www.bluetooth.com/specifications/specs/heart-rate-profile-1-0/ \
    --out heart_rate.draft.yaml

# Walk every needs_review node interactively
bert ir review heart_rate.draft.yaml --out heart_rate.yaml

# Confirm a YAML is loadable + runnable
bert ir validate heart_rate.yaml

# Semantic diff between two profile YAMLs (clearer than `git diff`)
bert ir diff a.yaml b.yaml
```

The parser annotates each node with a `confidence` score and a
`needs_review: true` flag when it's unsure. `bert run` **refuses** to load
a profile with any unreviewed nodes — `bert ir review` is the gate that
clears them.

### `bert pcap`

Re-analyse a recorded capture without re-running the DUT — handy for
triaging a flaky CI failure.

```bash
bert pcap analyse runs/20260501T112233Z/capture.pcapng \
    --against heart-rate
```

---

## Bundled profiles

Run `bert profiles list` for the live list. As of v0.1:

| name | profile | tests |
|---|---|---|
| `heart-rate` | Heart Rate Profile 1.0 | 4 |
| `battery-service` | Battery Service 1.0 | 2 |
| `device-information` | Device Information Service 1.1 | 1 |

To inspect a profile's tests:

```bash
bert profiles show heart-rate
```

---

## Authoring a new profile spec

Profiles are described as reviewed YAML at
`src/bert/profiles/<name>/profile.yaml`. The full schema lives in
[`src/bert/ir/schema.py`](src/bert/ir/schema.py); the easiest way to learn
the shape is to read the bundled
[`heart_rate/profile.yaml`](src/bert/profiles/heart_rate/profile.yaml).

**Recommended workflow:**

1. **Parse** a draft from the SIG docs.

   ```bash
   bert ir parse https://www.bluetooth.com/specifications/specs/<your-profile>/ \
       --out my_profile.draft.yaml
   ```

2. **Review** every flagged node interactively. The tool walks each
   `needs_review` node, shows you the source-doc anchor + extracted value,
   and lets you accept / reject / skip. On save it stamps `reviewer:` and
   `reviewed_at:` and clears the flags.

   ```bash
   bert ir review my_profile.draft.yaml --out my_profile.yaml
   ```

3. **Validate** the result.

   ```bash
   bert ir validate my_profile.yaml
   ```

4. **Run** it (no need to install / package the YAML — point at the file):

   ```bash
   bert run --profile ./my_profile.yaml --dut-name MyDUT
   ```

5. To **bundle** it with Bert, drop the YAML at
   `src/bert/profiles/<snake_name>/profile.yaml` and (optionally) profile-specific
   procedures at `src/bert/profiles/<snake_name>/tests.py`. Add an
   entry-point in `pyproject.toml`:

   ```toml
   [project.entry-points."bert.procedures"]
   my_profile = "bert.profiles.my_profile.tests:register"
   ```

### YAML structure (cheat sheet)

```yaml
schema_version: 1
metadata:
  name: My Profile
  abbrev: MYP
  version: "1.0"
  source_doc: https://www.bluetooth.com/...
  role_under_test: peripheral
  reviewer: you@example.com
  reviewed_at: 2026-05-01

services:
  - uuid: "0x180D"
    name: Heart Rate
    requirement: mandatory          # mandatory | optional | conditional | excluded
    characteristics:
      - uuid: "0x2A37"
        requirement: mandatory
        properties: [notify]        # read | write | notify | indicate | …
        cccd: mandatory             # mandatory | conditional if notify/indicate
        value:
          format: struct
          fields:
            - { name: flags, type: uint8 }
            - { name: hr_value, type: uint8_or_uint16, conditional_on: "flags.bit0" }

advertising:
  flags:
    LE_General_Discoverable: required
    BR_EDR_Not_Supported: required
  service_uuids_in_adv_or_scan_response: ["0x180D"]
  interval_ms: { min: 20.0, max: 10240.0 }

gap:
  connectable: true
  min_security_level: 1
  pairing_methods_allowed: [just_works, passkey]
  mtu_min: 23
  mtu_preferred: 247

procedures:
  - id: hrm_notify_cadence
    description: HRM notifications must arrive at the sensor's stated rate.
    bounds:
      interval_ms: { min: 250.0, max: 2000.0 }

test_cases:
  - id: TC_MYP_001
    title: Mandatory services discoverable
    procedure: discover_mandatory_services        # name registered via @testcase
    source: host                                  # host | ota | both
    timeout_s: 30.0
  - id: TC_MYP_002
    title: HRM notifications arrive at expected cadence
    procedure: subscribe_and_measure_cadence
    source: host
    bound: hrm_notify_cadence                     # references procedures[].id
    requires:
      - "services.0x180D.characteristics.0x2A37"
```

Every node accepts optional `confidence` (0..1) and `needs_review: bool`
fields. After human review both should be set so the validator is happy.

---

## How a run works (step by step)

When you invoke `bert run`, the orchestrator
([`src/bert/runner/core.py`](src/bert/runner/core.py)):

1. **Loads + validates the profile YAML.** Refuses unreviewed nodes,
   dangling procedure refs, duplicate test IDs, and notify-without-CCCD.
2. **Discovers the dongles** via USB serial-number prefix matching
   (`BERT-HCI-…` / `BERT-SNF-…`). Aborts with an actionable error if either
   role is missing.
3. **Starts the sniffer subprocess**, writing PCAP-NG to
   `runs/<ts>/capture.pcapng`. Waits for the file to grow past zero bytes
   before continuing.
4. **Brings up Bumble** on the HCI dongle (`Device.with_hci(...)` over the
   serial transport).
5. **Scans for the DUT** with the address/name filter; aborts on ambiguous
   matches.
6. **Connects + exchanges MTU**.
7. **Dispatches each test case.** Procedures live in a registry
   (`@testcase` decorator + `bert.procedures` entry-point group). The
   runner gives each one a `TestContext` exposing `bumble`, `timeline`, and
   the `assert_*` library. Every host-side observation (ATT op, notification,
   conn-param change) is appended to a unified monotonic-ns **timeline**.
8. **Tears down** the connection, **stops the sniffer**, and **folds** the
   PCAP into the same timeline using scapy's BTLE dissector. OTA events
   (`btle.adv`, `att.notify_ota`, `att.mtu_rsp`, etc.) live next to the
   host events, sorted by timestamp, ready for OTA-source assertions.
9. **Renders the report** in the requested formats. Failure records carry
   `(host_event_ids, ota_event_ids, timeline_window)` so the HTML report
   shows the failing log line beside the offending packet bytes.

Test cases marked `applies_if: "<UUID> present"` are auto-skipped when the
profile doesn't include that UUID, with a clear "skipped: applies_if false"
in the report rather than a silent pass.

---

## Reading the report

**`report.html`** — single self-contained file. Top section: pass/fail
banner + per-test table. Failure detail blocks show the assertion message,
detail dict, and cross-references into the timeline. Bottom section: the
full timeline (host events in blue, OTA events in amber). Open in any
browser.

**`report.junit.xml`** — drop into your CI's JUnit consumer (GitHub Actions'
`mikepenz/action-junit-report`, GitLab artefact reports, Jenkins, etc.).

**`report.md`** — same content as the HTML, paste-friendly for PRs and chat.

**`capture.pcapng`** — open in Wireshark for deep inspection. Pair it with
the HTML report's timeline IDs to find the exact packet behind a failure.

---

## Adding a custom test procedure

Procedures are async functions decorated with `@testcase("name")`:

```python
# src/bert/profiles/my_profile/tests.py
from bert.runner import AssertionFailure, TestContext, testcase

def register() -> None:
    """Called by the entry-point loader; importing this module is enough
    because @testcase is applied at import time, but having an empty
    register() lets us future-proof for setup-heavy profiles."""

@testcase("my_custom_check")
async def my_custom_check(ctx: TestContext) -> None:
    value = await ctx.bumble.read("0x180D", "0x2A38")
    if value is None:
        raise AssertionFailure("Body Sensor Location read returned no value")
    if not (0 <= value[0] <= 6):
        raise AssertionFailure(
            f"Body Sensor Location {value[0]} out of [0,6]",
            detail={"value": value.hex()},
        )
```

Register it via `pyproject.toml`:

```toml
[project.entry-points."bert.procedures"]
my_profile = "bert.profiles.my_profile.tests:register"
```

Reference it from your YAML:

```yaml
test_cases:
  - id: TC_MYP_010
    title: Body Sensor Location reads as a valid enum
    procedure: my_custom_check
    source: host
```

Use the [generic procedures](src/bert/runner/procedures/__init__.py) (e.g.
`discover_mandatory_services`, `read_characteristic`, `subscribe_and_count`)
when they fit — most profiles get a long way before needing a custom one.

---

## Project layout

```
src/bert/
├── cli/             typer app: main, ir, run, dongles, doctor, pcap, profiles
├── ir/              pydantic v2 schema, YAML loader, semantic validator
├── parser/          fetch (httpx, sha256-cached) → html (bs4+lxml) | pdf (pymupdf)
│                    → confidence-scored extractors → IR draft
├── review/          Rich-prompt review walker; semantic IR diff
├── runner/          core orchestrator, unified timeline, registry of @testcase
│                    procedures, TestContext, assertion library
├── adapters/        bumble_host (Device wrapper), hci_transport (dongle discovery),
│                    sniffer (extcap subprocess), pcap (scapy BTLE reader)
├── profiles/        SHIPPED reviewed IR + profile-specific tests.py
│                    heart_rate/ , battery_service/ , device_information/
├── reporter/        junit, html (jinja2 timeline waterfall), markdown
├── firmware/        prebuilt .hex blobs (shipped via package-data)
└── vendor/nrf_sniffer/   pinned copy of Nordic extcap plugin

tests/
├── unit/            IR round-trip, parser, PCAP fixtures, assertions
├── integration/     Bumble virtual controller: compliant + noncompliant fixtures
└── hwil/            opt-in (`pytest -m hwil`) real-hardware smoke
```

The architecture rule of thumb: **anything that imports `bumble.*` or
`scapy.*` lives in `src/bert/adapters/`**. The rest of the codebase only
sees Python primitives + our IR / Timeline types.

---

## Development

```bash
git clone <repo> && cd bert
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# unit tests (fast, no hardware)
pytest tests/unit -q

# integration (Bumble virtual controller; no hardware)
pytest tests/integration -q

# hardware-in-the-loop (requires both dongles + a known-good DUT)
BERT_HWIL_DUT_NAME=Polar\ H10 pytest -m hwil -q
```

Lint + typecheck:

```bash
ruff check src tests
mypy src
```

---

## Troubleshooting

### `bert: command not found`

You activated the venv in one shell and ran `bert` from another, or your
shell didn't pick up the venv's `bin/`. Two reliable workarounds:

```bash
# Use the venv binary directly (always works):
.venv/bin/bert <args>

# Or activate + invoke in the same shell line:
source .venv/bin/activate && bert <args>
```

### `bert doctor` reports "no Bert-flashed Nordic dongles detected"

Either you haven't run `bert flash-firmware` yet, or the dongles are
attached but their factory USB serial numbers aren't yet registered in
`~/.config/bert/dongles.json`. Run `bert flash-firmware` to flash + register
in one step. If the dongle is already running the right firmware but
unregistered, you can add a row to the JSON file by hand:

```json
{
  "version": 1,
  "dongles": [
    { "role": "hci", "serial_number": "F4CE36AAA" },
    { "role": "sniffer", "serial_number": "F4CE36BBB" }
  ]
}
```

(`bert dongles list` shows the serial numbers of attached Nordic devices,
and `BERT_CONFIG_DIR` overrides the registry location for testing.)

### `Multiple HCI dongles found` / `Multiple sniffer dongles found`

You have two of the same role plugged in. Disconnect one, or pass
`--hci-transport serial:/dev/cu.usbmodemXXXX,1000000` explicitly.

### "advertisement not received within 10s"

Either the DUT isn't advertising, or the address/name filter doesn't match.
Try `bert run ... --verbose` for DEBUG logging. Use `nrfconnect` or
`bluetoothctl` to confirm the DUT is alive.

### macOS grabs the HCI dongle

You flashed the wrong firmware. The HCI dongle **must** run Zephyr's
`hci_uart` sample (USB-CDC), not `hci_usb` (USB BT class 0xE0). Re-flash
with `bert flash-firmware --dongle hci`.

### Windows: dongle in DFU mode doesn't appear as a COM port

You may need the inbox `usbser.sys` driver (Win10+ has it; older Windows
needs Nordic's INF). After plugging in the dongle and pressing RESET:

1. Open **Device Manager** → it should appear under **Ports (COM & LPT)**
   as e.g. `Open DFU Bootloader (COMn)`.
2. If it's stuck under **Other devices** with a yellow warning, install the
   driver from Nordic's nRF5 SDK or use [Zadig](https://zadig.akeo.ie/) to
   bind the inbox CDC driver.

### Linux: "permission denied" on `/dev/ttyACM<n>`

Add yourself to the `dialout` group (or `uucp` on Arch):

```bash
sudo usermod -aG dialout "$USER"
# log out and back in for the group change to take effect
```

### Sniffer capture is empty

Common causes: the sniffer is on the wrong RF channel, or the DUT is
already connected before the sniffer starts and so it misses the connection
request. Run with `--no-sniffer` to confirm the host-side tests pass on
their own; if they do, file a bug with the run dir and we can iterate on
sniffer triggering.

### Pairing failures

v0.1 supports JustWorks and Passkey-display. LE Secure Connections numeric
comparison and OOB are tracked for v0.2 — they currently appear as
`skipped: pairing method not supported in v1` rather than a silent fail.

---

## Architecture deep-dive

For the design rationale (why Bumble, why the IR, why the parse-then-review
pipeline, what the cross-source timeline buys us), see the original plan at
[`/Users/isa56k/.claude/plans/i-want-to-create-soft-cerf.md`](.) — it's the
load-bearing document for v0.x and explains why each module exists.

Key invariants:

- **Adapters are the only modules that import vendor libraries.** The
  runner core, IR, parser, and reporters only know about Python primitives,
  pydantic models, and the `Timeline`. Swap a vendor and only `adapters/`
  changes.
- **The IR refuses to run with `needs_review` flags.** This is the human
  gate; without it, automated parsing of SIG docs would produce silently
  wrong tests.
- **Timestamps used for timing assertions come from the sniffer, not the
  host.** Host timestamps drift under macOS GC / Linux scheduling pressure;
  the sniffer's hardware-stamped OTA timeline is the truth.
- **Profile YAML is the source of truth.** Test cases reference procedures
  by name; procedures never reference YAML. Changing a profile never
  requires a code change unless you need a new procedure.

---

## License

Apache 2.0. See [LICENSE](LICENSE).
