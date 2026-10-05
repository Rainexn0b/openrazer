# Naga F17 recurrence investigation, 2026-10-05

Current canonical checkout: `/home/cyril/Projects/openrazer`, branch
`test-pr-2904-edualb`. The former linked worktree was retired after all code and
documents were consolidated here. Earlier source paths below are historical.

## Summary

The recurrence involves two distinct failure layers:

- OpenRazer's installed activity monitor has incomplete driver-mode recovery.
  Three fake-only reproductions confirm gaps in the retained algorithm.
- The running Naga Control AppImage can permanently stop forwarding after a
  transient mode-read failure. Its recovery regression fails against the live
  process's code and passes against current project source.

The original transition to firmware mode was not captured. These findings do
not prove that Naga Control caused it, or that a particular monitor gap caused
this incident. No product files, services, input grabs, configuration, or device
mode were changed during this investigation.

## Runtime provenance

Installed packages remained:

```text
openrazer-daemon-local 3.12.1.pr2904.fix1-6
openrazer-driver-dkms-local 3.12.1.pr2904.fix1-1
python-openrazer-local 3.12.1.pr2904.fix1-1
```

The installed `mouse_monitor.py` matches the derivative at `7b22a814`
byte-for-byte. Loaded and on-disk `razermouse` source versions both report
`E5F7731EF43CC8A31322BBE`, matching the previously validated driver. This is not
evidence of a return to the old sysfs cleanup defect.

OpenRazer PID `1526653` started on 2026-10-04 at 19:30:22 +02:00. The Naga Control
process inspected was PID `1694874`, started on 2026-10-05 at 18:58:59 +02:00,
using:

```text
/tmp/.mount_naga-cJFFEhK/usr/lib/python3.14/site-packages/naga_control/service/runtime.py
```

Its runtime lacked the `_needs_rescan = True` assignment present in current
`Naga-controlpanel/src/naga_control/service/runtime.py:142`. The AppImage backing
that process had been unlinked and replaced at the installation pathname, but
the running FUSE mount still referenced the old inode. A newer image on disk
does not update the running process. No installation or restart was performed
as part of this investigation.

## Incident evidence

The panel's `docs/hardware-validation.md:570-582` records the prior observation:

- F17 stopped during normal HyperSpeed use.
- OpenRazer returned `0:0` while the panel requested software mode.
- Panel status was unavailable with `hardware topology rescan is pending`.
- A verified `3:0` write alone did not restore its forwarding session.
- Restarting the panel with no held physical keys restored forwarding, and the
  user confirmed F17 worked again.

Kernel logs independently show these Naga control-transfer timeouts:

```text
2026-10-05T18:50:19+02:00 class 07, command 83: idle-time GET
2026-10-05T18:50:20+02:00 class 00, command 84: device-mode GET
2026-10-05T18:50:49+02:00 class 07, command 83: idle-time GET
```

No matching receiver disconnect, USB reset, or system suspend/resume was found
in the queried 18:45-18:59:15 incident window. There was no mouse-monitor
idle/reapply message in that window. The last such pair in the inspected logs
was on October 4 at 19:44:22 and 19:46:27. Absence of a recovery log does not
identify the original mode-reset trigger.

The shutdown SIGBUS in the old panel AppImage is a separate observed failure.
There is insufficient evidence to attribute mode loss to it. The inspected
installer replaces its image via a temporary sibling and `mv`; the current
backing inode evidence does not demonstrate in-place truncation.

## OpenRazer reproductions

The temporary fake-only harness is
`/tmp/opencode/test_naga_mouse_monitor_recovery.py`. Run it with:

```bash
PYTHONDONTWRITEBYTECODE=1 python -B /tmp/opencode/test_naga_mouse_monitor_recovery.py
```

All three reproduction assertions passed. Passing means the defective behavior
was reproduced, not that recovery was fixed. The harness mocks driver reads,
mode writes, clocks, and sleeps and does not start a monitor thread.

1. With a 300-second configured timeout and activity ages
   `60000 -> 1000 -> 200 -> 400 -> 600 -> 800`, software never marks idle and
   performs no mode restore. A status report resetting the activity timestamp
   can therefore hide a physical sleep/wake transition. That real status-report
   sequence was not captured in this incident.
