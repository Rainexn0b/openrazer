# SPDX-License-Identifier: GPL-2.0-or-later

import os
import tempfile
import unittest
import unittest.mock

from openrazer_daemon.dbus_services.dbus_methods.mouse_scroll_wheel import set_scroll_mode


class DummyDevice(object):
    SCROLL_MODE_MAX = 1

    def __init__(self, driver_path):
        self.driver_path = driver_path
        self.logger = unittest.mock.MagicMock()

    def get_driver_path(self, attribute):
        if attribute != 'scroll_mode':
            raise ValueError("unexpected attribute")
        return self.driver_path


class MouseScrollWheelTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.driver_path = os.path.join(self.temp_dir.name, 'scroll_mode')
        self.device = DummyDevice(self.driver_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_standard_modes(self):
        for mode in (0, 1):
            set_scroll_mode(self.device, mode)
            with open(self.driver_path, 'r') as driver_file:
                self.assertEqual(driver_file.read(), str(mode))

    def test_precision_tactile_mode(self):
        self.device.SCROLL_MODE_MAX = 2

        set_scroll_mode(self.device, 2)

        with open(self.driver_path, 'r') as driver_file:
            self.assertEqual(driver_file.read(), '2')

    def test_rejects_unsupported_mode(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            set_scroll_mode(self.device, 2)

    def test_rejects_out_of_range_mode(self):
        self.device.SCROLL_MODE_MAX = 2

        for mode in (-1, 3):
            with self.assertRaisesRegex(ValueError, "between 0 and 2"):
                set_scroll_mode(self.device, mode)
