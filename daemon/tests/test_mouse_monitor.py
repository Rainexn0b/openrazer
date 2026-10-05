# SPDX-License-Identifier: GPL-2.0-or-later

import configparser
import threading
import unittest
from unittest.mock import Mock, call, mock_open, patch

from openrazer_daemon.hardware.device_base import DeviceNotReadyError, RazerDevice
from openrazer_daemon.hardware.mouse import RazerNagaV3ProWireless
from openrazer_daemon.misc import mouse_monitor
from openrazer_daemon.misc.mouse_monitor import is_device_serial_ready


class FakeMouseTest(unittest.TestCase):
    def setUp(self):
        self.parent = Mock(DRIVER_MODE=True, _device_path='/fake/naga',
                           _device_mode_lock=threading.RLock())
        self.parent.get_driver_path.side_effect = lambda name: '/fake/naga/' + name
        self.monitor = mouse_monitor.MouseMonitor(0, self.parent)
        self.monitor._logger = Mock()
        self.activity = self.start_patch(patch.object(mouse_monitor, '_read_last_activity', return_value=0))
        self.ready = self.start_patch(patch.object(mouse_monitor, 'is_device_serial_ready', return_value=True))
        self.clock = self.start_patch(patch.object(mouse_monitor.time, 'monotonic', return_value=0.0))
        self.open = self.start_patch(patch('builtins.open', mock_open(read_data='60')))
        self.thread_errors = []

    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def run_polls(self, *timestamps):
        ticks = iter(timestamps)

        def wait(interval):
            self.assertEqual(interval, mouse_monitor.POLL_INTERVAL)
            now = next(ticks, None)
            if now is None:
                return True
            self.clock.return_value = now
            return False

        with patch.object(self.monitor._shutdown, 'wait', side_effect=wait):
            self.monitor.run()

    def start_worker(self, action):
        def run():
            try:
                action()
            except BaseException as error:
                self.thread_errors.append(error)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 1)
        return worker

    def finish_workers(self, *workers):
        for worker in workers:
            worker.join(1)
            self.assertFalse(worker.is_alive(), 'Fake I/O worker did not finish')
        self.assertEqual(self.thread_errors, [])


