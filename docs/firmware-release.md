# Firmware release workflow (maintainer)

End-users never need to read this. It documents how a Bert maintainer
publishes new prebuilt firmware so that `bert flash-firmware` can download
it on demand.

## What gets shipped

| role | source | shipped as | lives in GitHub Release |
|---|---|---|---|
| `hci` | Zephyr `samples/bluetooth/hci_uart` for board `nrf52840dongle/nrf52840` | `hci_uart_nrf52840dongle.hex` | yes |
| `sniffer` | Nordic's nRF Sniffer for Bluetooth LE | `sniffer_nrf52840dongle_nrf52840_<v>.zip` | **no** — Bert auto-discovers Nordic's bundled copy under `~/.nrfutil/share/nrfutil-ble-sniffer/firmware/` |

We don't redistribute Nordic's sniffer firmware; users install it once via
`nrfutil install ble-sniffer` and Bert finds it where Nordic puts it.

## Cutting a firmware release

1. **Trigger the build.** Either push a tag matching `firmware-*`, or use
   the **Actions → build-firmware → Run workflow** UI:

   ```bash
   git tag firmware-2026.05.06
   git push origin firmware-2026.05.06
   ```

   The `.github/workflows/build-firmware.yml` workflow:

   - Runs inside `ghcr.io/zephyrproject-rtos/ci`.
   - Initialises a fresh Zephyr workspace at the revision in the tag's
     workflow input (default `v3.7.0`).
   - Builds `hci_uart` for `nrf52840dongle/nrf52840`.
   - Uploads `hci_uart_nrf52840dongle.hex` as a release asset.
   - Echoes the SHA256 + size into the run summary and release notes.

2. **Update the Bert manifest** to point at the new release. From a Bert
   checkout:

   ```bash
   scripts/update_manifest.py \
       --owner <gh-owner> --repo bert \
       --tag firmware-2026.05.06
   ```

   This fetches the asset, hashes it, and rewrites
   `src/bert/firmware/manifest.json` with the new tag, URL, SHA256, and
   size.

3. **Commit the manifest change** and cut a new Bert release in the usual
   way (bump version in `pyproject.toml`, tag, push, publish to PyPI). Once
   end-users `pip install -U bert-ble-tester`, the next `bert flash-firmware`
   will pick up the new firmware on first run and cache it locally.

## Manual build (no GHA)

If you need to produce a hex outside CI:

```bash
docker run --rm -it -v "$PWD/out:/out" ghcr.io/zephyrproject-rtos/ci:v0.27.4 bash -c '
  mkdir -p /workspace && cd /workspace &&
  west init -m https://github.com/zephyrproject-rtos/zephyr --mr v3.7.0 . &&
  west update --narrow -o=--depth=1 &&
  west zephyr-export &&
  pip install -r zephyr/scripts/requirements.txt &&
  west build -b nrf52840dongle/nrf52840 \
    -d build-hci-dongle zephyr/samples/bluetooth/hci_uart &&
  cp build-hci-dongle/zephyr/zephyr.hex /out/hci_uart_nrf52840dongle.hex
'
sha256sum out/hci_uart_nrf52840dongle.hex
```

Upload the hex to a GitHub Release manually (or via `gh release upload`),
then run `scripts/update_manifest.py` as in step 2 above.

## Cache + verification UX

The runtime side (`bert.adapters.firmware_fetch`) verifies SHA256 against
the manifest before flashing. A mismatch implies one of:

- the manifest is stale relative to the release (impossible if you used the
  script above — fix by re-running it);
- the GitHub asset was tampered (rotate the release);
- the user's cache is corrupt (`bert firmware clear-cache` clears it).

End-users see clear diagnostics from `bert firmware verify` and
`bert firmware download --force`.

## Why pin SHA256 in the manifest

We could read SHA256 from a sibling `.sha256` file at flash time, but that
trusts the same network connection that delivers the binary. Pinning the
hash inside the Bert package makes the tool's idea of "the right firmware"
travel with the Bert version: a known-good Bert build can never be tricked
into flashing a substituted hex, even if the GitHub Release is compromised
later.
