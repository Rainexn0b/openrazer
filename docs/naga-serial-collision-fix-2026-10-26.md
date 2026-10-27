# Naga Serial Collision Fix 2026-10-26

Date: 2026-10-26.
Baseline: `26b0eeb5` (`naga-v3-pro-compat`).
Scope: Naga V3 Pro wired `1532:00E7` and wireless `1532:00E8` only.

## Problem

One physical Naga V3 Pro reports the same serial over both transports.
OpenRazer uses that serial as the D-Bus object path
`/org/razer/device/<serial>` and as the lookup identity.
Without a guard, the second route runs construction past the cached
serial: it writes driver mode before any D-Bus or collection step. On a
shared connection the duplicate object-path registration then fails
(measured pre-fix behavior: `KeyError` from the D-Bus object-path
registry before `DeviceCollection.add` is reached), so no map overwrite
occurs on that path — but the second fixture's mode write has already
happened. The collection code itself assigned both maps without a
duplicate check, so a second add with the same serial would replace the
incumbent mapping. The guard closes both the side-effect window and the
map hazard.

`DeviceCollection.add` overwrote without a duplicate check.
`RazerDevice.__init__` cached the serial early, then wrote driver mode,
started effect resources, and registered D-Bus before the daemon added the
device to the collection. A collection-only guard was therefore too late.

## Fix

Collection-scoped single-owner deferral before side effects.

`daemon/openrazer_daemon/misc/serial_identity.py`:

- `DuplicateDeviceError` for defensive collection rejection. Messages are
  generic and carry no serial or path values.
- `is_trustworthy_naga_serial` accepts only `A-Z0-9`, length 1 to 22, and
  rejects empty, `UNKNOWN_*`, lowercase, symbols, and overlong values.
- `is_naga_v3_pro_class` matches only exact `USB_VID 0x1532` with
  `USB_PID 0x00E7` or `0x00E8` via direct attribute equality. It does not
  int-coerce strings and returns false for mocks without those values.
- `serial_guard_kwargs(device_class, collection)` returns
  `{"serial_guard": callback}` only for exact Naga classes with a live
  collection, otherwise `{}`. Other classes keep their original call
  contract and dummy mocks receive no new kwarg.
- The callback lazily raises retryable `DeviceNotReadyError`: `EAGAIN` for
  untrustworthy Naga serials, `EBUSY` when the serial already exists in the
  daemon's actual `DeviceCollection`. No second sysfs read, no global
  reserve or release state. The existing daemon lock already encloses
  startup, construction, add, remove, and retry.

`hardware/device_base.py`:

- `RazerDevice.__init__` gains trailing `serial_guard=None` and invokes it
  immediately after the cached serial, before storage naming beyond the
  serial, mode writes, effect sync, persistence, battery, or D-Bus
  registration. Wireless subclasses forward `*args, **kwargs`, so the guard
  runs before their monitor starts.

`device.py`:

- `DeviceCollection.add` rejects duplicate ID or serial before creating the
  wrapper, registering the parent, or touching maps. Distinct devices are
  unchanged.
- `__delitem__` only removes the counterpart map when it points at the same
  object, so removal cannot orphan a survivor.

`daemon.py`:

- Both startup and hotplug construction sites add helper-generated kwargs.
  Duplicates now fail inside production construction as retryable errors and
  flow through the existing fast then slow retry machinery. No automatic
  preemption and no new API. Removal keeps its existing close then
  unpublish barrier and retry stays token-fenced.

Public `getDevices`, serial, object-path, and storage APIs are unchanged.
No raw HID, no new driver operations, and no live hardware changes.

## Verification (final)

Synthetic serials only, for example `SN100001` and `SN100002`.
No live sysfs reads, grabs, writes, daemons, or user-bus proofs.

Mocked-constructor suites (fake sysfs, D-Bus/effect/battery owners
mocked, exact constructor call counts asserted):

- Production guard through real Naga subclasses with temporary fake
  sysfs: a duplicate with the same synthetic serial raises before any
  second `DBusService.__init__`, mode, effect, battery, or monitor work.
  The incumbent stays addressable and is never closed by the rejected
  route.
- Startup with the same serial on both PIDs in both orders: one incumbent
  publishes, the other defers via the production guard path.
