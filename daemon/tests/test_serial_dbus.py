# SPDX-License-Identifier: GPL-2.0-or-later
"""Private-bus serial-collision regression with real Naga classes.

Outer unittest launches an isolated ``dbus-run-session`` subprocess running
this same file with ``--dbus-child``. The child uses the actual
``RazerNagaV3ProWired``/``RazerNagaV3ProWireless`` constructors against fake
tmp sysfs only (``testing=True``), real D-Bus export on the private bus, and
separate retained private client connections served by a GLib loop. Battery
and mouse monitor workers are mocked; mode writes to temp files are
preserved. The outer fixture root prefers shared scratch when available and
falls back to the standard tempfile root on builders (overridable for a
pinned package gate); the exact root is passed to the child, which verifies
it rather than assuming a machine path. A guard-omitted control records the
pre-fix baseline behavior.
Client buses close only after the server loop has quit and joined; closing
a private client connection while the server thread was still dispatching
crashed dbus-python in a probe, so teardown order is fixed and asserted.
"""

import configparser
import errno
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SERIAL_SHARED = "SN100001"
SERIAL_OTHER = "SN100002"
SERIAL_NOGUARD = "SN100003"
WIRED_ID = "0003:1532:00E7.0001"
WIRELESS_ID = "0003:1532:00E8.0001"
NOGUARD_WIRED_ID = "0003:1532:00E7.0009"
CHILD_FLAG = "--dbus-child"
OUTER_TIMEOUT = 120
CALL_TIMEOUT = 10
LOOP_JOIN_TIMEOUT = 10
PREFERRED_FIXTURE_ROOT = "/tmp/opencode"
CHILD_ROOT_ENV = "NAGA_SERIAL_DBUS_ROOT"
PARENT_OVERRIDE_ENV = "NAGA_SERIAL_DBUS_PARENT"


def _clean_dbus_env(env):
    return {k: v for k, v in env.items() if not k.startswith("DBUS_")}


def _outer_base_parent():
    override = os.environ.get(PARENT_OVERRIDE_ENV, "")
    if override:
        return override
    if os.path.isdir(PREFERRED_FIXTURE_ROOT):
        return PREFERRED_FIXTURE_ROOT
    return tempfile.gettempdir()


def _resolve_dbus_run_session():
    return shutil.which("dbus-run-session") or "/usr/bin/dbus-run-session"


def _run_private_child():
    base = tempfile.mkdtemp(prefix="naga-serial-dbus-", dir=_outer_base_parent())
    env = _clean_dbus_env(os.environ)
    env["HOME"] = os.path.join(base, "home")
    env["XDG_RUNTIME_DIR"] = os.path.join(base, "run")
    env["XDG_CONFIG_HOME"] = os.path.join(base, "config")
    env["XDG_CACHE_HOME"] = os.path.join(base, "cache")
    env["XDG_DATA_HOME"] = os.path.join(base, "data")
    env["TMPDIR"] = os.path.join(base, "tmp")
    env[CHILD_ROOT_ENV] = base
    for key in ("HOME", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME", "XDG_DATA_HOME", "TMPDIR"):
        os.makedirs(env[key], exist_ok=True)
    cmd = [_resolve_dbus_run_session(), "--", sys.executable,
           os.path.abspath(__file__), CHILD_FLAG]
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True,
                              text=True, timeout=OUTER_TIMEOUT)
    except Exception:
        shutil.rmtree(base, ignore_errors=True)
        raise
    return proc, base


class PrivateBusSerialCollisionTest(unittest.TestCase):
    def test_private_bus_duplicate_defers_and_hands_over(self):
        proc, base = _run_private_child()
        try:
            self.assertEqual(proc.returncode, 0,
                             "child failed:\nSTDOUT:\n%s\nSTDERR:\n%s" % (proc.stdout, proc.stderr))
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertTrue(payload.get("client_round_trip"), payload)
            self.assertEqual(payload.get("orders"), ["wired-first", "wireless-first"], payload)
            self.assertTrue(payload.get("handover_pid_switched"), payload)
            self.assertTrue(payload.get("distinct_coexist"), payload)
            self.assertTrue(payload.get("glib_stopped"), payload)
            self.assertTrue(payload.get("noguard_path_error"), payload)
            self.assertTrue(payload.get("noguard_second_mode_written"), payload)
            self.assertTrue(payload.get("noguard_incumbent_intact"), payload)
        finally:
            shutil.rmtree(base, ignore_errors=True)


