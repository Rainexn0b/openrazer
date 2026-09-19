# Naga V3 Pro upstream branch testing, 2026-09-12

## Purpose

This document records the September 12, 2026 test session for the updated
OpenRazer Naga V3 Pro pull request and its companion Polychromatic scroll-mode
branch. It preserves the exact commits tested, the hardware observations, the
feedback sent upstream, and the known-good local restore point.

The OpenRazer test branch is no longer installed. On September 13, the
compatible scroll-mode capability was ported onto the known-good local
OpenRazer branch and the corrected Polychromatic backend was adopted as the
new baseline.

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
2416bfebf0175db6aae519a450f55fe9eba255e9
```

The relevant baseline commits are:

```text
2416bfeb package reliable scroll mode baseline
1a250c1c retry Naga driver mode restoration
3444225a package scroll mode capability
5532eca5 expose supported scroll modes
b6ed426c document upstream Naga branch testing
f7529942 package Naga idle idle driver mode restoration
eb637a0a document Naga idle mode restoration
09a922ce restore Naga driver mode after wireless idle
85d3d844 package wireless discovery retry
77a12fac retry unavailable devices during discovery
```

After the capability integration and physical retest, the following packages
were installed:

```text
openrazer-daemon-local 3.12.4.nagav3.1-9
openrazer-driver-dkms-local 3.12.4.nagav3.1-9
python-openrazer-local 3.12.4.nagav3.1-9
polychromatic 0.9.8-1
```

The three OpenRazer packages remain frozen in `/etc/pacman.conf`:

```text
IgnorePkg = openrazer-daemon-local openrazer-driver-dkms-local python-openrazer-local
```

The local restore packages are:

```text
packaging/arch-local/openrazer-daemon-local-3.12.4.nagav3.1-9-any.pkg.tar.zst
packaging/arch-local/openrazer-driver-dkms-local-3.12.4.nagav3.1-9-any.pkg.tar.zst
packaging/arch-local/python-openrazer-local-3.12.4.nagav3.1-9-any.pkg.tar.zst
```

DKMS has the baseline driver installed for both current kernels. The on-disk
and loaded `razermouse` module source versions both read:

```text
665C13D879FF3501D395708
```

The restored runtime state is:

- Device mode: `3:0`
- Wireless idle timeout: `300` seconds
- `openrazer-daemon.service`: active
- Dock and wireless Naga: both present through the Python client with their
  real serials
- Scroll mode: tactile mode `0`
- Scroll mode options: `tactile`, `free_spin`, `precision_tactile`
- Polychromatic backend: PR `#613` commit
  `627f9850165775cb58ce513079df82509c2a4bb2`

`pacman -Qkk polychromatic` reports the expected modification-time, size, and
checksum mismatches for the intentionally overridden `openrazer.py`. Its SHA256
is `45c9fcc5122717d8787c82473cd6224bb45207f0e714e6e525e3f44a273eab24`,
matching the tested PR commit exactly. The tracked restore patch is
`packaging/polychromatic-local/polychromatic-0.9.8-scroll-mode-options.patch`.

### Integrated capability validation

The local integration adds only the finalized scroll-mode capability. It does
not include the PR branch's command timeout, discovery, driver-mode, global DPI,
or input mapping changes.

- `getScrollModeOptions` returns the ordered mode names over D-Bus.
- The Python client exposes the same values as `scroll_mode_options`.
- Existing scroll-mode devices are version `1`; the Naga V3 Pro is version `2`.
- Focused daemon and Python client tests passed, 10 tests total.
- Python byte-compilation passed.
- The driver built against `7.2.4-1-cachyos` with Clang and
  `6.18.50-1-cachyos-lts` with GCC.
- The complete daemon suite retained one unrelated legacy `effect_sync` test
  failure; all 42 other tests passed.
- DKMS installed the wake-retry fix for both kernels, and loaded/on-disk source
  versions both read `665C13D879FF3501D395708`.
- Live D-Bus and Python checks returned exactly the three Naga modes.
- Polychromatic displayed three unique choices and physically applied mode `2`.
- A sysfs-confirmed 60-second idle cycle reset mode to `0:0`; after wake, the
  bounded fire-and-forget attempts restored mode `3:0` without polling or a
  kernel warning. Separate input capture confirmed clean `KEY_F17` transitions
  while driver mode was active.

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

## OpenRazer PR 2904 updated retest, 2026-09-19

### Exact version tested

- Updated PR head: `7a6d39784cfc22c07205a8e43f5f64cf03399710`
- Previous tested PR head: `bfa5299ab66d1a5bdc32c09c5e0f9a1f9ed5d370`
- Temporary package version: `3.12.4.nagav3.edualb-2`
- Loaded test module source version: `72836A8715E98CA5722A2C3`

The update adds a daemon `MouseMonitor`, a read-only kernel
`device_last_activity` attribute, and an initialization wait that uses a valid
serial response to decide when the wireless mouse is ready. The monitor polls
the activity attribute every 200 ms, refreshes the hardware idle timeout every
30 seconds, and writes driver mode once after detecting an idle-to-active
transition.