class MouseMonitorTest(FakeMouseTest):
    def test_recovers_without_observing_idle_transition(self):
        self.activity.return_value = mouse_monitor.ACTIVE_WINDOW
        self.parent.get_device_mode.side_effect = ['0:0', '3:0']

        self.assertFalse(self.monitor._idle)
        self.run_polls(0)

        self.parent.set_device_mode.assert_called_once_with(3, 0)
        self.assertEqual(self.parent.get_device_mode.call_count, 2)
        self.assertFalse(self.monitor._idle)
        self.monitor._logger.info.assert_called_once_with(
            'Verified "driver mode" restoration during activity')

    def test_later_periodic_check_repairs_active_mode_drift(self):
        self.parent.get_device_mode.side_effect = ['3:0', '0:0', '3:0']

        self.run_polls(0, 1.999)
        self.parent.set_device_mode.assert_not_called()
        self.assertEqual(self.parent.get_device_mode.call_count, 1)
        self.run_polls(2)

        self.parent.set_device_mode.assert_called_once_with(3, 0)
        self.assertEqual(self.parent.get_device_mode.call_count, 3)
        self.assertFalse(self.monitor._idle)

    def test_dropped_write_retries_and_logs_info_only_after_verified_readback(self):
        self.parent.get_device_mode.side_effect = ['0:0', '0:0', '0:0', '3:0']

        self.run_polls(0)
        self.parent.set_device_mode.assert_called_once_with(3, 0)
        self.monitor._logger.info.assert_not_called()
        self.monitor._logger.debug.assert_called_once()
        self.run_polls(1.999)
        self.assertEqual(self.parent.set_device_mode.call_count, 1)
        self.monitor._logger.info.assert_not_called()
        self.run_polls(2)

        self.assertEqual(self.parent.set_device_mode.call_args_list, [call(3, 0), call(3, 0)])
        self.assertEqual(self.parent.get_device_mode.call_count, 4)
        self.monitor._logger.info.assert_called_once_with(
            'Verified "driver mode" restoration during activity')

    def test_already_in_driver_mode_does_not_write_or_log_restoration(self):
        self.parent.get_device_mode.return_value = '3:0'

        self.run_polls(0, 2)

        self.assertEqual(self.parent.get_device_mode.call_count, 2)
        self.parent.set_device_mode.assert_not_called()
        self.monitor._logger.info.assert_not_called()

    def test_empty_serial_skips_mode_usb_and_retries_after_two_seconds(self):
        self.ready.side_effect = is_device_serial_ready
        self.open.return_value.read.side_effect = ['', 'SERIAL123', '60']
        self.parent.get_device_mode.side_effect = ['0:0', '3:0']

        self.run_polls(0, 0.2, 1.999)
        self.ready.assert_called_once_with('/fake/naga')
        self.open.assert_called_once_with('/fake/naga/device_serial', 'r')
        self.parent.get_device_mode.assert_not_called()
        self.parent.set_device_mode.assert_not_called()
        self.monitor._logger.info.assert_not_called()
        self.run_polls(2)

        self.assertEqual(self.ready.call_count, 2)
        self.assertEqual(self.open.call_args_list, [
            call('/fake/naga/device_serial', 'r'),
            call('/fake/naga/device_serial', 'r'),
            call('/fake/naga/device_idle_time', 'r'),
        ])
        self.parent.set_device_mode.assert_called_once_with(3, 0)
        self.monitor._logger.info.assert_called_once()

    def test_io_and_malformed_mode_failures_survive_and_retry(self):
        cases = [('read', OSError), ('read', IndexError), ('read', ValueError),
                 ('write', OSError), ('readback', OSError),
                 ('readback', IndexError), ('readback', ValueError)]
        for phase, error_type in cases:
            with self.subTest(phase=phase, error=error_type.__name__):
                self.monitor._next_mode_check = 0
                self.monitor._logger.reset_mock()
                self.parent.get_device_mode.reset_mock(side_effect=True)
                self.parent.set_device_mode.reset_mock(side_effect=True)
                error = error_type('receiver not ready')
                first_reads = {'read': [error], 'write': ['0:0'], 'readback': ['0:0', error]}[phase]
                self.parent.get_device_mode.side_effect = first_reads + ['0:0', '3:0']
                if phase == 'write':
                    self.parent.set_device_mode.side_effect = [error, None]

                def verified(_message):
                    self.assertEqual(self.parent.get_device_mode.call_count, len(first_reads) + 2)
                    self.assertEqual(self.clock.return_value, 2)

                self.monitor._logger.info.side_effect = verified
                self.run_polls(0, 1.999, 2)
                self.monitor._logger.debug.assert_called_once()

                expected_writes = 1 if phase == 'read' else 2
                self.assertEqual(self.parent.set_device_mode.call_args_list, [call(3, 0)] * expected_writes)
                self.monitor._logger.info.assert_called_once_with(
                    'Verified "driver mode" restoration during activity')

    def test_no_activity_or_inactivity_does_not_issue_mode_usb(self):
        self.monitor._idle_time = 60
        self.activity.side_effect = [mouse_monitor.NO_ACTIVITY, 5001, 60001, 80000]

        self.run_polls(0, 0.2, 0.4, 0.6)

        self.ready.assert_not_called()
        self.open.assert_not_called()
        self.parent.get_device_mode.assert_not_called()
        self.parent.set_device_mode.assert_not_called()
        self.assertTrue(self.monitor._idle)

    def test_activity_is_rechecked_after_waiting_for_lock(self):
        for stale_sample in (mouse_monitor.NO_ACTIVITY, mouse_monitor.ACTIVE_WINDOW + 1):
            with self.subTest(sample=stale_sample):
                sampled = threading.Event()
                samples = iter((0, stale_sample))

                def read_activity(_path):
                    sampled.set()
                    return next(samples)

                self.activity.reset_mock()
                self.activity.side_effect = read_activity
                self.monitor._next_mode_check = 0
                with self.parent._device_mode_lock:
                    worker = self.start_worker(lambda: self.run_polls(0))
                    self.assertTrue(sampled.wait(1), 'Monitor did not sample activity')
                self.finish_workers(worker)

                self.assertEqual(self.activity.call_args_list, [call('/fake/naga/device_last_activity')] * 2)
                self.ready.assert_not_called()
                self.open.assert_not_called()
                self.parent.get_device_mode.assert_not_called()
                self.parent.set_device_mode.assert_not_called()

    def test_idle_timeout_cache_retains_positive_value_and_updates(self):
        cases = [(0, '60', 60), (30, '0', 60), (60, OSError('busy'), 60),
                 (90, 'malformed', 60), (120, '90', 90)]
        for now, response, expected in cases:
            with self.subTest(response=response):
                self.clock.return_value = now
                self.open.side_effect = response if isinstance(response, OSError) else None
                self.open.return_value.read.return_value = response
                self.assertEqual(self.monitor._get_idle_time(), expected)
                read_count = self.open.call_count
                self.clock.return_value = now + mouse_monitor.IDLE_TIME_REFRESH - 0.001
                self.assertEqual(self.monitor._get_idle_time(), expected)
                self.assertEqual(self.open.call_count, read_count)
        self.assertEqual(self.open.call_args_list, [call('/fake/naga/device_idle_time', 'r')] * len(cases))