- Hotplug with the same serial in both orders: same single-owner result.
- A real waiting retry publishes only after incumbent close plus
  unpublish. Removing the waiting route cancels its retry. Stale tokens
  cannot consume a replacement. Shutdown blocks even a correct token.
  Close failure and unpublish failure each keep the incumbent and prevent
  new waiting side effects.
- Generated `UNKNOWN_*` and malformed serials cannot enter guarded Naga
  registration. They fail with `EAGAIN` before D-Bus.
- Distinct serials publish together. Exact non-Naga classes receive no
  guard kwarg and keep their prior contract.
- Collection rejects duplicate ID or serial before maps or parent work and
  removal plus re-add cannot orphan a survivor.
- New warning text carries no serial or physical path values.

Counts: the pre-existing baseline without these suites is 72 daemon
tests with the single known `test_notify_run_effect_edge_case_3`
`TypeError` at `test_effect_sync.py:143`. With the 24 mocked-suite tests
plus the private-bus regression below (25 new), the full run is 97 tests
with the same single pre-existing error. The full suite does not fully
pass because of that pre-existing failure; it is unchanged by this fix.

Private-bus proof (`daemon/tests/test_serial_dbus.py`) covers real
D-Bus registration on an isolated bus; see the section below. It also
runs a guard-omitted control (pre-fix call contract, `serial_guard`
defaults to `None`): the duplicate D-Bus object-path registration raises
`KeyError` before `collection.add`, while the second fixture's
driver-mode write has already happened and effect/DPI restores have not
yet run. With the guard, the same attempt raises retryable `EBUSY`
before any second mode write.

## Limits

- Attaching both wired and wireless transports at once remains an
  unsupported conflict for Naga Control. This daemon repair prevents
  overwrite and duplicate D-Bus registration with ordered handover, but it
  does not make both routes simultaneously usable. The UI snapshot with
  both attached is still blocked by design.
- Waiting-route timing follows existing retry intervals and may be slow
  after repeated failures by design.

## Private-Bus Regression 2026-10-26

New: `daemon/tests/test_serial_dbus.py` (339 physical lines).

- Outer unittest launches an isolated `dbus-run-session` subprocess running
  this same file with `--dbus-child`. The outer wipes inherited `DBUS_*`
  addresses and isolates `HOME`, `XDG_RUNTIME_DIR`, `XDG_CONFIG_HOME`,
  `XDG_CACHE_HOME`, `XDG_DATA_HOME`, and `TMPDIR` under `/tmp/opencode`.
- The child uses the actual `RazerNagaV3ProWired` and
  `RazerNagaV3ProWireless` constructors against fake tmp sysfs only
  (`device_serial`, `device_type`, `device_mode`; `testing=True`, so the
  source never inspects real `/dev/input/by-id`). No pyudev import, no
  live service, bus, or device activity. Synthetic serials only
  (`SN100001`, `SN100002`, `SN100003`).
- Battery manager init is stubbed and `MouseMonitor` is mocked; driver-mode
  writes to temp files and real D-Bus publication are preserved. A GLib
  loop serves the private bus while separate retained private client
  connections round-trip `getSerial`/`getVidPid` at
  `/org/razer/device/<serial>`.
- Both arrival orders asserted: the second same-serial construction raises
  retryable `DeviceNotReadyError` `EBUSY` before any second `device_mode`
  change (file bytes and directory listing unchanged), before any new
  background worker (wireless monitor call count unchanged), and before
  any D-Bus registration (incumbent `getVidPid` still returns the first
  PID). The child refuses a non-`/tmp/opencode` `TMPDIR`, so isolation is
  enforced rather than assumed.
- After owner `close` plus `remove_from_connection` plus collection
  `remove`, the waiting route constructed with the actual same guard
  publishes at the same object path and the client round-trip resolves the
  new PID. Distinct serials publish together (`00E7` and `00E8` verified
  by path).
- Guard-omitted control: constructing the wireless route for an owned
  serial without `serial_guard` fails at D-Bus registration with
  `KeyError` naming the duplicate object path, before `collection.add`;
  the second fixture's `device_mode` already reads driver mode while its
  DPI/effect restores never ran. The incumbent stays registered and
  addressable throughout.
- Deterministic teardown: every constructed device is tracked immediately
  and released (close, unpublish, remove) before mocks exit; the GLib
  loop is quit and joined with an asserted stop; retained client buses
  close only afterwards (closing a private client connection while the
  server thread was still dispatching crashed dbus-python in a probe);
  a final collection pass runs before tmp removal. Per-call D-Bus and
  loop-join timeouts bound the child alongside the outer subprocess
  timeout.
