# SPDX-License-Identifier: GPL-2.0-or-later

import os
import tempfile
import unittest
import unittest.mock

from openrazer_daemon.dbus_services.dbus_methods.mouse_scroll_wheel import (
    get_scroll_mode_options,
    set_scroll_mode,
)
from openrazer_daemon.hardware.device_base import RazerDevice
from openrazer_daemon.hardware.mouse import (
    RazerBasiliskV3,
    RazerBasiliskV3_35K,
    RazerBasiliskV3Pro35KPhantomGreenEditionWired,
    RazerBasiliskV3Pro35KWired,
    RazerBasiliskV3ProWired,
    RazerNagaV3ProWired,
    RazerNagaV3ProWireless,
)


class DummyDevice(object):
    SCROLL_MODE_VERSION = 1

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
        self.device.SCROLL_MODE_VERSION = 2

        set_scroll_mode(self.device, 2)

        with open(self.driver_path, 'r') as driver_file:
            self.assertEqual(driver_file.read(), '2')

    def test_rejects_unsupported_mode(self):
        with self.assertRaisesRegex(ValueError, "in the range of 0 and 1"):
            set_scroll_mode(self.device, 2)

    def test_rejects_out_of_range_mode(self):
        self.device.SCROLL_MODE_VERSION = 2

        for mode in (-1, 3):
            with self.assertRaisesRegex(ValueError, "in the range of 0 and 2"):
                set_scroll_mode(self.device, mode)

    def test_standard_mode_options(self):
        self.assertEqual(get_scroll_mode_options(self.device), ["tactile", "free_spin"])

    def test_precision_tactile_mode_options(self):
        self.device.SCROLL_MODE_VERSION = 2

        self.assertEqual(get_scroll_mode_options(self.device),
                         ["tactile", "free_spin", "precision_tactile"])

    def test_device_mode_versions(self):
        self.assertEqual(RazerNagaV3ProWired.SCROLL_MODE_VERSION, 2)
        self.assertEqual(RazerNagaV3ProWireless.SCROLL_MODE_VERSION, 2)
        self.assertIn('get_scroll_mode_options', RazerNagaV3ProWireless.METHODS)

        for device_class in (RazerBasiliskV3, RazerBasiliskV3ProWired,
                             RazerBasiliskV3Pro35KWired,
                             RazerBasiliskV3Pro35KPhantomGreenEditionWired,
                             RazerBasiliskV3_35K):
            self.assertEqual(device_class.SCROLL_MODE_VERSION, 1)
            self.assertIn('get_scroll_mode_options', device_class.METHODS)

    def test_missing_device_image_is_empty_string(self):
        self.device.DEVICE_IMAGE = None

        self.assertEqual(RazerDevice.get_device_image(self.device), '')
