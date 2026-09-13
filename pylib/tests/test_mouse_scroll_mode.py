# SPDX-License-Identifier: GPL-2.0-or-later

import unittest
import unittest.mock

from openrazer.client.devices.mice import RazerMouse


class MouseScrollModeTest(unittest.TestCase):
    def setUp(self):
        self.mouse = object.__new__(RazerMouse)
        self.mouse._capabilities = {'scroll_mode_options': True}
        self.scroll_interface = unittest.mock.MagicMock()
        self.mouse._dbus_interfaces = {'scroll': self.scroll_interface}

    def test_scroll_mode_options(self):
        self.scroll_interface.getScrollModeOptions.return_value = (
            "tactile", "free_spin", "precision_tactile")

        self.assertEqual(self.mouse.scroll_mode_options,
                         ["tactile", "free_spin", "precision_tactile"])
        self.scroll_interface.getScrollModeOptions.assert_called_once_with()

    def test_scroll_mode_options_unsupported(self):
        self.mouse._capabilities['scroll_mode_options'] = False

        with self.assertRaises(NotImplementedError):
            _ = self.mouse.scroll_mode_options


if __name__ == '__main__':
    unittest.main()