- Exact coverage: real constructors plus real `DeviceCollection` guard
  plus real private-bus export. Full-daemon `_add`/`_retry` scheduling
  with fake pyudev objects and mocked timers remains covered by
  `test_serial_handover.py` mocks (including its exact-path fence, now
  trailing-slash normalized), not by this file.

Evidence (2026-10-26, `26b0eeb5` baseline, no product edits; run from the
repository root):

```text
PYTHONPATH=$PWD/daemon:$PWD/pylib /usr/bin/python3 -m unittest discover -s daemon/tests -p 'test_serial_dbus.py' -v
test_private_bus_duplicate_defers_and_hands_over ... ok
Ran 1 test in 0.089s
OK
exit=0
```

```text
ruff check --isolated --select E9,F63,F7,F82 daemon/tests/test_serial_dbus.py
All checks passed!
ruff_exit=0
git diff --check
diffcheck_exit=0
```

```text
/usr/bin/python3 -m autopep8 --max-line-length 500 --ignore E402 --diff daemon/tests/test_serial_dbus.py
(empty diff)
autopep8_diff_exit=0
```

Child payload under correct isolation (repeated runs deterministic;
child stderr empty):

```text
{"client_round_trip": true, "orders": ["wired-first", "wireless-first"], "handover_pid_switched": true, "distinct_coexist": true, "glib_stopped": true, "noguard_path_error": true, "noguard_second_mode_written": true, "noguard_incumbent_intact": true}
child_exit=0
```

Final supervisor verification (independent rerun, same baseline):

- isolated `test_serial*.py`: 25 tests OK.
- full daemon suite: 97 tests with exactly the same pre-existing
  `test_notify_run_effect_edge_case_3` `TypeError` at
  `test_effect_sync.py:143`; exit 1 from that pre-existing failure only.
- client suite: 7 tests OK.
- `git diff --check`: pass.
- `ruff` isolated `E9,F63,F7,F82`: all 4 new Python files pass.
- `py_compile`: all 7 changed/new Python files pass.
- `autopep8 --max-line-length 500 --ignore E402 --diff` over all 7
  files: only `test_serial_dbus.py` line 261 lambda over-indented by 1;
  fixed to 24 spaces by single-line patch (no mass format); re-check
  empty.
- `pylint --errors-only`: toolchain BLOCKED, not a verdict — it crashed
  with `F0002` `TypeError: NodeNG.infer() got an unexpected keyword
  argument 'asname'` across imports. Recorded as blocked, not pass.
- Naga full standard checks (supervisor background task, exit 0):
  `.venv/bin/ruff check . && .venv/bin/ruff format --check . &&
  .venv/bin/pyright && .venv/bin/pytest -q` in Naga-controlpanel —
  All checks passed; 542 files already formatted; 0 errors, 0 warnings,
  0 informations; 4806 passed, 2 skipped (coverage missing),
  5 hardware deselected, 9 dbus_next warnings, 722.37s. Concurrent
  other-user dock-settings work added untracked files in that tree while
  the command ran; no edits from this task there, and no exact/stable
  other-source revision is certified here.

Limits: synthetic serials only; no live sysfs reads/grabs/writes,
daemons, GUI/service, installation, or hardware tests. Dual-transport
physical proof, natural retry timing after repeated failures, and any
live-bus behavior remain pending the attentive physical gate.
Dual-attach/screenshot state remains conflicted by design. This file
does not change the dual-transport conflict policy for Naga Control.

## Packaging Candidate 2026-10-27

Packaging candidate (uncommitted until the supervisor stages the combined
commit): `packaging/arch-canonical/PKGBUILD` moves to
`3.12.1.pr2904.fix3-1`, its `check()` gate runs the 6 pre-existing suites
plus the 3 new serial suites (80 tests total) in a private fake-safe
environment, and `daemon/tests/test_serial_dbus.py` accepts a portable
fixture root (shared scratch preferred, standard tempfile fallback, exact
root passed from parent and verified by the child) with no product-behavior
change. Upgrade, rollback, activation, and install-verification steps are in
[UPGRADE-fix3](../packaging/arch-canonical/UPGRADE-fix3.md). No combined
commit SHA, build, or install receipt is claimed here; the v1 tag and the
Naga published pin remain on fix2 until attended acceptance passes.
