# SPDX-License-Identifier: GPL-2.0-or-later

import typing
import unittest
from unittest.mock import Mock, call, patch

import dbus

from openrazer.client import devices
from openrazer.client.devices.mice import RazerMouse


SCROLL_METHODS = (
    'getScrollMode', 'setScrollMode', 'getScrollModeOptions',
    'getScrollAcceleration', 'setScrollAcceleration',
    'getScrollSmartReel', 'setScrollSmartReel',
)


class MouseScrollModeTest(unittest.TestCase):
    def setUp(self):
        self.scroll = Mock()

    def make_mouse(self, methods=SCROLL_METHODS):
        device = Mock()
        device.getDeviceName.return_value = 'Fake Mouse'
        device.getDeviceType.return_value = 'mouse'
        device.getDriverVersion.return_value = 'test'
        device.getVidPid.return_value = (0x1532, 0x00E8)
        device.hasMatrix.return_value = False
        introspection = Mock()
        introspection.Introspect.return_value = (
            '<node><interface name="razer.device.scroll">' +
            ''.join('<method name="{0}"/>'.format(name) for name in methods) +
            '</interface></node>'
        )
        interfaces = {
            'org.freedesktop.DBus.Introspectable': introspection,
            'razer.device.misc': device,
            'razer.device.lighting.brightness': Mock(),
            'razer.device.scroll': self.scroll,
        }
        with patch.object(devices._dbus, 'SessionBus') as session_bus, \
                patch.object(devices._dbus, 'Interface', side_effect=lambda proxy, name: interfaces[name]) as interface, \
                patch.object(devices, '_RazerFX'):
            mouse = RazerMouse('FAKE_SERIAL')
        session_bus.return_value.get_object.assert_called_once_with('org.razer', '/org/razer/device/FAKE_SERIAL')
        self.assertEqual(call(mouse._dbus, 'razer.device.scroll') in interface.call_args_list,
                         any(name in methods for name in ('getScrollMode', 'getScrollAcceleration', 'getScrollSmartReel')))
        return mouse

    def test_introspection_registers_scroll_capabilities_and_interface(self):
        mouse = self.make_mouse()
        for capability in ('scroll_mode', 'scroll_mode_options', 'scroll_acceleration', 'scroll_smart_reel'):
            self.assertTrue(mouse.has(capability))
            self.assertTrue(mouse.capabilities[capability])
        self.assertIs(mouse._dbus_interfaces['scroll'], self.scroll)

    def test_options_are_an_ordered_native_string_list(self):
        mouse = self.make_mouse()
        for options in (['tactile', 'free_spin'], ['tactile', 'free_spin', 'precision_tactile']):
            with self.subTest(options=options):
                self.scroll.reset_mock()
                self.scroll.getScrollModeOptions.return_value = dbus.Array(
                    [dbus.String(option) for option in options], signature='s')
                result = mouse.scroll_mode_options
                self.assertEqual(result, options)
                self.assertIs(type(result), list)
                self.assertTrue(all(type(option) is str for option in result))
                self.scroll.getScrollModeOptions.assert_called_once_with()
        self.assertEqual(typing.get_type_hints(RazerMouse.scroll_mode_options.fget)['return'], list[str])

    def test_get_and_set_all_naga_scroll_modes(self):
        mouse = self.make_mouse()
        for mode in (0, 1, 2):
            with self.subTest(mode=mode):
                self.scroll.reset_mock()
                self.scroll.getScrollMode.return_value = dbus.Byte(mode)
                result = mouse.scroll_mode
                self.assertEqual(result, mode)
                self.assertIs(type(result), int)
                mouse.scroll_mode = mode
                self.scroll.getScrollMode.assert_called_once_with()
                self.scroll.setScrollMode.assert_called_once_with(mode)

    def test_missing_options_endpoint_does_not_disable_scroll_mode(self):
        mouse = self.make_mouse(('getScrollMode', 'setScrollMode'))
        self.assertTrue(mouse.has('scroll_mode'))
        self.assertFalse(mouse.has('scroll_mode_options'))
        with self.assertRaises(NotImplementedError):
            _ = mouse.scroll_mode_options
        self.scroll.getScrollModeOptions.assert_not_called()
        mouse.scroll_mode = 1
        self.scroll.setScrollMode.assert_called_once_with(1)

    def test_missing_scroll_endpoints_raise_without_dbus_calls(self):
        mouse = self.make_mouse(())
        for capability in ('scroll_mode', 'scroll_mode_options', 'scroll_acceleration', 'scroll_smart_reel'):
            with self.subTest(capability=capability):
                self.assertFalse(mouse.has(capability))
                with self.assertRaises(NotImplementedError):
                    getattr(mouse, capability)
        with self.assertRaises(NotImplementedError):
            mouse.scroll_mode = 0
        self.assertNotIn('scroll', mouse._dbus_interfaces)
        self.assertEqual(self.scroll.mock_calls, [])

    def test_basilisk_options_preserve_acceleration_and_smart_reel(self):
        mouse = self.make_mouse()
        self.scroll.getScrollModeOptions.return_value = dbus.Array(
            [dbus.String('tactile'), dbus.String('free_spin')], signature='s')
        self.assertEqual(mouse.scroll_mode_options, ['tactile', 'free_spin'])
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.scroll.reset_mock()
                self.scroll.getScrollAcceleration.return_value = dbus.Boolean(enabled)
                self.scroll.getScrollSmartReel.return_value = dbus.Boolean(enabled)
                self.assertIs(mouse.scroll_acceleration, enabled)
                self.assertIs(mouse.scroll_smart_reel, enabled)
                mouse.scroll_acceleration = enabled
                mouse.scroll_smart_reel = enabled
                self.scroll.setScrollAcceleration.assert_called_once_with(enabled)
                self.scroll.setScrollSmartReel.assert_called_once_with(enabled)

    def test_scroll_options_are_read_only(self):
        mouse = self.make_mouse()
        with self.assertRaises(AttributeError):
            mouse.scroll_mode_options = ['tactile']
        self.assertEqual(self.scroll.mock_calls, [])


if __name__ == '__main__':
    unittest.main()
