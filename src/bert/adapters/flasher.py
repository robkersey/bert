"""Drive ``nrfutil`` to flash firmware onto an nRF52840 USB Dongle (PCA10059).

The dongle has no SWD debugger; flashing happens via Nordic's Open
Bootloader (USB DFU). The bootloader presents itself as a CDC-ACM device
with VID ``0x1915`` PID ``0x521F`` and listens on the same virtual COM port
that ``nrfutil dfu usb-serial`` will write to.

We use the legacy Python ``nrfutil`` workflow because it's the only path
that supports the dongle's Open Bootloader (the new Rust-based
``nrfutil device`` only handles J-Link-equipped boards):

  1. ``nrfutil pkg generate --hw-version 52 --sd-req 0 \\
         --application <hex> --application-version 1 <pkg.zip>``
  2. ``nrfutil dfu usb-serial -pkg <pkg.zip> -p <port>``

The flow per dongle is:

  * Prompt the user to plug in / press RESET.
  * Poll pyserial until the bootloader enumerates.
  * Run the two ``nrfutil`` commands.
  * Wait for the application firmware to re-enumerate.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt
from serial.tools import list_ports

from bert.adapters import dongle_registry, firmware_fetch
from bert.adapters.hci_transport import NORDIC_VID, RECOGNISED_VIDS

log = logging.getLogger(__name__)
console = Console()

# nRF52840 USB Dongle Open Bootloader (factory MBR + bootloader; entered by RESET button).
DFU_BOOTLOADER_PID = 0x521F

# Bundled firmware paths (relative to the package's ``firmware/`` dir).
DEFAULT_FIRMWARE = {
    "hci": "hci_uart_nrf52840dongle.hex",
    "sniffer": "sniffer_nrf52840dongle.hex",
}


class FlashError(RuntimeError):
    """Anything that goes wrong during the flashing dance."""


@dataclass
class DfuPort:
    device: str  # /dev/cu.usbmodemXXXX or COMn
    serial_number: str
    description: str


# --------------------------------------------------------------------------- #
# Tooling checks                                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DfuTool:
    """A flasher tool: path + which CLI dialect it speaks.

    The two nrfutil flavours have *different* subcommand structures:

    * Legacy ``pc-nrfutil``:
        - ``nrfutil pkg generate --hw-version 52 --sd-req 0 \\
              --application <hex> --application-version 1 <zip>``
        - ``nrfutil dfu usb-serial -pkg <zip> -p <port>``

    * ``adafruit-nrfutil`` (a fork that pre-dates the pkg/dfu split):
        - ``adafruit-nrfutil dfu genpkg --application <hex> \\
              --application-version 1 --sd-req 0 <zip>``
        - ``adafruit-nrfutil dfu serial -pkg <zip> -p <port>``
    """

    path: str
    dialect: str  # "adafruit" | "legacy-python"

    def dfu_serial_args(self, package: str, port: str) -> list[str]:
        if self.dialect == "adafruit":
            return [self.path, "dfu", "serial", "-pkg", package, "-p", port]
        return [self.path, "dfu", "usb-serial", "-pkg", package, "-p", port]

    def pkg_generate_args(self, hex_path: str, out_zip: str) -> list[str]:
        if self.dialect == "adafruit":
            return [
                self.path, "dfu", "genpkg",
                "--application", hex_path,
                "--application-version", "1",
                "--sd-req", "0",
                out_zip,
            ]
        return [
            self.path, "pkg", "generate",
            "--hw-version", "52",
            "--sd-req", "0",
            "--application", hex_path,
            "--application-version", "1",
            out_zip,
        ]


def _probe_nrfutil(path: str) -> DfuTool | None:
    """Identify which nrfutil dialect this binary is, or None if unsupported."""

    probe = subprocess.run(
        [path, "--help"], capture_output=True, text=True, timeout=10
    )
    text = (probe.stdout or "") + (probe.stderr or "")
    if "adafruit" in text.lower():
        return DfuTool(path=path, dialect="adafruit")
    # Legacy pc-nrfutil has a top-level `pkg` group; the Rust-based nrfutil
    # has `device`/`ble-sniffer` instead. Probe for the differentiator.
    sub_probe = subprocess.run(
        [path, "pkg", "--help"], capture_output=True, text=True, timeout=10
    )
    if sub_probe.returncode == 0 and "generate" in (sub_probe.stdout + sub_probe.stderr):
        return DfuTool(path=path, dialect="legacy-python")
    # Maybe an adafruit fork that the --help text didn't reveal — check for
    # `dfu genpkg`.
    sub_probe2 = subprocess.run(
        [path, "dfu", "--help"], capture_output=True, text=True, timeout=10
    )
    if sub_probe2.returncode == 0 and "genpkg" in (sub_probe2.stdout + sub_probe2.stderr):
        return DfuTool(path=path, dialect="adafruit")
    return None


def ensure_nrfutil(*, allow_download: bool = True) -> DfuTool:
    """Return a usable DFU tool, preferring (in order):

    1. The cached Bert-shipped binary at
       ``$BERT_TOOLS_CACHE/bert-dfu/<sha>/bert-dfu-<platform>``. This is a
       PyInstaller-bundled ``adafruit-nrfutil`` published per-platform on
       Bert's GitHub Releases — no Python deps to conflict with.
    2. ``adafruit-nrfutil`` on ``$PATH``.
    3. ``nrfutil`` on ``$PATH``, if it's the legacy Python variant
       (rejected if it's the new Rust variant, which lacks USB DFU support).

    If nothing is found and ``allow_download`` is True, attempts to download
    the Bert-shipped binary from GitHub Releases. A clear error explains the
    options if download is disabled or the platform isn't supported.
    """
    from bert.adapters import firmware_fetch

    candidates: list[str] = []

    # 1. Cached binary, if Bert's shipped one exists for this host.
    if allow_download:
        tools = firmware_fetch.load_tool_manifest()
        spec = tools.get("bert-dfu")
        if spec is not None:
            if firmware_fetch.is_tool_cached(spec):
                cached = firmware_fetch.cached_tool_path(spec)
                candidates.append(str(cached))
            elif not spec.is_placeholder:
                try:
                    candidates.append(str(firmware_fetch.fetch_tool(spec)))
                except firmware_fetch.FirmwareFetchError as exc:
                    log.warning("could not fetch bert-dfu: %s", exc)

    # 2. PATH-installed adafruit-nrfutil (no click conflicts with typer).
    if (p := shutil.which("adafruit-nrfutil")) is not None:
        candidates.append(p)

    # 3. PATH-installed legacy nrfutil — last resort because installing this
    #    in the same venv as Bert downgrades click and breaks typer.
    if (p := shutil.which("nrfutil")) is not None:
        candidates.append(p)

    for cand in candidates:
        tool = _probe_nrfutil(cand)
        if tool is not None:
            log.info("using DFU tool: %s (%s)", tool.path, tool.dialect)
            return tool

    raise FlashError(
        "no usable DFU tool found.\n"
        "  Bert needs a tool that speaks the nRF52840 dongle's Open Bootloader.\n"
        "  Options (in order of preference):\n"
        "    1. Run `bert tools download` to grab the Bert-shipped binary\n"
        "       (single static executable, no Python deps).\n"
        "    2. `pipx install adafruit-nrfutil` (isolated venv, won't pollute Bert's).\n"
        "    3. `pip install adafruit-nrfutil` (only safe in a venv where Bert is NOT installed).\n"
        "  AVOID `pip install nrfutil` — pc-nrfutil pins click<8 which breaks typer."
    )


def nrfutil_home() -> Path:
    """The Rust-based nrfutil's home dir.

    Per Nordic's docs, ``$NRFUTIL_HOME`` defaults to ``~/.nrfutil`` on
    macOS/Linux and ``%USERPROFILE%/.nrfutil`` on Windows. The ble-sniffer
    subcommand stashes its firmware under ``share/nrfutil-ble-sniffer/firmware``.
    """
    env = os.environ.get("NRFUTIL_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".nrfutil"


def _scan_nordic_sniffer_firmware() -> Path | None:
    """Return the most recent Nordic-installed dongle sniffer firmware, if any.

    The file is shipped as a pre-packaged DFU .zip ready to flash via
    ``nrfutil dfu usb-serial -pkg``. We pick the highest version number we find.
    """
    base = nrfutil_home() / "share" / "nrfutil-ble-sniffer" / "firmware"
    if not base.is_dir():
        return None
    candidates = sorted(
        base.glob("sniffer_nrf52840dongle_nrf52840_*.zip"),
        key=lambda p: p.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def resolve_firmware(
    role: str,
    override: Path | None = None,
    *,
    allow_download: bool = True,
) -> Path:
    """Locate the firmware image for ``role``.

    Search order:

      1. ``override`` (CLI ``--firmware-<role>``).
      2. Files bundled inside the ``bert`` wheel under ``src/bert/firmware/``
         (rare — only used if the maintainer chose to ship a payload directly).
      3. **(sniffer only)** Nordic's nrfutil-ble-sniffer install at
         ``$NRFUTIL_HOME/share/nrfutil-ble-sniffer/firmware/`` (cross-platform;
         honours ``NRFUTIL_HOME`` env, defaults to ``~/.nrfutil``).
      4. **GitHub Releases** via :mod:`bert.adapters.firmware_fetch`. The
         manifest at ``src/bert/firmware/manifest.json`` pins the URL +
         SHA256; the file is downloaded once and cached under
         ``$BERT_FIRMWARE_CACHE`` / ``~/.cache/bert/firmware/``. Set
         ``allow_download=False`` to forbid this step (offline mode).

    The returned path may be a ``.hex`` (raw image — caller wraps it with
    ``nrfutil pkg generate``) or a ``.zip`` (already a DFU bundle, ready for
    ``nrfutil dfu usb-serial``).
    """

    if override is not None:
        if not override.exists():
            raise FlashError(f"firmware override not found: {override}")
        return override

    bundled = (
        Path(__file__).resolve().parent.parent / "firmware" / DEFAULT_FIRMWARE[role]
    )
    if bundled.exists():
        return bundled

    if role == "sniffer":
        nordic = _scan_nordic_sniffer_firmware()
        if nordic is not None:
            log.info("using Nordic-installed sniffer firmware: %s", nordic)
            return nordic

    if allow_download:
        manifest = firmware_fetch.load_manifest()
        spec = manifest.get(role)
        if spec is not None and not spec.is_placeholder:
            try:
                return firmware_fetch.fetch(spec)
            except firmware_fetch.FirmwareFetchError as exc:
                raise FlashError(str(exc)) from exc

    hint_lines = [
        f"no firmware found for role {role!r}.",
        f"  Pass --firmware-{role} <path> to point at a hex/zip explicitly.",
        "",
    ]
    if role == "sniffer":
        hint_lines.extend(
            [
                "Or install Nordic's ble-sniffer subcommand (it ships the dongle firmware):",
                "    nrfutil install ble-sniffer",
                f"  → firmware will appear under {nrfutil_home()}/share/nrfutil-ble-sniffer/firmware/",
            ]
        )
    else:  # hci
        hint_lines.extend(
            [
                "Either:",
                "  1. Wait for an upcoming Bert release that publishes the prebuilt hex,",
                "  2. Or build it yourself from Zephyr:",
                "       west init -m https://github.com/zephyrproject-rtos/zephyr --mr v3.7.0 zephyrproject",
                "       cd zephyrproject && west update",
                "       west build -b nrf52840dongle/nrf52840 zephyr/samples/bluetooth/hci_uart",
                "       bert flash-firmware --firmware-hci build/zephyr/zephyr.hex",
            ]
        )
    raise FlashError("\n".join(hint_lines))


# --------------------------------------------------------------------------- #
# Bootloader detection                                                         #
# --------------------------------------------------------------------------- #


def list_dfu_ports() -> list[DfuPort]:
    """Return every nRF52840 dongle currently in DFU/Open Bootloader mode."""

    out: list[DfuPort] = []
    for p in list_ports.comports():
        if getattr(p, "vid", None) != NORDIC_VID:
            continue
        if getattr(p, "pid", None) != DFU_BOOTLOADER_PID:
            continue
        out.append(
            DfuPort(
                device=p.device,
                serial_number=(getattr(p, "serial_number", "") or "").strip(),
                description=p.description or "",
            )
        )
    return out


async def wait_for_dfu(
    *, baseline: set[str], timeout_s: float = 30.0, poll_interval_s: float = 0.5
) -> DfuPort:
    """Locate a dongle in DFU mode, prefering ones that appeared since ``baseline``.

    Strategy:

      1. **Use a newly-appeared device first.** If a DFU port shows up that
         wasn't present when ``baseline`` was snapshotted, return it
         immediately (handles "press Enter, then press RESET").
      2. **Otherwise, accept whatever DFU device is currently present.** This
         covers the natural workflow where the user plugs in + presses RESET
         *before* invoking ``bert flash-firmware`` (or before reaching the
         prompt for the next role in a multi-dongle flash).
      3. If multiple DFU devices are present and none is new vs the baseline,
         the choice is ambiguous → error and ask the user to disconnect
         spares.
      4. If nothing appears in ``timeout_s``, time out with a hint.
    """

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        present = list_dfu_ports()
        new_ports = [p for p in present if p.device not in baseline]
        if new_ports:
            return new_ports[0]
        if present:
            if len(present) > 1:
                devs = ", ".join(p.device for p in present)
                raise FlashError(
                    f"multiple dongles in DFU mode ({devs}); disconnect all but "
                    f"the one you want to flash, then try again."
                )
            log.info("using already-present DFU dongle on %s", present[0].device)
            return present[0]
        await asyncio.sleep(poll_interval_s)
    raise FlashError(
        f"timed out after {timeout_s:.0f}s waiting for a dongle in DFU mode.\n"
        f"Press the small RESET button on the side of the dongle (nearest the SoC) "
        f"to enter Open Bootloader. If the bootloader's red LED is already pulsing, "
        f"check that it appears as a USB-CDC device:\n"
        f"    .venv/bin/python -c \"from serial.tools.list_ports import comports; "
        f"[print(p.device, hex(p.vid or 0), hex(p.pid or 0), p.description) for p in comports()]\""
    )


# Backwards-compatible alias so existing tests / callers keep working.
async def wait_for_new_dfu(*, baseline: set[str], timeout_s: float = 30.0) -> DfuPort:
    return await wait_for_dfu(baseline=baseline, timeout_s=timeout_s)


async def wait_for_app_enumeration(
    *,
    dfu_baseline: set[str],
    pre_flash_app_devices: set[str] | None = None,
    timeout_s: float = 30.0,
) -> tuple[str, str] | None:
    """After flashing, wait for the dongle to come back up as the application device.

    We can't predict the application's VID/PID exactly (depends on the firmware
    image we wrote — Zephyr samples use Zephyr's VID 0x2FE3, Nordic samples
    use 0x1915). Heuristic: any Bert-recognised-VID device that ISN'T in DFU
    mode and wasn't already attached pre-flash is the one. Returns
    ``(device_path, serial_number)`` or ``None`` on timeout.
    """

    pre_app = pre_flash_app_devices or set()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for p in list_ports.comports():
            vid = getattr(p, "vid", None)
            if vid not in RECOGNISED_VIDS:
                continue
            if getattr(p, "pid", None) == DFU_BOOTLOADER_PID:
                continue
            if p.device in dfu_baseline or p.device in pre_app:
                continue
            sn = (getattr(p, "serial_number", "") or "").strip()
            return p.device, sn
        await asyncio.sleep(0.5)
    return None


# --------------------------------------------------------------------------- #
# Flashing                                                                     #
# --------------------------------------------------------------------------- #


async def flash_one(role: str, firmware_path: Path, *, tool: DfuTool) -> str | None:
    """Flash a single role. Prompts the user; runs nrfutil; returns the new
    application device path (or ``None`` if it didn't re-enumerate in time).

    ``firmware_path`` may be a ``.hex`` (raw image; we wrap it with
    ``nrfutil pkg generate``) or a ``.zip`` (already a DFU bundle, e.g.
    Nordic's ``sniffer_nrf52840dongle_nrf52840_*.zip``; we flash it directly).

    ``tool`` is a :class:`DfuTool` describing which nrfutil flavour is in use;
    obtain one from :func:`ensure_nrfutil`.
    """

    console.rule(f"Flashing [bold]{role}[/bold] dongle")
    console.print(f"firmware: {firmware_path}")

    baseline_dfu = {p.device for p in list_dfu_ports()}
    Prompt.ask(
        f"  Plug in the dongle you want to use as the [bold]{role}[/bold] role, then press its "
        f"small [italic]RESET[/italic] button (the side button, NOT the white one).\n"
        f"  When the bootloader's red LED pulses, press Enter to continue",
        default="",
        show_default=False,
    )

    pre_flash_app_devices = {
        p.device for p in list_ports.comports()
        if getattr(p, "vid", None) in RECOGNISED_VIDS
        and getattr(p, "pid", None) != DFU_BOOTLOADER_PID
    }

    console.print("[dim]looking for DFU bootloader…[/dim]")
    port = await wait_for_dfu(baseline=baseline_dfu, timeout_s=30)
    console.print(f"[green]✓[/green] DFU device on {port.device} (sn={port.serial_number})")

    suffix = firmware_path.suffix.lower()
    with tempfile.TemporaryDirectory() as td:
        if suffix == ".zip":
            # Already a DFU bundle (e.g. Nordic-shipped sniffer dongle .zip).
            pkg = firmware_path
            console.print("[dim]firmware is a DFU package; skipping pkg generate[/dim]")
        elif suffix == ".hex":
            pkg = Path(td) / "package.zip"
            try:
                subprocess.run(
                    tool.pkg_generate_args(str(firmware_path), str(pkg)),
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                raise FlashError(
                    f"`{tool.path} pkg generate` failed:\n{e.stderr or e.stdout}"
                ) from e
        else:
            raise FlashError(
                f"unsupported firmware file type {suffix!r}: expected .hex or .zip "
                f"(got {firmware_path})"
            )

        console.print("[dim]flashing… (takes 10-30 seconds)[/dim]")
        try:
            proc = subprocess.run(
                tool.dfu_serial_args(str(pkg), port.device),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise FlashError(
                f"`{tool.path} dfu` failed:\n{e.stderr or e.stdout}"
            ) from e
        log.debug("dfu output:\n%s", proc.stdout)

    # Wait for the dongle to re-enumerate as the application device. The new
    # firmware presents under a different USB descriptor — Zephyr samples
    # change VID and reformat the iSerialNumber from the same factory data.
    # We register *that* serial number in the registry so discovery works
    # against the running firmware (the bootloader's SN is irrelevant once
    # the app is alive).
    enum = await wait_for_app_enumeration(
        dfu_baseline={port.device},
        pre_flash_app_devices=pre_flash_app_devices,
        timeout_s=30,
    )
    if enum is not None:
        new_dev, app_sn = enum
        console.print(f"[green]✓[/green] {role} dongle re-enumerated as {new_dev}")
        register_sn = app_sn or port.serial_number
    else:
        console.print(
            f"[yellow]![/yellow] {role} dongle did not re-enumerate within 30s. "
            f"It probably finished but is slow; run `bert dongles list-all` to "
            f"check. Falling back to the bootloader SN for registration; if "
            f"discovery later fails, re-run `bert flash-firmware`."
        )
        new_dev = None
        register_sn = port.serial_number

    if register_sn:
        dongle_registry.upsert(role, register_sn)
        console.print(
            f"[green]✓[/green] registered {role} dongle (sn={register_sn}) "
            f"in {dongle_registry.registry_path()}"
        )
    else:
        console.print(
            "[yellow]![/yellow] no USB serial number captured; the dongle "
            "won't auto-discover. Add it manually to "
            f"{dongle_registry.registry_path()} or rerun `bert flash-firmware`."
        )
    return new_dev


async def flash_all(
    *,
    roles: list[str],
    firmware_hci: Path | None = None,
    firmware_sniffer: Path | None = None,
) -> None:
    """High-level: flash the listed roles, prompting for each."""

    tool = ensure_nrfutil()
    overrides = {"hci": firmware_hci, "sniffer": firmware_sniffer}
    paths = {role: resolve_firmware(role, overrides.get(role)) for role in roles}

    for role in roles:
        await flash_one(role, paths[role], tool=tool)
        if role != roles[-1]:
            console.print()
            with suppress(KeyboardInterrupt):
                Prompt.ask(
                    "  Unplug this dongle (or leave it; it won't conflict) and "
                    "get the next one ready. Press Enter when ready",
                    default="",
                    show_default=False,
                )

    console.print()
    console.rule("[bold green]done[/bold green]")
    console.print("Run `bert dongles list` to confirm both dongles are recognised.")
