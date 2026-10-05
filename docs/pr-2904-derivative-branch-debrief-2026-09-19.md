# PR 2904 derivative fix branch debrief, 2026-09-19

Current canonical checkout: `/home/cyril/Projects/openrazer`, branch
`test-pr-2904-edualb`. The linked worktree and redundant local branches were
retired after preserving their work. Paths and commit identities below describe
historical test states; use the current canonical checkout for further work.

## Purpose

This document independently records the origin, failures, fixes, validation,
and remaining risks of the `test-pr-2904-edualb` branch. It does not depend on
the separate local baseline branch or its testing notes.

The branch is a derivative of the exact OpenRazer PR `#2904` revision tested on
September 19, 2026. It does not modify the contributor's repository or PR
branch. The fixes exist on the Rainexn0b fork and can be reviewed or
cherry-picked separately.

- Upstream PR: <https://github.com/openrazer/openrazer/pull/2904>
- Contributor repository: <https://github.com/edualb/openrazer>
- Contributor branch: `add-naga-v3-pro-support`
- Exact contributor commit used as the base:
  `7a6d39784cfc22c07205a8e43f5f64cf03399710`
- Fix branch: <https://github.com/Rainexn0b/openrazer/tree/test-pr-2904-edualb>
- Local canonical worktree: `/home/cyril/Projects/openrazer-pr2904-as-is`

## Branch lineage

The fixes were committed directly on top of the contributor's exact tested
head. The contributor's commits were not copied, squashed, or rebased.

```text
7b22a814 keep unavailable wireless devices discoverable
430529fa handle unavailable wireless devices without blocking
4f929a86 fix naga v3 pro activity sysfs cleanup
7a6d3978 considering the device serial be ready before initialization
```

The implementation diff from `7a6d3978` through `7b22a814` changes six files:

```text
daemon/openrazer_daemon/daemon.py
daemon/openrazer_daemon/hardware/device_base.py
daemon/openrazer_daemon/hardware/mouse.py
daemon/openrazer_daemon/misc/mouse_monitor.py
daemon/tests/test_daemon_hotplug.py
driver/razermouse_driver.c
```

## Test hardware and environment

- Mouse: Razer Naga V3 Pro over HyperSpeed
- Wireless USB ID: `1532:00E8`
- Mouse serial: verified real device serial (redacted in this public report)
- Dock USB ID: `1532:00A4`
- Python: 3.14
- Current kernel: `7.2.4-1-cachyos`, driver built with Clang/LLVM
- LTS kernel: `6.18.50-1-cachyos-lts`, driver built with GCC

The wired `1532:00E7` and wireless `1532:00E8` transports must not be used at
the same time. They expose the same mouse serial and conflict as OpenRazer
D-Bus identities. HID instance suffixes such as `.0013` are also not stable and
must not be hard-coded in scripts.

## What was wrong in the tested PR head

### Missing sysfs cleanup

The PR creates the Naga V3 Pro `device_last_activity` sysfs attribute during
probe, but its Naga V3 Pro disconnect block did not remove that attribute.

A clean boot and first driver load worked, which hid the defect. A live
`razermouse` unload and reload with the receiver connected reproduced it:

1. The disconnect callback removed the other Naga attributes but left
   `device_last_activity` attached to the existing HID device object.
2. The next probe attempted to create the same filename and logged
   `sysfs: cannot create duplicate filename`.
3. Probe failed before valid driver data was installed.
4. Reading the stale attribute dereferenced invalid state and caused a kernel
   NULL-pointer Oops in `razer_attr_read_device_last_activity`.
5. Mouse input disappeared and module teardown became stuck until reboot.

This was a driver lifecycle bug, not a packaging or kernel-version problem.

### Blocking daemon startup while the mouse was asleep

`RazerNagaV3ProWireless.__init__()` called `wait_until_ready()` before normal
device construction. The wait had no overall deadline. If the receiver was
present while the mouse was sleeping or powered off, constructing that one
device withheld startup of the complete daemon.

The failure was reproduced with the exact PR code:

- The replacement process acquired `org.razer` but did not serve the useful
  D-Bus object tree.
- The available dock was withheld together with the unavailable mouse.
- Startup completed only after mouse movement.
- If the mouse never woke, the constructor could wait indefinitely.