2. Starting idle and simulating a wake, the first mode write returns normally
   but is dropped. The monitor clears pending recovery after that single write
   without readiness or mode verification. Fake firmware remains in mode zero.
3. A cached idle timeout of 300 seconds becomes zero after a read timeout and
   remains zero for 30 seconds. During that interval even a 301-second activity
   age does not mark idle.

Relevant derivative source:

- `daemon/openrazer_daemon/misc/mouse_monitor.py:103-121`: timeout cache and
  replacement with zero on failure.
- `daemon/openrazer_daemon/misc/mouse_monitor.py:123-138`: single write and
  unconditional clearing of pending recovery on apparent success.
- `daemon/openrazer_daemon/misc/mouse_monitor.py:150-169`: restoration requires
  a previously recognized idle state.
- `driver/razermouse_driver.c:148-151`: `BUSY` responses count as success.
- `driver/razermouse_driver.c:6198-6235`: activity classification uses nonzero
  report bytes rather than decoded user movement/button semantics.

Mode `0:0` prevents the special firmware report used by the existing
`0x59 -> HID usage 0x6c -> KEY_F17` translation. The F17 translation itself has
not changed in the installed derivative.

## Panel differential reproduction

The existing fake-worker test
`tests/test_service_mode_recovery.py::test_transient_read_failure_rescans_a_fenced_worker`
was run against both the running image's service class and current source.
Neither version was run against real hardware.

```text
Installed runtime: FAIL, timeout waiting for mode_ready
status=unavailable, observed_mode=None, mode_ready=False
mode_error="hardware topology rescan is pending"
rescans=1, sessions=1, worker_stale=True

Current source: PASS
status=available, observed_mode=software, mode_ready=True
mode_error=None
rescans=2, sessions=2, worker_stale=False
```

Stopping forwarding fences the hardware worker. The installed code does not
request the rescan needed to clear that fence, so ordinary polling cannot
recover. Current source already fixes this by setting `_needs_rescan = True`
alongside `mark_topology_stale()` before stopping the session.

The three focused source modules also passed all 32 tests:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -p no:cacheprovider \
  -m 'not hardware' tests/test_openrazer_mode.py tests/test_service_mode.py \
  tests/test_service_mode_recovery.py
