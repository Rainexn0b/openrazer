# SPDX-License-Identifier: GPL-2.0-or-later

import configparser
import os
import threading
import tempfile
import types
import unittest
import unittest.mock

import openrazer_daemon.hardware
from openrazer_daemon.daemon import RazerDaemon
from openrazer_daemon.device import DeviceCollection
from openrazer_daemon.hardware.device_base import DeviceNotReadyError, RazerDevice
from openrazer_daemon.hardware.mouse import RazerNagaV3ProWireless


class DriverModeDevice(RazerDevice):
    USB_VID = 0x1532
    USB_PID = 0x00E8
    DRIVER_MODE = True


class DaemonHotplugTest(unittest.TestCase):
    def setUp(self):
        self.daemon = object.__new__(RazerDaemon)
        self.daemon._razer_devices = DeviceCollection()
        self.daemon._config = unittest.mock.MagicMock()
        self.daemon._persistence = unittest.mock.MagicMock()
        self.daemon._test_dir = None
        self.daemon._unknown_serial_counter = {}
        self.daemon._udev_context = unittest.mock.MagicMock()
        self.daemon._pending_device_retries = {}
        self.daemon._device_lock = threading.RLock()
        self.daemon._udev_collection_lock = unittest.mock.MagicMock()
        self.daemon._stopping = False
        self.daemon.logger = unittest.mock.MagicMock()
        self.daemon.device_added = unittest.mock.MagicMock()
        self.daemon.device_removed = unittest.mock.MagicMock()

        self.device = types.SimpleNamespace(sys_name='0003:1532:00E8.0001', sys_path='/sys/devices/naga')
        self.device_class = unittest.mock.MagicMock()
        self.device_class.match.return_value = True
        self.daemon._device_classes = [self.device_class]

    @unittest.mock.patch('openrazer_daemon.daemon.grp.getgrgid', return_value=('plugdev',))
    @unittest.mock.patch('openrazer_daemon.daemon.os.stat')
    @unittest.mock.patch('openrazer_daemon.daemon.threading.Timer')
    def test_startup_device_timeout_schedules_hotplug_retry(self, timer_class, stat, _getgrgid):
        stat.return_value.st_gid = 1000
        self.daemon._udev_context.list_devices.return_value = [self.device]
        self.device_class.side_effect = DeviceNotReadyError(110, 'Connection timed out')

        self.daemon._load_devices()

        timer_class.assert_called_once_with(2, self.daemon._retry_add_device,
                                            args=(self.device, 1, [], unittest.mock.ANY))
        timer_class.return_value.start.assert_called_once_with()
        self.assertTrue(timer_class.return_value.daemon)
        pending_retry = self.daemon._pending_device_retries[self.device.sys_name]
        self.assertIs(pending_retry[1], timer_class.return_value)
        self.assertEqual(len(self.daemon._razer_devices), 0)

    @unittest.mock.patch('openrazer_daemon.daemon.time.sleep')
    @unittest.mock.patch('openrazer_daemon.daemon.threading.Timer')
    def test_add_device_retries_initial_timeout(self, timer_class, _sleep):
        self.device_class.side_effect = DeviceNotReadyError(110, 'Connection timed out')

        self.daemon._add_device(self.device)

        timer_class.assert_called_once_with(2, self.daemon._retry_add_device,
                                            args=(self.device, 1, None, unittest.mock.ANY))
        timer_class.return_value.start.assert_called_once_with()
        self.assertTrue(timer_class.return_value.daemon)
        pending_retry = self.daemon._pending_device_retries[self.device.sys_name]
        self.assertIs(pending_retry[1], timer_class.return_value)
        self.assertEqual(len(self.daemon._razer_devices), 0)
        self.daemon.device_added.assert_not_called()

    @unittest.mock.patch('openrazer_daemon.daemon.time.sleep')
    @unittest.mock.patch('openrazer_daemon.daemon.threading.Timer')
    def test_add_device_switches_to_slow_retry(self, timer_class, _sleep):
        self.device_class.side_effect = DeviceNotReadyError(110, 'Connection timed out')

        self.daemon._add_device(self.device, retry_count=15)

        timer_class.assert_called_once_with(30, self.daemon._retry_add_device,
                                            args=(self.device, 16, None, unittest.mock.ANY))
        timer_class.return_value.start.assert_called_once_with()
        self.assertTrue(timer_class.return_value.daemon)
        self.daemon.logger.warning.assert_called_once()
        self.assertEqual(len(self.daemon._razer_devices), 0)

    @unittest.mock.patch('openrazer_daemon.daemon.time.sleep')
    @unittest.mock.patch('openrazer_daemon.daemon.threading.Timer')
    def test_remove_device_cancels_slow_retry(self, timer_class, _sleep):
        self.device_class.side_effect = DeviceNotReadyError(110, 'Connection timed out')

        self.daemon._add_device(self.device, retry_count=15)
        self.daemon._remove_device(self.device)

        timer_class.return_value.cancel.assert_called_once_with()
        self.assertEqual(self.daemon._pending_device_retries, {})

    @unittest.mock.patch('openrazer_daemon.daemon.os.path.exists', return_value=True)
    @unittest.mock.patch('openrazer_daemon.daemon.time.sleep')
    @unittest.mock.patch('openrazer_daemon.daemon.threading.Timer')
    def test_slow_retry_adds_device_after_late_wake(self, timer_class, _sleep, _exists):
        razer_device = unittest.mock.MagicMock()
        razer_device.get_serial.return_value = 'SERIAL123'
        self.device_class.side_effect = [DeviceNotReadyError(110, 'Connection timed out'), razer_device]

        self.daemon._add_device(self.device, retry_count=15)
        retry_callback = timer_class.call_args.args[1]
        retry_args = timer_class.call_args.kwargs['args']
        retry_callback(*retry_args)

        self.assertEqual(self.daemon._razer_devices.serials(), ['SERIAL123'])
        self.assertEqual(self.daemon._pending_device_retries, {})
        self.daemon.device_added.assert_called_once_with()

    @unittest.mock.patch('openrazer_daemon.daemon.time.sleep')
    @unittest.mock.patch('openrazer_daemon.daemon.threading.Timer')
    def test_add_device_does_not_retry_unrelated_io_error(self, timer_class, _sleep):
        self.device_class.side_effect = OSError(5, 'Input/output error')

        with self.assertRaises(OSError):
            self.daemon._add_device(self.device)

        timer_class.assert_not_called()

    @unittest.mock.patch('openrazer_daemon.daemon.os.path.exists', return_value=True)
    @unittest.mock.patch('openrazer_daemon.daemon.time.sleep')
    @unittest.mock.patch('openrazer_daemon.daemon.threading.Timer')
    def test_add_device_succeeds_when_retry_fires(self, timer_class, _sleep, _exists):
        razer_device = unittest.mock.MagicMock()
        razer_device.get_serial.return_value = 'SERIAL123'
        self.device_class.side_effect = [DeviceNotReadyError(110, 'Connection timed out'), razer_device]

        self.daemon._add_device(self.device)
        retry_callback = timer_class.call_args.args[1]
        retry_args = timer_class.call_args.kwargs['args']
        retry_callback(*retry_args)

        self.assertEqual(self.daemon._razer_devices.serials(), ['SERIAL123'])
        self.assertEqual(self.daemon._pending_device_retries, {})
        self.daemon.device_added.assert_called_once_with()
        razer_device.register_parent.assert_called_once()

    def test_remove_device_cancels_pending_retry(self):
        retry = unittest.mock.MagicMock()
        self.daemon._pending_device_retries[self.device.sys_name] = (object(), retry)

        self.daemon._remove_device(self.device)

        retry.cancel.assert_called_once_with()
        self.assertEqual(self.daemon._pending_device_retries, {})

    def test_remove_device_is_ignored_during_shutdown(self):
        retry = unittest.mock.MagicMock()
        self.daemon._pending_device_retries[self.device.sys_name] = (object(), retry)
        self.daemon._stopping = True

        self.daemon._remove_device(self.device)

        retry.cancel.assert_not_called()
        self.assertIn(self.device.sys_name, self.daemon._pending_device_retries)

    def test_stale_callback_does_not_consume_replacement_retry(self):
        current_token = object()
        current_retry = unittest.mock.MagicMock()
        self.daemon._pending_device_retries[self.device.sys_name] = (current_token, current_retry)

        self.daemon._retry_add_device(self.device, 1, None, object())

        self.assertEqual(self.daemon._pending_device_retries,
                         {self.device.sys_name: (current_token, current_retry)})
        self.device_class.assert_not_called()

    @unittest.mock.patch('openrazer_daemon.daemon.time.sleep')
    @unittest.mock.patch('openrazer_daemon.daemon.os.path.exists', return_value=True)
    def test_collector_resets_state_after_error(self, _exists, _sleep):
        self.daemon._collecting_udev = True
        self.daemon._collecting_udev_devices = [self.device]
        self.daemon._add_device = unittest.mock.MagicMock(side_effect=RuntimeError('test error'))

        with self.assertRaisesRegex(RuntimeError, 'test error'):
            self.daemon._collecting_udev_method(self.device)

        self.assertFalse(self.daemon._collecting_udev)

    @unittest.mock.patch('openrazer_daemon.daemon.time.sleep')
    @unittest.mock.patch('openrazer_daemon.daemon.os.path.exists', return_value=True)
    def test_collector_preserves_additional_interfaces(self, _exists, _sleep):
        other_device = types.SimpleNamespace(sys_name='0003:1532:00E8.0002', sys_path='/sys/devices/naga2')
        self.daemon._collecting_udev = True
        self.daemon._collecting_udev_devices = [self.device, other_device]
        self.daemon._add_device = unittest.mock.MagicMock()

        self.daemon._collecting_udev_method(self.device)

        self.daemon._add_device.assert_has_calls([
            unittest.mock.call(other_device, additional_interfaces=[self.device.sys_path]),
            unittest.mock.call(self.device, additional_interfaces=[other_device.sys_path]),
        ])

    def test_quit_continues_after_resume_timeout(self):
        self.daemon.resume_devices = unittest.mock.MagicMock(
            side_effect=TimeoutError(110, 'Connection timed out'))
        self.daemon._main_loop = unittest.mock.MagicMock()
        self.daemon._udev_observer = unittest.mock.MagicMock()
        self.daemon.write_persistence = unittest.mock.MagicMock()
        self.daemon._persistence_file = None

        self.daemon.quit(15)

        self.daemon.logger.warning.assert_called_once()
        self.daemon._main_loop.quit.assert_called_once_with()
        self.daemon._udev_observer.send_stop.assert_called_once_with()
        self.daemon.write_persistence.assert_called_once_with(None)

    def test_quit_continues_after_device_close_timeout(self):
        failed_device = types.SimpleNamespace(dbus=unittest.mock.MagicMock())
        failed_device.dbus.close.side_effect = TimeoutError(110, 'Connection timed out')
        closed_device = types.SimpleNamespace(dbus=unittest.mock.MagicMock())
        self.daemon._razer_devices = [failed_device, closed_device]
        self.daemon.resume_devices = unittest.mock.MagicMock()
        self.daemon._main_loop = unittest.mock.MagicMock()
        self.daemon._udev_observer = unittest.mock.MagicMock()
        self.daemon.write_persistence = unittest.mock.MagicMock()
        self.daemon._persistence_file = None

        self.daemon.quit(15)

        failed_device.dbus.close.assert_called_once_with()
        closed_device.dbus.close.assert_called_once_with()
        self.daemon.logger.warning.assert_called_once()
        self.daemon.write_persistence.assert_called_once_with(None)