### Shutdown depended on unavailable hardware

Stopping the daemon attempted control transfers to every device. With the
wireless mouse asleep, restoring driver mode raised
`TimeoutError: [Errno 110] Connection timed out`.

After the first exception was handled, hardware validation found a second
shutdown path: `close()` read the current DPI and wrote device mode before
stopping the mouse-monitor thread. Either operation could time out. Aborting
`close()` left the monitor thread alive, so the process still exceeded the
10-second systemd stop timeout and was killed with `SIGKILL`.

### Hotplug and retry state needed coordination

Moving readiness failures out of the constructor required the daemon to track
pending retries, removal events, additional HID interfaces, and shutdown
concurrently. Without explicit coordination, stale timer callbacks could
consume replacement retries, removed devices could be retried, and grouped HID
interfaces could be lost.

## What this branch changed

### Commit `4f929a86`: sysfs lifecycle fix

The Naga V3 Pro disconnect block now removes
`dev_attr_device_last_activity`, matching the attribute created during probe.

This is the smallest direct fix for the duplicate sysfs file, failed reprobe,
and stale attribute Oops.

### Commit `430529fa`: non-blocking readiness and safe shutdown

The daemon and device lifecycle changes are:

- Added `DeviceNotReadyError` to distinguish a present device that is not yet
  accepting commands from unrelated I/O failures.
- Moved the initial driver-mode write before background services and D-Bus
  object registration. A timeout therefore leaves no partially initialized
  device or monitor thread.
- Removed the unbounded `wait_until_ready()` call from the Naga wireless
  constructor.
- Added asynchronous device retries without withholding available devices or
  the daemon's D-Bus service.
- Added a retry limit of 15 scheduled attempts, nominally two seconds apart.
- Added locking and retry tokens so add, remove, stale callback, and shutdown
  paths do not corrupt pending retry state.
- Cancelled pending retries when a device is removed or the daemon stops.
- Preserved grouped additional HID interfaces across delayed hotplug handling.
- Made shutdown continue after an `OSError` or `TimeoutError` while resuming or
  closing one device.
- Made device close idempotent and best-effort for DPI and device-mode I/O.
- Guaranteed `_close()` runs and the object is marked closed even when the
  unavailable hardware rejects final commands. This ensures monitor and
  battery resources are released.

### Commit `7b22a814`: recoverable late discovery

The initial asynchronous implementation stopped after 15 retries. Since waking
the mouse does not emit a new udev add event, a mouse that slept beyond that
window could remain undiscovered until daemon restart or receiver reconnect.

The follow-up commit closes that gap:

- The 15 rapid retries remain at two-second intervals.
- After the rapid window, discovery continues quietly every 30 seconds until
  the device succeeds, is removed, or the daemon stops.
- The Naga wireless constructor performs one non-blocking valid-serial check
  before base construction.
- An empty, malformed, or unavailable serial raises `DeviceNotReadyError`
  before generated fallback identity, configuration, background service, or
  D-Bus initialization.
- The obsolete blocking `wait_until_ready()` implementation was removed.
- Internal exception imports are hidden from the daemon's hardware class
  discovery mechanism.
- Failed retries no longer log a misleading `Found valid device` message.

## Added regression tests

`daemon/tests/test_daemon_hotplug.py` contains 18 focused tests covering:

- Startup readiness failure scheduling a retry
- Hotplug readiness failure scheduling a retry
- Successful registration when a retry fires
- Transition from rapid retries to the slow retry interval
- Successful registration after a late wake in the slow retry phase
- Cancellation of a pending slow retry on removal
- No retry for unrelated I/O errors
- Retry cancellation on removal
- Removal ignored during shutdown
- Stale timer callbacks not consuming replacement retries
- Collector state reset after an error
- Preservation of additional HID interfaces
- Shutdown continuing after resume timeout
- Shutdown continuing after a device close timeout
- Driver-mode readiness failure before background service creation
- Valid Naga serial required before base construction
- Readiness exceptions excluded from hardware class discovery
- Resource cleanup and idempotence after close-time hardware errors

## Validation results

### Source and build checks

- Focused daemon tests: 18 passed
- Python byte compilation: passed
- Focused mypy check with third-party imports ignored: passed
- `git diff --check`: passed
- Driver build against `7.2.4-1-cachyos` with Clang/LLVM: passed
- Driver build against `6.18.50-1-cachyos-lts` with GCC: passed