class NagaMouseMonitorTest(FakeMouseTest):
    def setUp(self):
        super().setUp()
        fake = self.parent
        self.parent = object.__new__(RazerNagaV3ProWireless)
        self.parent._is_closed = True  # Keep destructor cleanup hardware-free.
        self.parent.DRIVER_MODE = True
        self.parent._device_mode_lock = fake._device_mode_lock
        self.parent._device_path = fake._device_path
        self.parent.get_driver_path = fake.get_driver_path
        self.parent.get_device_mode = fake.get_device_mode
        self.parent.logger = Mock()
        self.parent.restore_brightness = Mock()
        self.parent._resume_device = Mock()
        self.parent.disable_notify = False
        self.parent.disable_persistence = False
        self.parent._mouse_monitor = self.monitor
        self.monitor._parent = self.parent
        self.write = self.start_patch(patch.object(RazerDevice, 'set_device_mode'))
        self.addCleanup(setattr, self.parent, '_is_closed', True)

    def construct(self, config):
        self.parent.__init__(device_path='/fake/naga', device_number=0, config=config,
                             persistence=configparser.ConfigParser(), testing=True,
                             additional_interfaces=None, additional_methods=[], unknown_serial_counter={})

    def test_explicit_firmware_mode_disables_monitor_even_if_write_fails(self):
        for error in (None, OSError('busy')):
            with self.subTest(error=error):
                self.parent.DRIVER_MODE = True
                self.write.reset_mock()
                self.write.side_effect = error
                if error is None:
                    self.parent.set_device_mode(0, 0)
                else:
                    with self.assertRaises(OSError):
                        self.parent.set_device_mode(0, 0)
                self.assertFalse(self.parent.DRIVER_MODE)
                self.monitor._next_mode_check = 0
                self.run_polls(0, 2)

                self.write.assert_called_once_with(0, 0)
                self.ready.assert_not_called()
                self.open.assert_not_called()
                self.parent.get_device_mode.assert_not_called()

    def test_explicit_driver_mode_reenables_recovery_despite_config_false(self):
        config = configparser.ConfigParser()
        config.read_dict({'Device:SERIAL123': {'driver_mode': 'false'}})

        def base_init(device, *_args, **kwargs):
            device.config = kwargs['config']
            device.DRIVER_MODE = device.config.getboolean('Device:SERIAL123', 'driver_mode')
            device._is_closed = False

        self.parent._close = Mock()
        with patch.object(RazerDevice, '__init__', autospec=True, side_effect=base_init), \
                patch.object(mouse_monitor.MouseMonitor, 'start', autospec=True) as start:
            self.construct(config)
        self.monitor = self.parent._mouse_monitor
        self.monitor._logger = Mock()
        start.assert_called_once_with(self.monitor)
        self.assertFalse(self.parent.DRIVER_MODE)
        self.write.assert_not_called()
        self.parent.get_device_mode.side_effect = ['0:0', '3:0']

        self.parent.set_device_mode(3, 0)
        self.run_polls(0)

        self.assertTrue(self.parent.DRIVER_MODE)
        self.assertFalse(self.parent.config.getboolean('Device:SERIAL123', 'driver_mode'))
        self.assertEqual(self.write.call_args_list, [call(3, 0), call(3, 0)])
        self.monitor._logger.info.assert_called_once()
        self.parent.close()

    def test_resume_respects_explicit_firmware_mode(self):
        self.parent.set_device_mode(0, 0)

        self.parent.resume_device()

        self.write.assert_called_once_with(0, 0)
        self.assertFalse(self.parent.DRIVER_MODE)
        self.parent.restore_brightness.assert_called_once_with()
        self.parent._resume_device.assert_called_once_with()
        self.assertFalse(self.parent.disable_notify)
        self.assertFalse(self.parent.disable_persistence)

    def test_resume_clears_notification_and_persistence_flags_on_failure(self):
        for driver_mode in (True, False):
            with self.subTest(driver_mode=driver_mode):
                self.parent.DRIVER_MODE = driver_mode
                self.write.reset_mock()
                self.write.side_effect = OSError('busy') if driver_mode else None
                self.parent.restore_brightness.side_effect = None if driver_mode else OSError('busy')

                with self.assertRaises(OSError):
                    self.parent.resume_device()

                self.assertFalse(self.parent.disable_notify)
                self.assertFalse(self.parent.disable_persistence)
                self.assertEqual(self.parent.DRIVER_MODE, driver_mode)
                self.assertEqual(self.write.call_count, int(driver_mode))
                self.parent._resume_device.assert_not_called()

    def test_close_stops_and_joins_monitor_before_base_teardown(self):
        order = []

        def join():
            self.assertTrue(self.monitor.shutdown)
            order.append('join')

        self.parent._is_closed = False
        self.monitor._ident = 123  # Model a started thread; join itself is mocked.
        self.monitor.join = Mock(side_effect=join)
        self.parent.getDPI = Mock(side_effect=lambda: order.append('dpi') or [800, 800])
        self.write.side_effect = lambda *_args: order.append('firmware mode')
        self.parent._close = Mock(side_effect=lambda: order.append('cleanup'))

        self.parent.close()
        self.parent.close()

        self.assertEqual(order, ['join', 'dpi', 'firmware mode', 'cleanup'])
        self.monitor.join.assert_called_once_with()
        self.assertTrue(self.parent._is_closed)

    def test_partial_constructor_with_no_monitor_can_close(self):
        self.ready.return_value = False
        with self.assertRaises(DeviceNotReadyError):
            self.parent.__init__(device_path='/fake/naga')
        self.assertIsNone(self.parent._mouse_monitor)
        self.parent.close()
        self.write.assert_not_called()

        # Also cover a base constructor that became open before failing.
        self.parent._is_closed = False
        self.parent._close = Mock()
        self.parent.close()
        self.parent._close.assert_called_once_with()
        self.assertTrue(self.parent._is_closed)

    def test_constructor_start_failure_closes_without_joining_unstarted_monitor(self):
        def base_init(device, *_args, **_kwargs):
            self.assertIsNone(device._mouse_monitor)
            device._is_closed = False

        self.parent._close = Mock()
        with patch.object(RazerDevice, '__init__', autospec=True, side_effect=base_init), \
                patch.object(mouse_monitor.MouseMonitor, 'start', autospec=True,
                             side_effect=RuntimeError('cannot start monitor')) as start:
            with self.assertRaisesRegex(RuntimeError, 'cannot start monitor'):
                self.construct(configparser.ConfigParser())
        monitor = self.parent._mouse_monitor
        start.assert_called_once_with(monitor)
        self.assertIsNotNone(monitor)
        self.assertIsNone(monitor.ident)
        self.assertFalse(self.parent._is_closed)

        with patch.object(monitor, 'join', wraps=monitor.join) as join, \
                patch.object(RazerDevice, 'close', autospec=True, side_effect=RazerDevice.close) as base_close:
            self.parent.close()
            join.assert_not_called()
            base_close.assert_called_once_with(self.parent)

        self.assertTrue(monitor.shutdown)
        self.assertTrue(self.parent._is_closed)
        self.parent._close.assert_called_once_with()
        self.write.assert_called_once_with(0, 0)

    def test_constructor_initial_mode_write_failure_precedes_background_services(self):
        self.parent.get_serial = Mock(return_value='SERIAL123')
        error = OSError(110, 'receiver unavailable')
        self.write.side_effect = error
        with patch('openrazer_daemon.hardware.device_base.effect_sync.EffectSync',
                   side_effect=AssertionError('Background services must not start')) as effect_sync:
            with self.assertRaises(DeviceNotReadyError) as raised:
                self.construct(configparser.ConfigParser())

        self.assertIs(raised.exception.__cause__, error)
        self.assertTrue(self.parent._is_closed)
        self.assertIsNone(self.parent._mouse_monitor)
        effect_sync.assert_not_called()
        self.parent.close()
        self.write.assert_called_once_with(3, 0)

    def test_firmware_request_wins_over_recovery_waiting_for_shared_lock(self):
        sampled = threading.Event()
        self.activity.side_effect = lambda _path: sampled.set() or 0

        with self.parent._device_mode_lock:
            worker = self.start_worker(lambda: self.run_polls(0))
            self.assertTrue(sampled.wait(1), 'Monitor did not sample activity')
            self.parent.set_device_mode(0, 0)
        self.finish_workers(worker)

        self.write.assert_called_once_with(0, 0)
        self.assertFalse(self.parent.DRIVER_MODE)
        self.ready.assert_not_called()
        self.open.assert_not_called()
        self.parent.get_device_mode.assert_not_called()

    def test_external_mode_request_waits_for_inflight_verified_recovery(self):
        reading = threading.Event()
        release = threading.Event()
        requested = threading.Event()
        completed = threading.Event()
        order = []

        def get_mode():
            order.append('read')
            if len(order) == 1:
                reading.set()
                if not release.wait(1):
                    raise AssertionError('Fake read was not released')
                return '0:0'
            return '3:0'

        def request_mode():
            requested.set()
            self.parent.set_device_mode(0, 0)
            completed.set()

        self.parent.get_device_mode.side_effect = get_mode
        self.write.side_effect = lambda mode, _param: order.append(mode)
        recovering = self.start_worker(self.monitor._apply_driver_mode)
        try:
            self.assertTrue(reading.wait(1), 'Recovery did not reach fake read')
            requesting = self.start_worker(request_mode)
            self.assertTrue(requested.wait(1), 'External request did not start')
            self.assertFalse(completed.wait(0.05), 'External request bypassed the shared lock')
        finally:
            release.set()
        self.finish_workers(recovering, requesting)

        self.assertEqual(order, ['read', 3, 'read', 0])
        self.assertFalse(self.parent.DRIVER_MODE)

    def test_started_monitor_close_waits_for_inflight_io_and_prevents_later_monitor_io(self):
        reading = threading.Event()
        release = threading.Event()
        closing = threading.Event()
        closed = threading.Event()
        shutdown_wait = self.monitor._shutdown.wait
        run = self.monitor.run

        def wait(interval):
            self.assertEqual(interval, mouse_monitor.POLL_INTERVAL)
            if not reading.is_set():
                return False
            self.assertTrue(shutdown_wait(1), 'Close did not stop the monitor')
            return True

        def monitored_run():
            try:
                run()
            except BaseException as error:
                self.thread_errors.append(error)

        def get_mode():
            self.assertFalse(self.monitor.shutdown)
            reading.set()
            if not release.wait(1):
                raise AssertionError('Fake read was not released')
            self.assertFalse(self.monitor.shutdown)
            return '3:0'

        def close():
            closing.set()
            self.parent.close()
            closed.set()

        def teardown(*_args):
            self.assertTrue(self.monitor.shutdown)
            self.assertFalse(self.monitor.is_alive(), 'Base teardown preceded monitor join')

        self.parent._is_closed = False
        self.parent._close = Mock(side_effect=teardown)
        self.write.side_effect = teardown
        self.parent.get_device_mode.side_effect = get_mode
        self.monitor.daemon = True
        self.monitor.run = monitored_run
        self.start_patch(patch.object(self.monitor._shutdown, 'wait', side_effect=wait))
        try:
            self.monitor.start()
            self.addCleanup(self.monitor.join, 1)
            self.assertTrue(reading.wait(1), 'Recovery did not reach fake read')
            closer = self.start_worker(close)
            self.assertTrue(closing.wait(1), 'Close request did not start')
            self.assertFalse(closed.wait(0.05), 'Close bypassed the shared lock')
            self.assertFalse(self.monitor.shutdown)
            self.write.assert_not_called()
            self.parent._close.assert_not_called()
        finally:
            release.set()
        self.finish_workers(self.monitor, closer)
        self.assertTrue(self.monitor.shutdown)
        self.assertTrue(closed.is_set())
        self.assertTrue(self.parent._is_closed)
        self.parent._close.assert_called_once_with()
        self.write.assert_called_once_with(0, 0)
        calls_before = (self.activity.call_count, self.ready.call_count,
                        self.open.call_count, self.parent.get_device_mode.call_count)

        self.monitor._apply_driver_mode()
        run()

        self.assertEqual((self.activity.call_count, self.ready.call_count,
                          self.open.call_count, self.parent.get_device_mode.call_count), calls_before)
        self.write.assert_called_once_with(0, 0)


if __name__ == '__main__':
    unittest.main()