class DeviceConstructionTest(unittest.TestCase):
    def test_hardware_discovery_ignores_readiness_exception(self):
        self.assertNotIn(DeviceNotReadyError, openrazer_daemon.hardware.get_device_classes())

    @unittest.mock.patch('openrazer_daemon.hardware.mouse.mouse_monitor.is_device_serial_ready', return_value=False)
    def test_wireless_naga_requires_valid_serial(self, is_device_serial_ready):
        device = object.__new__(RazerNagaV3ProWireless)

        with self.assertRaisesRegex(DeviceNotReadyError, 'Device serial is not ready'):
            RazerNagaV3ProWireless.__init__(device, device_path='/sys/devices/naga')

        self.assertTrue(device._is_closed)
        is_device_serial_ready.assert_called_once_with('/sys/devices/naga')

    @unittest.mock.patch('openrazer_daemon.hardware.device_base.effect_sync.EffectSync')
    def test_driver_mode_timeout_precedes_background_services(self, effect_sync):
        config = configparser.ConfigParser()
        persistence = configparser.ConfigParser()

        with tempfile.TemporaryDirectory() as device_path:
            with open(os.path.join(device_path, 'device_serial'), 'w') as serial_file:
                serial_file.write('SERIAL123')

            device = object.__new__(DriverModeDevice)
            with unittest.mock.patch.object(DriverModeDevice, 'set_device_mode',
                                            side_effect=TimeoutError(110, 'Connection timed out')):
                with self.assertRaises(DeviceNotReadyError):
                    DriverModeDevice.__init__(device, device_path, 0, config, persistence,
                                              True, None, [], {})

            self.assertTrue(device._is_closed)
            effect_sync.assert_not_called()
            device.close()

    def test_close_cleans_up_after_hardware_timeouts(self):
        device = object.__new__(DriverModeDevice)
        device._is_closed = False
        device.METHODS = ['get_dpi_xy']
        device.logger = unittest.mock.MagicMock()
        device.getDPI = unittest.mock.MagicMock(
            side_effect=TimeoutError(110, 'Connection timed out'))
        device.set_device_mode = unittest.mock.MagicMock(
            side_effect=TimeoutError(110, 'Connection timed out'))
        device._close = unittest.mock.MagicMock()

        device.close()
        device.close()

        device.getDPI.assert_called_once_with()
        device.set_device_mode.assert_called_once_with(0x00, 0x00)
        device._close.assert_called_once_with()
        self.assertTrue(device._is_closed)
        self.assertEqual(device.logger.warning.call_count, 2)


if __name__ == '__main__':
    unittest.main()