The complete daemon suite ran 42 tests. It retained one pre-existing,
unrelated failure:

```text
test_effect_sync.EffectSyncTest.test_notify_run_effect_edge_case_3
```

The failure is a `NoneType` access in the legacy `effect_sync` test and was
present independently of this branch's changes.

### Driver runtime validation

The fixed module was installed with the receiver initially disconnected, then
the receiver was connected and probed normally. A subsequent live module
unload and reload was performed with the receiver connected, reproducing the
original failure trigger.

Results:

- All four Naga HID interfaces disconnected and rebound.
- `device_last_activity` was recreated normally.
- No duplicate sysfs filename was logged.
- No probe failure or kernel Oops occurred.
- Mouse input remained recoverable.
- The loaded and on-disk fixed module source version matched:
  `E5F7731EF43CC8A31322BBE`.

A driver-only rebind does not emit the USB add event that applies OpenRazer's
sysfs group permissions. One physical receiver reconnect was therefore used to
restore normal attribute permissions. This is expected udev behavior and is
separate from the fixed duplicate-file defect.

### Daemon runtime validation

Startup was tested with the receiver present and the mouse confirmed
unavailable:

- systemd started the daemon immediately.
- The daemon served the available dock over D-Bus.
- The Naga was not registered under a generated fallback serial.
- The unavailable Naga was retried separately.
- Repeated D-Bus requests for the dock remained responsive during retries.
- After mouse movement, a retry registered the real device serial and set
  device mode `03 00` without restarting the daemon.

Shutdown was first tested with a naturally idle mouse. This exposed the
close-time DPI read and surviving monitor-thread issue described above. After
the final cleanup change, the equivalent unavailable-hardware path was tested
by powering off the mouse while leaving its receiver and registered daemon
object present.

Final result:

- The driver-mode, DPI, and close-time mode operations logged expected timeout
  warnings.
- Monitor and device cleanup still completed.
- The daemon restarted in 1.95 seconds.
- systemd did not send `SIGKILL` or mark the service failed.
- The replacement daemon served the dock while retrying the powered-off mouse.
- Powering the mouse on registered its real serial without another restart.

The final restored runtime state was:

- `openrazer-daemon.service`: active
- Dock and Naga both present with real serials
- Naga device mode: `03 00`
- Naga idle timeout: `300` seconds
- No new duplicate-filename warning, Oops, or NULL-pointer fault

### Late-wake validation after rapid retry exhaustion

The final follow-up was installed and tested with the Naga powered off while
its receiver remained connected:

1. The daemon stopped cleanly and restarted in 2.07 seconds.
2. The dock was available immediately over D-Bus.
3. The Naga failed all 15 rapid valid-serial checks without generating a
   fallback serial.
4. The daemon logged the transition to a 30-second retry interval.
5. At least two long-tail retry cycles ran while the mouse remained off.
6. Repeated dock D-Bus requests completed in 2 to 3 ms during this period.
7. The mouse was powered on and moved without restarting the daemon or
   reconnecting the receiver.
8. A later slow retry registered the real device serial.
9. Device mode read back `03 00` and idle timeout remained `300` seconds.

Packaging the follow-up initially exposed an import-surface error:
`DeviceNotReadyError` was imported as a public name in `hardware.mouse`, so the
daemon's dynamic class discovery treated it as a device class and failed while
reading `USB_VID`. The import is now private, a regression test covers this
condition, and the corrected package starts normally.

## Test package state

The hardware validation used temporary Arch split packages built from the
working tree:

```text
openrazer-daemon-local 3.12.1.pr2904.fix1-6
openrazer-driver-dkms-local 3.12.1.pr2904.fix1-1
python-openrazer-local 3.12.1.pr2904.fix1-1
```

The daemon package has a later package release because shutdown, late-wake, and
dynamic class-discovery issues were found and corrected during runtime
validation. The driver source did not change after `fix1-1`.

The temporary PKGBUILD and package artifacts were outside this Git branch under
`/tmp/opencode/openrazer-pr2904-as-is-pkg`. They are not a reproducible part of
the branch and may disappear after reboot. A maintained package recipe should
be added separately if this branch is used for long-term installation.