def _child_make_sysfs(root, serial):
    path = tempfile.mkdtemp(prefix="fake-naga-", dir=root)
    with open(os.path.join(path, "device_serial"), "w") as handle:
        handle.write(serial)
    with open(os.path.join(path, "device_type"), "w") as handle:
        handle.write("mouse")
    with open(os.path.join(path, "device_mode"), "wb") as handle:
        handle.write(bytes([0x00, 0x00]))
    return path


def _child_config():
    config = configparser.ConfigParser()
    config["Startup"] = {
        "restore_persistence": "False",
        "persistence_dual_boot_quirk": "False",
        "battery_notifier": "False",
    }
    persistence = configparser.ConfigParser()
    persistence.status = {"changed": False}
    return config, persistence


def _read_mode(path):
    with open(os.path.join(path, "device_mode"), "rb") as handle:
        return handle.read()


def _child_client_get(serial, buses):
    import dbus
    bus = dbus.SessionBus(private=True)
    buses.append(bus)
    obj = bus.get_object("org.razer", "/org/razer/device/" + serial)
    misc = dbus.Interface(obj, "razer.device.misc")
    got_serial = str(misc.getSerial(timeout=CALL_TIMEOUT))
    vidpid = [int(v) for v in misc.getVidPid(timeout=CALL_TIMEOUT)]
    return got_serial, vidpid


def _child_build(cls, path, number, config, persistence, counter, collection):
    from openrazer_daemon.misc import serial_identity
    kwargs = serial_identity.serial_guard_kwargs(cls, collection)
    return cls(device_path=path, device_number=number, config=config,
               persistence=persistence, testing=True, additional_interfaces=[],
               additional_methods=[], unknown_serial_counter=counter, **kwargs)


def _release(dev, dev_id, collection):
    # Strict success-path teardown: errors propagate, never swallowed.
    try:
        dev.close()
    finally:
        try:
            dev.remove_from_connection()
        finally:
            collection.remove(dev_id)


def _child_order(first_cls, first_id, second_cls, second_id, collection,
                 root, counter, buses, owned):
    import unittest.mock as mock
    from openrazer_daemon.hardware.device_base import DeviceNotReadyError
    config, persistence = _child_config()
    first_path = _child_make_sysfs(root, SERIAL_SHARED)
    first = _child_build(first_cls, first_path, 0, config, persistence, counter, collection)
    owned.append((first, first_id, collection))
    assert first._testing is True
    assert first.event_files == []
    assert first_path.startswith(root)
    collection.add(first_id, first.serial, first)
    got_serial, vidpid = _child_client_get(SERIAL_SHARED, buses)
    assert got_serial == SERIAL_SHARED, (got_serial, SERIAL_SHARED)
    assert vidpid == [0x1532, first_cls.USB_PID], vidpid
    assert _read_mode(first_path) == bytes([0x03, 0x00])
    second_path = _child_make_sysfs(root, SERIAL_SHARED)
    before_mode = _read_mode(second_path)
    before_files = sorted(os.listdir(second_path))
    from openrazer_daemon.misc import mouse_monitor as _mm
    monitor_calls = _mm.MouseMonitor.call_count if isinstance(_mm.MouseMonitor, mock.MagicMock) else None
    try:
        duplicate = _child_build(second_cls, second_path, 1, config, persistence, counter, collection)
    except DeviceNotReadyError as err:
        assert err.errno == errno.EBUSY, err
        assert SERIAL_SHARED not in str(err), str(err)
    else:
        owned.append((duplicate, second_id, collection))
        raise AssertionError("duplicate serial must raise EBUSY")
    assert _read_mode(second_path) == before_mode
    assert sorted(os.listdir(second_path)) == before_files
    if monitor_calls is not None and second_cls.__name__.endswith("Wireless"):
        assert _mm.MouseMonitor.call_count == monitor_calls
    got_serial2, vidpid2 = _child_client_get(SERIAL_SHARED, buses)
    assert got_serial2 == SERIAL_SHARED
    assert vidpid2 == [0x1532, first_cls.USB_PID], (vidpid2, first_cls)
    assert len(collection) == 1
    _release(first, first_id, collection)
    owned.remove((first, first_id, collection))
    waiting = _child_build(second_cls, second_path, 1, config, persistence, counter, collection)
    owned.append((waiting, second_id, collection))
    collection.add(second_id, waiting.serial, waiting)
    got_serial3, vidpid3 = _child_client_get(SERIAL_SHARED, buses)
    assert got_serial3 == SERIAL_SHARED
    assert vidpid3 == [0x1532, second_cls.USB_PID], (vidpid3, second_cls)
    _release(waiting, second_id, collection)
    owned.remove((waiting, second_id, collection))
    return True