Both driver builds passed: Clang/LLVM was used for `7.2.4-1-cachyos` and GCC
for `6.18.50-1-cachyos-lts`. Python byte compilation and focused mypy checks
passed. The daemon suite passed 42 tests and retained the unrelated legacy
`effect_sync` failure. No tests cover the new monitor or readiness behavior.

### Passing behavior

| Test | Result | Evidence |
| --- | --- | --- |
| DKMS install | Pass | The exact PR module installed for both current kernels. |
| Awake discovery | Pass | The dock and Naga appeared with their real serials. |
| Scroll capability | Pass | D-Bus, Python, and Polychromatic returned exactly tactile, free-spin, and precision tactile. |
| Scroll mode writes | Pass | Modes `0`, `1`, and `2` round-tripped; Precision Tactile worked physically. |
| Awake Hypershift | Pass | A control click and `KEY_F17` press/release pair were captured on separate evdev interfaces. |
| Runtime idle/wake | Pass for one cycle | After a confirmed 60-second sleep, movement caused the monitor to log restoration, mode read back `03 00`, and three Hypershift presses produced clean `KEY_F17` pairs. |
| Wake during startup | Pass | After movement, the blocked daemon obtained serial `PM2625H38605110`, set mode `03 00`, and completed initialization. |

The runtime test also exposed delayed idle recognition. The hardware was visibly
asleep after 70 seconds, but `device_last_activity` reported activity only
17.8 seconds old and the monitor had not marked the mouse idle. The apparent
sleep-transition report reset the driver's timestamp. The monitor declared the
mouse idle only after the timestamp later reached 139 seconds. Restoration
worked after that delayed transition.

### Restart-while-asleep failure

Restarting the daemon with the Naga confirmed asleep still failed clean
shutdown. The daemon attempted a driver-mode write, raised
`TimeoutError: [Errno 110] Connection timed out`, exceeded systemd's 10-second
stop timeout, and was killed with `SIGKILL`.

The replacement process then found the dock and Naga but blocked inside the
Naga constructor. It logged the idle state at `15:26:50` and did not initialize
the Naga or log `Serving DBus` until movement at `15:27:33`. Startup therefore
withheld the whole service, including the available dock, for 43 seconds. If
the mouse remains off or unavailable, `wait_until_ready()` has no overall idle
deadline and can block startup indefinitely.

This is worse than the local baseline's bounded asynchronous discovery retry.
The baseline serves available devices immediately, retries the unavailable
Naga separately, and stops retrying after a bounded window. The updated PR was
therefore not adopted despite its successful runtime idle/wake cycle.

### Rollback result

The three `pkgrel=9` baseline packages were restored immediately. DKMS was
verified installed for both kernels; the current kernel's loaded and on-disk
module fingerprints both returned `665C13D879FF3501D395708`. Final state was
daemon active, real dock and Naga serials, device mode `03 00`, idle timeout
`300`, scroll mode `0`, all three scroll options, and the expected Polychromatic
backend hash
`45c9fcc5122717d8787c82473cd6224bb45207f0e714e6e525e3f44a273eab24`.

## Polychromatic PR 613

### Exact versions tested

- Repository: `polychromatic/polychromatic`
- Branch: `scroll_mode_options`
- Initial commit: `ee4fbed9b29d109c5296cec7609c9ad5993978ed`
- Corrected retest commit: `627f9850165775cb58ce513079df82509c2a4bb2`

PR `#613` changes only `polychromatic/backends/openrazer.py`. Following the
maintainer's test instructions, each tested revision of that file replaced
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

### Corrected retest and adoption

The force-updated commit `627f9850` initializes the option list as empty and
then appends only the modes reported by OpenRazer. Retesting showed exactly
three unique entries. Precision Tactile applied mode `2` and produced the finer
tactile wheel behavior. This corrected file was installed with the local
`pkgrel=9` capability and wake-retry build and became the new baseline.

- [Polychromatic PR 613 corrected retest](https://github.com/polychromatic/polychromatic/pull/613#issuecomment-5652349700)

## Restore procedure

If a future branch test replaces the baseline packages, restore all three as a
single Pacman transaction:

```bash
sudo pacman -U --noconfirm \
  packaging/arch-local/openrazer-daemon-local-3.12.4.nagav3.1-9-any.pkg.tar.zst \
  packaging/arch-local/openrazer-driver-dkms-local-3.12.4.nagav3.1-9-any.pkg.tar.zst \
  packaging/arch-local/python-openrazer-local-3.12.4.nagav3.1-9-any.pkg.tar.zst
sudo pacman -S --noconfirm polychromatic
sudo patch --forward --strip=1 \
  --directory=/usr/lib/python3.14/site-packages \
  < packaging/polychromatic-local/polychromatic-0.9.8-scroll-mode-options.patch
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
`665C13D879FF3501D395708`. Finally, verify mode `3:0`, idle `300`, scroll mode
`0`, all three `scroll_mode_options`, the real wireless serial, the three unique
Polychromatic choices, and `KEY_F17` after an actual idle/wake cycle.

Temporary test checkouts and packages were kept under `/tmp/opencode` during
this session. They are disposable and must not be treated as restore artifacts.