## What is fixed

Under the tested hardware and software conditions, this derivative branch
fixes the confirmed regressions introduced or exposed by the tested PR head:

- Live reload no longer leaves `device_last_activity` behind or crashes on a
  stale attribute.
- An asleep or powered-off Naga no longer withholds D-Bus service for available
  devices.
- A later wake can register the Naga under its real serial during the retry
  window or subsequent slow retry phase.
- Empty serial responses no longer create a fallback Naga identity during
  readiness discovery.
- Shutdown no longer depends on successful control transfers to the sleeping
  mouse.
- Monitor resources are released even when close-time hardware I/O fails.

This does not mean the contributor's remote PR branch was changed. It means the
forked branch contains tested candidate fixes on top of the exact PR revision.

## Remaining risks and follow-up work

### Long-tail retry tradeoff

Once rapid discovery is exhausted, the daemon performs one valid-serial probe
every 30 seconds. This is intentionally low frequency and the tested dock
remained responsive, but a multi-hour powered-off run was not performed.
Future testing should confirm the receiver remains asleep, logs remain quiet at
normal log levels, and no unexpected power or USB side effects accumulate.

### Final natural-idle restart retest

The naturally idle restart test exposed the final monitor cleanup defect on an
intermediate package. The corrected code was then validated against the same
timeout behavior with the mouse physically powered off, which is a stronger
unavailable-hardware condition and exercised the same resume, DPI, mode, and
monitor cleanup paths.

The complete sequence was repeated on the final installed
`openrazer-daemon-local 3.12.1.pr2904.fix1-6` package on 2026-09-19:

1. The Naga received no movement or button input for 150 seconds, exceeding
   the earlier observed roughly 139-second monitor idle-recognition delay.
2. The user daemon stopped and restarted cleanly in about one second, without
   a systemd timeout or `SIGKILL`.
3. The replacement daemon initialized the dock and Naga and set the Naga to
   driver mode.
4. Waking the Naga restored pointer and click input; InputMapper also detected
   `F17`.
5. D-Bus confirmed the real device serial, device mode `03 00`, and
   idle timeout `300` seconds.

Qualification added on 2026-10-05: this verifies recovery after daemon restart,
which explicitly reapplies driver mode during initialization. The 150-second
wait was shorter than the reported 300-second timeout, and physical sleep or
the monitor's idle state was not captured. It does not establish continuous
idle/wake recovery without restarting the daemon. See
`naga-f17-recurrence-investigation-2026-10-05.md` for the later recurrence,
fake-only recovery reproductions, and the separate panel forwarding defect.

### Existing monitor behavior was not redesigned

This branch retains the contributor's activity monitor and wake-restoration
algorithm. Earlier testing observed that the hardware visibly slept before
`device_last_activity` allowed the monitor to declare it idle. Idle recognition
was delayed until the reported inactivity reached roughly 139 seconds.

One complete final-package runtime idle/restart/wake restoration cycle was
confirmed. Repeated idle/wake cycles, immediate wake after LED shutdown, and a
silently dropped first mode write still need broader validation.

### Scope of hardware coverage

The daemon lifecycle changes are shared code, but physical validation covered
only the Naga V3 Pro wireless transport and Mouse Dock Pro. Other OpenRazer
devices, the wired Naga transport, and the PR's `0xD2` to `KEY_F18` input
mapping were not physically tested.

### Unrelated shutdown log noise

Python 3.14 and the installed `daemonize` package still print an ignored
`SystemExit: 0` traceback from the package's atexit callback during otherwise
successful daemon shutdown. This is noisy but did not cause the systemd timeout
or affect the fixes in this branch.

## Recommended next steps

1. Run multiple idle/wake cycles and verify driver mode and `KEY_F17` every
   time.
2. Run a longer powered-off test of the 30-second retry phase.
3. Run the full test suite on the project's supported Python versions to
   separate the Python 3.14 legacy test behavior from branch regressions.
4. Test at least one unrelated OpenRazer device because the readiness and close
   changes are in shared daemon code.
5. Add or preserve a reproducible package recipe outside temporary storage.
7. If upstream submission is desired, rebase onto the latest PR or target
   branch, keep the driver lifecycle fix separate, and present the daemon
   lifecycle changes with the focused regression tests.