```

## Prior acceptance-test limitation

The earlier successful no-input/restart/wake sequence proved restart and input
recovery after startup explicitly reapplied driver mode. It did not prove
continuous idle/wake recovery without a daemon restart.

The recorded 150-second no-input wait was also shorter than the 300-second
reported timeout, and that run did not capture physical sleep or the monitor's
idle state. It should not be treated as proof that the monitor recognized idle.
The earlier debrief already retained immediate-wake and dropped-first-write
coverage as outstanding.

## State observed after investigation

Read-only D-Bus queries showed the already-recovered session:

- OpenRazer active; dock and Naga present.
- Naga real device serial verified, mode `3:0`, idle timeout 300 seconds.
- Panel available, desired/observed software mode, mode ready, not calibrating,
  and no settings failures.

No new physical F17 press/release capture was performed. Healthy D-Bus state is
not a substitute for that capture.

## Scoped follow-up

1. Deploy and restart the panel with the existing recovery fix only after its
   own hardware/release gates. Verify running-image provenance afterward.
2. Harden OpenRazer recovery: preserve valid timeout knowledge on transient
   errors, verify mode restoration instead of trusting the first write, and
   avoid requiring a perfectly observed idle transition.
3. Respect explicit mode intent. The panel now intentionally supports firmware
   `0:0` as well as software `3:0`; unconditional forcing of driver mode would
   conflict with that feature. The monitor currently consults startup
   `DRIVER_MODE`, not the last external mode request.
4. Capture repeated idle/wake cycles without daemon restart, including wake
   immediately after LEDs extinguish. First isolate OpenRazer from remapping,
   then repeat with the panel and its verified recovery build.
5. Capture mode requests/readiness and physical F17 down/up separately from
   generated panel output. Existing logs do not identify the caller or firmware
   event responsible for the initial mode transition.

## OpenRazer recovery follow-up

The OpenRazer fixes were implemented in the derivative working tree on October
5. The panel fixes remain delegated to the separate Naga Control agent; no
panel code or service was changed here.

### Behavior changes

- The monitor verifies driver mode during recent activity, without requiring a
  previously recognized idle transition. Activity is sampled every 200 ms;
  USB mode checks occur at most every two seconds while activity age is at most
  5000 ms. Inactive or unavailable activity samples do not trigger mode I/O.
- Each check requires a live valid serial. Mode `3:0` is read before deciding
  whether a write is necessary. After writing, the monitor requires a matching
  readback before logging recovery. Empty/busy readiness, I/O errors, malformed
  reads, or a dropped write are retried on a later recent-activity check.
- Idle-time refresh retains the last positive timeout after an error or zero
  response. It is refreshed only during serial-ready recent activity, and idle
  recognition is no longer a prerequisite for mode recovery.
- Naga wireless mode requests update the instance's `DRIVER_MODE` intent before
  writing. Explicit firmware mode cancels automatic recovery even if its write
  fails; the requesting client remains responsible for retrying that firmware
  request. An explicit driver-mode request re-enables recovery. Startup config
  and persisted config are not modified by this runtime policy.
- A per-device reentrant lock orders explicit requests, verification, resume,
  and shutdown. Activity freshness is rechecked after acquiring the lock.
  Screensaver resume respects current intent and restores notification and
  persistence flags even when hardware I/O fails.
- Shutdown interrupts the poll wait and stops/joins the monitor before DPI,
  firmware-mode, and resource teardown. A monitor that failed to start is not
  joined, preserving cleanup of partially constructed devices.

Only `hardware/mouse.py` and `misc/mouse_monitor.py` were changed in product
code. Shared base-device and kernel code remain unchanged.

### Verification

- Twenty new fake-only recovery/lifecycle tests passed.
- All eighteen existing hotplug/readiness/shutdown tests passed.
- Full daemon suite: 62 tests, 61 passed; only the previously recorded
  `test_notify_run_effect_edge_case_3` error remains.
- The integrated real-thread/fake-I/O close-order test passed 25 consecutive
  runs.
- `mypy --ignore-missing-imports`, `compileall`, and whitespace checks passed.
- A daemon-only package was built successfully, and all twenty new tests also
  passed when importing its staged installed modules.

### Installed state

After explicit approval to restart OpenRazer, the daemon-only package
`openrazer-daemon-local 3.12.1.pr2904.fix1-7` was installed through a visible
Konsole sudo prompt. The driver and Python client package remain at `fix1-1`.
No driver reload was needed.

OpenRazer restarted at 2026-10-05 20:16:49 +02:00 with PID `1743325`. Both changed
installed modules compare byte-for-byte with the tested working tree. D-Bus
showed both dock and Naga, Naga mode `3:0`, and the panel available with
desired/observed software mode and `mode_ready=true`. Existing dock lighting
effect-sync timeouts appeared during profile application; those are outside
this mode-recovery patch.

Package recipe and artifacts are under
`/tmp/opencode/openrazer-naga-recovery-pkg`. The recipe builds base commit
`7b22a814` with the two changed working-tree modules and applies the same Arch
`plugdev` to `openrazer` group substitution as the previous package. These
temporary artifacts may disappear after reboot. Recovery code and tests were
subsequently committed and pushed as `76c7abf2` on `fork/test-pr-2904-edualb`.
This documentation remains intentionally untracked and outside that commit.

### Remaining hardware acceptance

No new physical F17 capture or repeated sleep/wake sequence was performed in
this implementation round. Test multiple actual sleep/wake cycles without
restarting the daemon, including immediate wake after LED shutdown and explicit
firmware/software handoff coordinated with the panel agent.

Normal active checks add two control exchanges every two seconds, plus the
cached idle-time refresh; a repair adds a write and readback. Validate input
latency and receiver behavior under normal use. The first shortcut press can
precede recovery, and readiness that remains unavailable beyond the five-second
recent-activity window requires subsequent input before another check. The
kernel's existing activity classifier is unchanged, so a status report may
still make an inactive device appear briefly active.
