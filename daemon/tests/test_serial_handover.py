# SPDX-License-Identifier: GPL-2.0-or-later
"""Handover coverage through the production serial guard."""

import configparser
import errno
import os
import tempfile
import threading
import types
import unittest
import unittest.mock

from openrazer_daemon.daemon import RazerDaemon
from openrazer_daemon.device import DeviceCollection
from openrazer_daemon.hardware.device_base import DeviceNotReadyError, RazerDevice
from openrazer_daemon.misc import serial_identity


FAKE_SERIAL = "SN100001"
OTHER_SERIAL = "SN100002"
WIRED_ID = "0003:1532:00E7.0001"
WIRELESS_ID = "0003:1532:00E8.0001"


class HandoverWired(RazerDevice):
    USB_VID = 0x1532
    USB_PID = 0x00E7
    DRIVER_MODE = True
    METHODS: list[str] = ["get_battery"]


class HandoverWireless(RazerDevice):
    USB_VID = 0x1532
    USB_PID = 0x00E8
    DRIVER_MODE = True
    METHODS: list[str] = ["get_battery"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._handover_monitor_started = True


class OtherDevice(RazerDevice):
    USB_VID = 0x1532
    USB_PID = 0x00A7
    DRIVER_MODE = False
    METHODS: list[str] = []


def make_daemon():
    daemon = object.__new__(RazerDaemon)
    daemon._razer_devices = DeviceCollection()
    config = configparser.ConfigParser()
    config["Startup"] = {"restore_persistence": "False", "persistence_dual_boot_quirk": "False"}
    daemon._config = config
    daemon._persistence = configparser.ConfigParser()
    daemon._test_dir = None
    daemon._unknown_serial_counter = {}
    daemon._udev_context = unittest.mock.MagicMock()
    daemon._pending_device_retries = {}
    daemon._device_lock = threading.RLock()
    daemon._stopping = False
    daemon.logger = unittest.mock.MagicMock()
    daemon.device_added = unittest.mock.MagicMock()
    daemon.device_removed = unittest.mock.MagicMock()
    daemon.write_persistence = unittest.mock.MagicMock()
    daemon._persistence_file = None
    return daemon


class GuardFixture(unittest.TestCase):
    def setUp(self):
        self.tmpdirs = []
        self._real_exists = os.path.exists
        self._real_listdir = os.listdir

        def _exists(path, *args, **kwargs):
            if os.fspath(path).rstrip("/") == "/dev/input/by-id":
                return False
            return self._real_exists(path, *args, **kwargs)

        def _listdir(path, *args, **kwargs):
            if os.fspath(path).rstrip("/") == "/dev/input/by-id":
                return []
            return self._real_listdir(path, *args, **kwargs)

        self.patches = [
            unittest.mock.patch(
                "openrazer_daemon.hardware.device_base.DBusService.__init__", return_value=None
            ),
            unittest.mock.patch.object(RazerDevice, "add_dbus_method", return_value=None),
            unittest.mock.patch.object(
                RazerDevice, "remove_from_connection", return_value=None, create=True
            ),
            unittest.mock.patch.object(RazerDevice, "set_device_mode"),
            unittest.mock.patch(
                "openrazer_daemon.hardware.device_base.effect_sync.EffectSync"
            ),
            unittest.mock.patch.object(RazerDevice, "_init_battery_manager"),
            unittest.mock.patch("os.path.exists", side_effect=_exists),
            unittest.mock.patch("os.listdir", side_effect=_listdir),
        ]
        self.mocks = [p.start() for p in self.patches]
        (
            self.dbus_init,
            _,
            self.remove_mock,
            self.mode_mock,
            self.sync_mock,
            self.battery_mock,
            _,
            _,
        ) = self.mocks
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def tearDown(self):
        for tmp in self.tmpdirs:
            for name in os.listdir(tmp):
                os.unlink(os.path.join(tmp, name))
            os.rmdir(tmp)

    def device_dir(self, serial):
        tmp = tempfile.mkdtemp()
        self.tmpdirs.append(tmp)
        with open(os.path.join(tmp, "device_serial"), "w") as handle:
            handle.write(serial)
        with open(os.path.join(tmp, "device_type"), "w") as handle:
            handle.write("mouse")
        return tmp

    def udev(self, sys_name, sys_path):
        return types.SimpleNamespace(sys_name=sys_name, sys_path=sys_path)

    def config(self):
        config = configparser.ConfigParser()
        config["Startup"] = {
            "restore_persistence": "False",
            "persistence_dual_boot_quirk": "False",
        }
        return config

    def close_daemon(self, daemon):
        for dev in list(daemon._razer_devices.devices):
            dev.dbus.close()


class ConstructionGuardTest(GuardFixture):
    def test_duplicate_before_side_effects(self):
        first_dir = self.device_dir(FAKE_SERIAL)
        second_dir = self.device_dir(FAKE_SERIAL)
        collection = DeviceCollection()
        kwargs = serial_identity.serial_guard_kwargs(HandoverWired, collection)
        first = HandoverWired(
            first_dir, 0, self.config(), configparser.ConfigParser(), True, [], [], {}, **kwargs
        )
        collection.add(WIRED_ID, first.serial, first)
        dbus_calls = self.dbus_init.call_count
        mode_calls = self.mode_mock.call_count
        sync_calls = self.sync_mock.call_count
        battery_calls = self.battery_mock.call_count
        kwargs2 = serial_identity.serial_guard_kwargs(HandoverWireless, collection)
        with self.assertRaises(DeviceNotReadyError) as ctx:
            HandoverWireless(
                second_dir, 1, self.config(), configparser.ConfigParser(), True, [], [], {},
                **kwargs2,
            )
        self.assertEqual(ctx.exception.errno, errno.EBUSY)
        self.assertEqual(self.dbus_init.call_count, dbus_calls)
        self.assertEqual(self.mode_mock.call_count, mode_calls)
        self.assertEqual(self.sync_mock.call_count, sync_calls)
        self.assertEqual(self.battery_mock.call_count, battery_calls)
        self.assertIs(collection[FAKE_SERIAL].dbus, first)
        self.assertFalse(hasattr(collection[FAKE_SERIAL].dbus, "_handover_monitor_started"))
        first.close()

    def test_untrustworthy_rejected(self):
        bad_dir = self.device_dir("!!not-valid!!")
        collection = DeviceCollection()
        kwargs = serial_identity.serial_guard_kwargs(HandoverWired, collection)
        with self.assertRaises(DeviceNotReadyError) as ctx:
            HandoverWired(
                bad_dir, 0, self.config(), configparser.ConfigParser(), True, [], [], {}, **kwargs
            )
        self.assertEqual(ctx.exception.errno, errno.EAGAIN)
        self.assertEqual(self.dbus_init.call_count, 0)
        self.assertEqual(len(collection), 0)


class DaemonHandoverTest(GuardFixture):
    def setUp(self):
        super().setUp()
        self.daemon = make_daemon()

    def tearDown(self):
        self.close_daemon(self.daemon)
        super().tearDown()

    def _add(self, udev):
        with unittest.mock.patch("openrazer_daemon.daemon.time.sleep"), unittest.mock.patch(
            "openrazer_daemon.daemon.threading.Timer"
        ) as timer_class:
            self.daemon._add_device(udev)
            return timer_class

    def _pair(self, serial=FAKE_SERIAL):
        wired_path = self.device_dir(serial)
        wireless_path = self.device_dir(serial)
        wired = self.udev(WIRED_ID, wired_path)
        wireless = self.udev(WIRELESS_ID, wireless_path)
        self.daemon._device_classes = [HandoverWired, HandoverWireless]
        return wired, wireless

    def test_hotplug_wired_first(self):
        wired, wireless = self._pair()
        timer = self._add(wired)
        timer.assert_not_called()
        self.assertEqual(len(self.daemon._razer_devices), 1)
        dbus_calls = self.dbus_init.call_count
        timer2 = self._add(wireless)
        timer2.assert_called_once()
        self.assertEqual(len(self.daemon._razer_devices), 1)
        self.assertEqual(self.dbus_init.call_count, dbus_calls)

    def test_hotplug_wireless_first(self):
        wireless_path = self.device_dir(FAKE_SERIAL)
        wired_path = self.device_dir(FAKE_SERIAL)
        wireless = self.udev(WIRELESS_ID, wireless_path)
        wired = self.udev(WIRED_ID, wired_path)
        self.daemon._device_classes = [HandoverWired, HandoverWireless]
        self._add(wireless)
        self.assertEqual(len(self.daemon._razer_devices), 1)
        dbus_calls = self.dbus_init.call_count
        timer = self._add(wired)
        timer.assert_called_once()
        self.assertEqual(self.dbus_init.call_count, dbus_calls)

    def test_startup_both_orders(self):
        for first_id, second_id in ((WIRED_ID, WIRELESS_ID), (WIRELESS_ID, WIRED_ID)):
            with self.subTest(first=first_id):
                daemon = make_daemon()
                daemon._device_classes = [HandoverWired, HandoverWireless]
                first_path = self.device_dir(FAKE_SERIAL)
                second_path = self.device_dir(FAKE_SERIAL)
                first = self.udev(first_id, first_path)
                second = self.udev(second_id, second_path)
                daemon._udev_context.list_devices.return_value = [first, second]
                before = self.dbus_init.call_count
                with unittest.mock.patch("openrazer_daemon.daemon.time.sleep"), unittest.mock.patch(
                    "openrazer_daemon.daemon.threading.Timer"
                ) as timer_class:
                    daemon._load_devices()
                    timer_class.assert_called_once()
                self.assertEqual(len(daemon._razer_devices), 1)
                self.assertEqual(self.dbus_init.call_count, before + 1)
                self.close_daemon(daemon)

    def test_retry_handover_after_remove(self):
        wired, wireless = self._pair()
        self.daemon._device_classes = [HandoverWired, HandoverWireless]
        with unittest.mock.patch("openrazer_daemon.daemon.time.sleep"), unittest.mock.patch(
            "openrazer_daemon.daemon.threading.Timer"
        ) as timer_class:
            self.daemon._add_device(wired)
            self.assertEqual(self.daemon.device_added.call_count, 1)
            self.daemon._add_device(wireless)
            retry_cb = timer_class.call_args.args[1]
            retry_args = timer_class.call_args.kwargs["args"]
            remove_calls = self.remove_mock.call_count
            added_before = self.daemon.device_added.call_count
            with unittest.mock.patch("openrazer_daemon.daemon.os.path.exists", return_value=True):
                self.daemon._remove_device(wired)
                self.assertEqual(len(self.daemon._razer_devices), 0)
                self.assertEqual(self.remove_mock.call_count, remove_calls + 1)
                self.assertEqual(self.daemon.device_added.call_count, added_before)
                retry_cb(*retry_args)
            self.assertEqual(self.daemon._razer_devices.serials(), [FAKE_SERIAL])
            self.assertEqual(self.daemon.device_added.call_count, added_before + 1)

    def test_waiting_remove_cancels_retry(self):
        wired, wireless = self._pair()
        self.daemon._device_classes = [HandoverWired, HandoverWireless]
        with unittest.mock.patch("openrazer_daemon.daemon.time.sleep"), unittest.mock.patch(
            "openrazer_daemon.daemon.threading.Timer"
        ) as timer_class:
            self.daemon._add_device(wired)
            self.daemon._add_device(wireless)
            timer_class.assert_called_once()
            pending_timer = timer_class.return_value
            retry_cb = timer_class.call_args.args[1]
            retry_args = timer_class.call_args.kwargs["args"]
            self.daemon._remove_device(wireless)
            pending_timer.cancel.assert_called_once_with()
            self.assertEqual(self.daemon._pending_device_retries, {})
            with unittest.mock.patch("openrazer_daemon.daemon.os.path.exists", return_value=True):
                retry_cb(*retry_args)
            self.assertEqual(len(self.daemon._razer_devices), 1)

    def test_stale_token_ignored_and_stopping_blocks(self):
        wired, wireless = self._pair()
        self.daemon._device_classes = [HandoverWired, HandoverWireless]
        with unittest.mock.patch("openrazer_daemon.daemon.time.sleep"), unittest.mock.patch(
            "openrazer_daemon.daemon.threading.Timer"
        ):
            self.daemon._add_device(wired)
            self.daemon._add_device(wireless)
            pending_before = self.daemon._pending_device_retries[WIRELESS_ID]
            token = pending_before[0]
            before = self.dbus_init.call_count
            with unittest.mock.patch("openrazer_daemon.daemon.os.path.exists", return_value=True):
                self.daemon._retry_add_device(wireless, 1, None, object())
            self.assertIs(self.daemon._pending_device_retries[WIRELESS_ID], pending_before)
            self.assertEqual(self.dbus_init.call_count, before)
            self.daemon._stopping = True
            with unittest.mock.patch("openrazer_daemon.daemon.os.path.exists", return_value=True):
                self.daemon._retry_add_device(wireless, 1, None, token)
            self.assertEqual(len(self.daemon._razer_devices), 1)
            self.assertEqual(self.dbus_init.call_count, before)

    def test_close_failure_keeps_incumbent(self):
        wired, wireless = self._pair()
        self.daemon._device_classes = [HandoverWired, HandoverWireless]
        with unittest.mock.patch("openrazer_daemon.daemon.time.sleep"), unittest.mock.patch(
            "openrazer_daemon.daemon.threading.Timer"
        ):
            self.daemon._add_device(wired)
            self.daemon._add_device(wireless)
            before = self.dbus_init.call_count
            incumbent = self.daemon._razer_devices[FAKE_SERIAL]
            with unittest.mock.patch.object(
                incumbent.dbus, "close", side_effect=OSError(5, "nope")
            ):
                with self.assertRaises(OSError):
                    self.daemon._remove_device(wired)
            self.assertEqual(len(self.daemon._razer_devices), 1)
            with unittest.mock.patch("openrazer_daemon.daemon.os.path.exists", return_value=True):
                token = self.daemon._pending_device_retries[WIRELESS_ID][0]
                self.daemon._retry_add_device(wireless, 1, None, token)
            self.assertEqual(self.dbus_init.call_count, before)

    def test_unpublish_failure_keeps_incumbent(self):
        wired, wireless = self._pair()
        self.daemon._device_classes = [HandoverWired, HandoverWireless]
        with unittest.mock.patch("openrazer_daemon.daemon.time.sleep"), unittest.mock.patch(
            "openrazer_daemon.daemon.threading.Timer"
        ):
            self.daemon._add_device(wired)
            self.daemon._add_device(wireless)
            before = self.dbus_init.call_count
            incumbent = self.daemon._razer_devices[FAKE_SERIAL]
            with unittest.mock.patch.object(
                incumbent.dbus, "remove_from_connection", side_effect=OSError(5, "nope")
            ):
                with self.assertRaises(OSError):
                    self.daemon._remove_device(wired)
            self.assertEqual(len(self.daemon._razer_devices), 1)
            with unittest.mock.patch("openrazer_daemon.daemon.os.path.exists", return_value=True):
                token = self.daemon._pending_device_retries[WIRELESS_ID][0]
                self.daemon._retry_add_device(wireless, 1, None, token)
            self.assertEqual(self.dbus_init.call_count, before)

    def test_distinct_and_other_contract(self):
        wired_path = self.device_dir(FAKE_SERIAL)
        wireless_path = self.device_dir(OTHER_SERIAL)
        wired = self.udev(WIRED_ID, wired_path)
        wireless = self.udev(WIRELESS_ID, wireless_path)
        self.daemon._device_classes = [HandoverWired, HandoverWireless]
        with unittest.mock.patch("openrazer_daemon.daemon.time.sleep"), unittest.mock.patch(
            "openrazer_daemon.daemon.threading.Timer"
        ) as timer_class:
            self.daemon._add_device(wired)
            self.daemon._add_device(wireless)
            timer_class.assert_not_called()
        self.assertEqual(len(self.daemon._razer_devices), 2)
        other_daemon = make_daemon()
        other_daemon._device_classes = [OtherDevice]
        self.assertEqual(serial_identity.serial_guard_kwargs(OtherDevice, other_daemon._razer_devices), {})


if __name__ == "__main__":
    unittest.main()