def _child_noguard_control(root, counter, buses, owned):
    # Guard omitted (pre-fix call contract, serial_guard defaults to None):
    # the duplicate D-Bus object-path registration fails before
    # collection.add, but the second fixture's driver-mode write has already
    # happened. The failed half-built object is unreferenceable here, so the
    # caller collects garbage before tmp removal (see _child_main).
    from openrazer_daemon.device import DeviceCollection
    from openrazer_daemon.hardware.mouse import RazerNagaV3ProWired, RazerNagaV3ProWireless
    config, persistence = _child_config()
    collection = DeviceCollection()
    first_path = _child_make_sysfs(root, SERIAL_NOGUARD)
    first = _child_build(RazerNagaV3ProWired, first_path, 7, config, persistence, counter, collection)
    owned.append((first, NOGUARD_WIRED_ID, collection))
    collection.add(NOGUARD_WIRED_ID, first.serial, first)
    second_path = _child_make_sysfs(root, SERIAL_NOGUARD)
    assert _read_mode(second_path) == bytes([0x00, 0x00])
    outcome = {"path_error": False, "second_mode_written": False, "incumbent_intact": False}
    try:
        RazerNagaV3ProWireless(device_path=second_path, device_number=8, config=config,
                               persistence=persistence, testing=True, additional_interfaces=[],
                               additional_methods=[], unknown_serial_counter=counter)
    except KeyError as err:
        assert SERIAL_NOGUARD in str(err), str(err)
        outcome["path_error"] = True
    else:
        raise AssertionError("unguarded duplicate must fail at D-Bus registration")
    assert _read_mode(second_path) == bytes([0x03, 0x00])
    assert "dpi" not in os.listdir(second_path)
    outcome["second_mode_written"] = True
    got_serial, vidpid = _child_client_get(SERIAL_NOGUARD, buses)
    assert (got_serial, vidpid) == (SERIAL_NOGUARD, [0x1532, 0x00E7]), (got_serial, vidpid)
    assert len(collection) == 1
    outcome["incumbent_intact"] = True
    _release(first, NOGUARD_WIRED_ID, collection)
    owned.remove((first, NOGUARD_WIRED_ID, collection))
    gc.collect()
    return outcome


