# Naga V3 Pro upstream branch testing, 2026-09-12

## Purpose

This document records the September 12, 2026 test session for the updated
OpenRazer Naga V3 Pro pull request and its companion Polychromatic scroll-mode
branch. It preserves the exact commits tested, the hardware observations, the
feedback sent upstream, and the known-good local restore point.

The test branches are no longer installed. The system was restored to the
known-good local OpenRazer build and stock Polychromatic after testing.

## Hardware and environment

- Mouse: Razer Naga V3 Pro over HyperSpeed, USB ID `1532:00E8`
- Mouse serial: `PM2625H38605110`
- Dock USB ID: `1532:00A4`
- OpenRazer test kernels:
  - `7.2.4-1-cachyos`, built with Clang
  - `6.18.50-1-cachyos-lts`, built with GCC
- Polychromatic package: `0.9.8-1`
- Python: 3.14

Do not connect the wired `1532:00E7` transport and wireless `1532:00E8`
transport simultaneously. They report the same serial and therefore conflict
as OpenRazer D-Bus identities.

## Known-good baseline and restore point

The baseline is the pushed `add-razer-naga-v3-pro-support` branch at:

```text
f7529942032374175ae8ee9a8f93c1f49fb44d45
```

The relevant baseline commits are:

```text
f7529942 package Naga idle idle driver mode restoration
eb637a0a document Naga idle mode restoration
09a922ce restore Naga driver mode after wireless idle
85d3d844 package wireless discovery retry
77a12fac retry unavailable devices during discovery
```

At the end of the test and restore work, before this report was added, the
repository was clean and synchronized with the fork. The following packages
were installed:

```text
openrazer-daemon-local 3.12.4.nagav3.1-7
openrazer-driver-dkms-local 3.12.4.nagav3.1-7
python-openrazer-local 3.12.4.nagav3.1-7
polychromatic 0.9.8-1
```

The three OpenRazer packages remain frozen in `/etc/pacman.conf`:

```text
IgnorePkg = openrazer-daemon-local openrazer-driver-dkms-local python-openrazer-local
```

The local restore packages are:

```text
packaging/arch-local/openrazer-daemon-local-3.12.4.nagav3.1-7-any.pkg.tar.zst
packaging/arch-local/openrazer-driver-dkms-local-3.12.4.nagav3.1-7-any.pkg.tar.zst
packaging/arch-local/python-openrazer-local-3.12.4.nagav3.1-7-any.pkg.tar.zst
```

DKMS has the baseline driver installed for both current kernels. The on-disk
and loaded `razermouse` module source versions both read:

```text
36593E92584CB2992B29AAC
```

The restored runtime state is:

- Device mode: `3:0`
- Wireless idle timeout: `300` seconds
- `openrazer-daemon.service`: active
- Dock and wireless Naga: both present through the Python client with their
  real serials
- Polychromatic backend: stock `0.9.8-1` contents

`pacman -Qkk polychromatic` reports only a modification-time mismatch for
`openrazer.py` after the test file was replaced and the package reinstalled.
The restored file's checksum matches the saved stock file; there is no content
or size mismatch.

## GitHub updates

The original support issue is:

- [openrazer/openrazer#2885](https://github.com/openrazer/openrazer/issues/2885)

The substantive development and test discussion moved to:

- [openrazer/openrazer#2904](https://github.com/openrazer/openrazer/pull/2904)
- [polychromatic/polychromatic#613](https://github.com/polychromatic/polychromatic/pull/613)

OpenRazer PR `#2904` requested hardware testing of a revised implementation.
Polychromatic PR `#613` was then created to consume the new OpenRazer
`scroll_mode_options` capability and expose precision tactile mode.

## OpenRazer PR 2904

### Exact version tested

- Repository: `edualb/openrazer`
- Branch: `add-naga-v3-pro-support`
- Commit: `bfa5299ab66d1a5bdc32c09c5e0f9a1f9ed5d370`

The branch was cloned and packaged outside the working repository. Temporary
test packages used version `3.12.4.nagav3.edualb-1`, replaced the three local
OpenRazer packages during the test, and were replaced when the baseline was
restored.

The driver compiled successfully against both installed kernels. The host did
not have the `pytest` command installed, so this session used compilation,
D-Bus and Python API checks, evdev capture, sysfs readback, physical behavior,
and daemon/kernel logs.

### Relevant branch changes

The updated branch includes:

- `RAZER_NAGA_V3_PRO_WAIT_US` set to `99900`, replacing the earlier approach
  of relying on discovery retries with a longer per-command response wait
- `SCROLL_MODE_VERSION = 2` for the Naga V3 Pro
- D-Bus `getScrollModeOptions` returning tactile, free spin, and precision
  tactile mode names
- Python client exposure of `scroll_mode_options`
- Precision tactile mode `2` accepted by `setScrollMode`
- Naga V3 Pro `DPI_MAX = 50000`
- A shared low-level DPI clamp raised from 45,000 to 50,000
- Firmware report value `0xD2` translated to `KEY_F18` for the AI Prompt
  Master assignment

The branch does not include the baseline daemon discovery retry from
`77a12fac` or the wake-triggered driver-mode restoration from `09a922ce`.

### Passing results

| Test | Result | Evidence |
| --- | --- | --- |
| Current-kernel build | Pass | Built against `7.2.4-1-cachyos` with Clang. |
| LTS-kernel build | Pass | Built against `6.18.50-1-cachyos-lts` with GCC. |
| Awake daemon discovery | Pass | Dock and Naga appeared with their real serials after normal initialization. |
| Scroll capability over D-Bus | Pass | Returned `tactile`, `free_spin`, and `precision_tactile`. |
| Scroll capability through Python | Pass | `scroll_mode_options` returned the same three values. |
| Tactile mode `0` | Pass | D-Bus readback returned `0`; normal tactile detents worked physically. |
| Free-spin mode `1` | Pass | D-Bus readback returned `1`; the wheel disengaged and spun freely. |
| Precision tactile mode `2` | Pass | D-Bus readback returned `2`; finer tactile behavior worked physically. |
| Hypershift before idle | Pass | Three presses produced clean `KEY_F17` press/release pairs. |

The `0xD2` to `KEY_F18` mapping was not verified. No known physical control or
onboard assignment produced firmware value `0xD2` during this session, so this
is untested rather than failed.

### Idle-wake failure

The revised 99 ms command timeout does not restore driver mode after wireless
idle.

Reproduction:

1. Start the daemon with the mouse awake and verify device mode `03 00`.
2. Set the wireless idle timeout to 60 seconds.
3. Leave the mouse untouched for 65 seconds.
4. Move the mouse once to wake it.
5. Press the ring-finger/Hypershift control three times.
6. Read device mode and inspect all Naga evdev interfaces.

Observed result:

- Device mode changed from `03 00` to `00 00` when the mouse slept.
- Movement woke the mouse but did not restore driver mode.
- Three Hypershift presses produced no input events.
- Device mode remained `00 00`.

This matches the original failure: in device mode, the firmware does not emit
the keyboard report that OpenRazer translates to `KEY_F17`. Increasing a
response timeout affects commands sent while the device is available; it does
not create an event or command that restores driver mode after wake.

### Daemon restart while asleep

The 99 ms timeout also does not solve discovery when the receiver is present
but the mouse is sleeping.

Reproduction:

1. Set idle to 60 seconds and allow the mouse to sleep.
2. Do not move the mouse.
3. Restart `openrazer-daemon.service`.

Observed shutdown behavior:

- `resume_device()` attempted to set driver mode.
- The write raised `TimeoutError: [Errno 110] Connection timed out`.
- Daemon shutdown did not finish before the 10-second systemd timeout.
- systemd sent `SIGKILL` and marked the service result as a timeout.

Observed startup behavior:

- The daemon found the Naga HID path.
- Five serial reads timed out.
- It generated an invalid fallback serial after receiving an empty value.
- Initial driver-mode setup timed out.
- The daemon exited and its D-Bus service became unavailable.
- The battery notifier also encountered a timed-out read while startup was
  failing.

The kernel logged timed-out commands for device mode and battery requests.
Moving the mouse and restarting the daemon recovered the real serial and D-Bus
service immediately.

### OpenRazer conclusions

The longer Naga command timeout works for normal commands while the mouse is
awake, but it is not sufficient for either wireless failure mode:

- Device construction must be retried when the receiver is present while the
  mouse is asleep.
- Driver mode must be reapplied after the first wake report when userspace has
  requested mode `3:0`.

The baseline implementation has performed both jobs reliably in daily use for
several days. Its wake restoration does not poll and does not wake the sleeping
mouse. It waits for actual mouse input after a long gap, briefly delays the
fire-and-forget mode command while the receiver recovers, and respects an
explicit userspace request for mode `0:0`.

The PR's class-level `DPI_MAX = 50000` correctly advertises the Naga's limit.
However, changing the shared low-level protocol clamp to 50,000 changes the
accepted range for every mouse. The baseline keeps the 50,000 allowance scoped
to this device and preserves the existing 45,000 behavior for other mice.

### Feedback posted

The complete test result was posted at:

- [OpenRazer PR 2904 test comment](https://github.com/openrazer/openrazer/pull/2904#issuecomment-5645847015)

## Polychromatic PR 613

### Exact version tested

- Repository: `polychromatic/polychromatic`
- Branch: `scroll_mode_options`
- Commit: `ee4fbed9b29d109c5296cec7609c9ad5993978ed`

PR `#613` changes only `polychromatic/backends/openrazer.py`. Following the
maintainer's test instructions, that exact file temporarily replaced
`/usr/lib/python3.14/site-packages/polychromatic/backends/openrazer.py` from
Polychromatic `0.9.8-1`. It was tested with OpenRazer PR `#2904` active so the
new D-Bus and Python capability existed.

### Passing result

The capability integration itself works:

- OpenRazer returned `tactile`, `free_spin`, and `precision_tactile`.
- Polychromatic displayed Precision Tactile.
- Selecting Precision Tactile applied numeric mode `2`.
- The wheel physically entered precision tactile mode.

### Duplicate scroll-mode entries

The Scroll Mode selector displayed six choices:

```text
Tactile
Free Spin
Precision Tactile
Tactile
Free Spin
Precision Tactile
```

Direct backend inspection confirmed the same duplicate parameter list:

```text
[(0, 'Tactile'), (1, 'Free Spin'), (2, 'Precision Tactile'),
 (0, 'Tactile'), (1, 'Free Spin'), (2, 'Precision Tactile')]
```

At the tested commit, `_get_scroll_options()` initializes
`scroll_mode.parameters` with all three parameter objects and then appends
those same objects again according to `scroll_mode_options`. Initializing the
list as empty before the conditional appends removes the duplicates.

The current code also affects devices without the new capability. The fallback
sets `modes` to tactile and free spin, but the initial parameter list already
contains all three modes. Such devices would therefore display an unsupported
Precision Tactile entry plus duplicate Tactile and Free Spin entries.

### Feedback posted

The complete test result was posted at:

- [Polychromatic PR 613 test comment](https://github.com/polychromatic/polychromatic/pull/613#issuecomment-5645847019)

## Restore procedure

If a future branch test replaces the baseline packages, restore all three as a
single Pacman transaction:

```bash
sudo pacman -U --noconfirm \
  packaging/arch-local/openrazer-daemon-local-3.12.4.nagav3.1-7-any.pkg.tar.zst \
  packaging/arch-local/openrazer-driver-dkms-local-3.12.4.nagav3.1-7-any.pkg.tar.zst \
  packaging/arch-local/python-openrazer-local-3.12.4.nagav3.1-7-any.pkg.tar.zst
```

Stop applications using the driver, reload the packaged module, retrigger udev
for the current primary `1532:00E8` HID path if a live reload leaves its sysfs
files owned incorrectly, and restart the daemon. HID instance suffixes such as
`.0009` are not stable across reboots or reconnects and must not be hard-coded.

Verify the restoration with:

```bash
pacman -Q openrazer-daemon-local openrazer-driver-dkms-local python-openrazer-local
dkms status openrazer-driver/3.12.4.nagav3.1
modinfo -F srcversion razermouse
tr -d '\n' < /sys/module/razermouse/srcversion
systemctl --user is-active openrazer-daemon.service
```

The on-disk and loaded source versions must both equal
`36593E92584CB2992B29AAC`. Finally, verify mode `3:0`, idle `300`, the real
wireless serial, and `KEY_F17` after an actual idle/wake cycle.

Temporary test checkouts and packages were kept under `/tmp/opencode` during
this session. They are disposable and must not be treated as restore artifacts.