def _child_main():
    import threading
    import unittest.mock as mock
    import dbus.mainloop.glib
    from gi.repository import GLib
    dbus.mainloop.glib.threads_init()
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    loop = GLib.MainLoop()
    loop_thread = threading.Thread(target=loop.run, daemon=True)
    loop_thread.start()
    tmpbase = os.environ.get("TMPDIR", "")
    expected_root = os.environ.get(CHILD_ROOT_ENV, "")
    assert tmpbase, "parent must isolate TMPDIR"
    assert expected_root, "parent must pass the exact fixture root"
    root = tempfile.mkdtemp(prefix="naga-child-", dir=tmpbase)
    result = {
        "client_round_trip": False,
        "orders": [],
        "handover_pid_switched": False,
        "distinct_coexist": False,
        "glib_stopped": False,
        "noguard_path_error": False,
        "noguard_second_mode_written": False,
        "noguard_incumbent_intact": False,
    }
    owned = []
    buses = []
    try:
        assert "pyudev" not in sys.modules, "pyudev must stay unimported"
        assert tmpbase.startswith(expected_root), (tmpbase, expected_root)
        assert root.startswith(tmpbase), (root, tmpbase)
        assert root.startswith(expected_root), (root, expected_root)
        with mock.patch("openrazer_daemon.hardware.device_base.RazerDevice._init_battery_manager",
                        lambda self: setattr(self, "_battery_manager", None)), \
                mock.patch("openrazer_daemon.misc.mouse_monitor.MouseMonitor") as monitor_mock:
            from openrazer_daemon.device import DeviceCollection
            from openrazer_daemon.hardware.mouse import RazerNagaV3ProWired, RazerNagaV3ProWireless
            counter: dict = {}
            _child_order(RazerNagaV3ProWired, WIRED_ID, RazerNagaV3ProWireless, WIRELESS_ID,
                         DeviceCollection(), root, counter, buses, owned)
            result["orders"].append("wired-first")
            _child_order(RazerNagaV3ProWireless, WIRELESS_ID, RazerNagaV3ProWired, WIRED_ID,
                         DeviceCollection(), root, counter, buses, owned)
            result["orders"].append("wireless-first")
            result["handover_pid_switched"] = True
            result["client_round_trip"] = True
            collection3 = DeviceCollection()
            config, persistence = _child_config()
            wired = _child_build(RazerNagaV3ProWired, _child_make_sysfs(root, SERIAL_SHARED),
                                 0, config, persistence, counter, collection3)
            owned.append((wired, WIRED_ID, collection3))
            collection3.add(WIRED_ID, wired.serial, wired)
            other = _child_build(RazerNagaV3ProWireless, _child_make_sysfs(root, SERIAL_OTHER),
                                 1, config, persistence, counter, collection3)
            owned.append((other, WIRELESS_ID, collection3))
            collection3.add(WIRELESS_ID, other.serial, other)
            assert len(collection3) == 2
            got_a, pid_a = _child_client_get(SERIAL_SHARED, buses)
            got_b, pid_b = _child_client_get(SERIAL_OTHER, buses)
            assert (got_a, pid_a) == (SERIAL_SHARED, [0x1532, 0x00E7]), (got_a, pid_a)
            assert (got_b, pid_b) == (SERIAL_OTHER, [0x1532, 0x00E8]), (got_b, pid_b)
            result["distinct_coexist"] = True
            _release(wired, WIRED_ID, collection3)
            owned.remove((wired, WIRED_ID, collection3))
            _release(other, WIRELESS_ID, collection3)
            owned.remove((other, WIRELESS_ID, collection3))
            assert monitor_mock.call_count >= 1
            outcome = _child_noguard_control(root, counter, buses, owned)
            result["noguard_path_error"] = outcome["path_error"]
            result["noguard_second_mode_written"] = outcome["second_mode_written"]
            result["noguard_incumbent_intact"] = outcome["incumbent_intact"]
    finally:
        # Failure-path leftovers only: the success path releases everything
        # above, so nothing is swallowed on success.
        for dev, dev_id, collection in reversed(owned):
            try:
                dev.close()
            except Exception:
                pass
            try:
                dev.remove_from_connection()
            except Exception:
                pass
            try:
                collection.remove(dev_id)
            except Exception:
                pass
        owned.clear()
        try:
            loop.quit()
        except Exception:
            pass
        loop_thread.join(timeout=LOOP_JOIN_TIMEOUT)
        assert not loop_thread.is_alive(), "GLib server thread did not stop"
        result["glib_stopped"] = True
        for bus in buses:
            try:
                bus.close()
            except Exception:
                pass
        buses.clear()
        gc.collect()
        shutil.rmtree(root, ignore_errors=True)
    print(json.dumps(result))
    sys.stdout.flush()


if __name__ == "__main__":
    if CHILD_FLAG in sys.argv:
        _child_main()
    else:
        unittest.main()
