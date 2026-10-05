# SPDX-License-Identifier: GPL-2.0-or-later

import inspect
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch

import dbus

from openrazer_daemon.dbus_services import dbus_methods
from openrazer_daemon.dbus_services.dbus_methods import mouse_scroll_wheel
from openrazer_daemon.hardware import mouse


NAGA_DEVICES = (mouse.RazerNagaV3ProWired, mouse.RazerNagaV3ProWireless)
BASILISK_DEVICES = (
    mouse.RazerBasiliskV3,
    mouse.RazerBasiliskV3ProWired,
    mouse.RazerBasiliskV3ProWireless,
    mouse.RazerBasiliskV3Pro35KWired,
    mouse.RazerBasiliskV3Pro35KWireless,
    mouse.RazerBasiliskV3Pro35KPhantomGreenEditionWired,
    mouse.RazerBasiliskV3Pro35KPhantomGreenEditionWireless,
    mouse.RazerBasiliskV3_35K,
)


class MouseScrollWheelTest(unittest.TestCase):
    def make_device(self, device_class):
        return SimpleNamespace(
            SCROLL_MODE_VERSION=device_class.SCROLL_MODE_VERSION,
            logger=Mock(),
            get_driver_path=Mock(side_effect=lambda name: '/fake/mouse/' + name),
        )

    def test_naga_accepts_all_three_modes(self):
        for device_class in NAGA_DEVICES:
            self.assertEqual(device_class.SCROLL_MODE_VERSION, 2)
            for mode in (0, 1, 2):
                with self.subTest(device=device_class.__name__, mode=mode):
                    device = self.make_device(device_class)
                    with patch('builtins.open', mock_open()) as driver_open:
                        mouse_scroll_wheel.set_scroll_mode(device, dbus.Byte(mode))
                    device.get_driver_path.assert_called_once_with('scroll_mode')
                    driver_open.assert_called_once_with('/fake/mouse/scroll_mode', 'w')
                    driver_open().write.assert_called_once_with(str(mode))

    def test_basilisk_accepts_existing_modes(self):
        for device_class in BASILISK_DEVICES:
            self.assertEqual(device_class.SCROLL_MODE_VERSION, 1)
            for mode in (0, 1):
                with self.subTest(device=device_class.__name__, mode=mode):
                    device = self.make_device(device_class)
                    with patch('builtins.open', mock_open()) as driver_open:
                        mouse_scroll_wheel.set_scroll_mode(device, dbus.Byte(mode))
                    driver_open.assert_called_once_with('/fake/mouse/scroll_mode', 'w')
                    driver_open().write.assert_called_once_with(str(mode))

    def test_invalid_modes_do_not_touch_sysfs(self):
        for device_class in NAGA_DEVICES + BASILISK_DEVICES:
            invalid_modes = (-1, 3, 255) if device_class in NAGA_DEVICES else (-1, 2, 3, 255)
            for mode in invalid_modes:
                with self.subTest(device=device_class.__name__, mode=mode):
                    device = self.make_device(device_class)
                    with patch('builtins.open', mock_open()) as driver_open:
                        with self.assertRaises(ValueError):
                            mouse_scroll_wheel.set_scroll_mode(device, mode)
                    driver_open.assert_not_called()
                    device.get_driver_path.assert_not_called()

    def test_get_mode_reads_integer_from_sysfs(self):
        for mode in (0, 1, 2):
            with self.subTest(mode=mode):
                device = self.make_device(mouse.RazerNagaV3ProWireless)
                with patch('builtins.open', mock_open(read_data=' {0}\n'.format(mode))) as driver_open:
                    result = mouse_scroll_wheel.get_scroll_mode(device)
                self.assertEqual(result, mode)
                self.assertIs(type(result), int)
                device.get_driver_path.assert_called_once_with('scroll_mode')
                driver_open.assert_called_once_with('/fake/mouse/scroll_mode', 'r')

    def test_options_match_all_scroll_device_method_lists(self):
        scroll_devices = {
            device_class for name, device_class in inspect.getmembers(mouse, inspect.isclass)
            if not name.startswith('_') and 'set_scroll_mode' in device_class.METHODS
        }
        self.assertEqual(scroll_devices, set(NAGA_DEVICES + BASILISK_DEVICES))
        controls = ('get_scroll_mode', 'set_scroll_mode', 'get_scroll_mode_options',
                    'get_scroll_acceleration', 'set_scroll_acceleration',
                    'get_scroll_smart_reel', 'set_scroll_smart_reel')
        for device_class in scroll_devices:
            with self.subTest(device=device_class.__name__):
                for method in controls:
                    self.assertEqual(device_class.METHODS.count(method), 1)
                    self.assertTrue(getattr(dbus_methods, method).endpoint)
                expected = ['tactile', 'free_spin']
                if device_class in NAGA_DEVICES:
                    expected.append('precision_tactile')
                device = self.make_device(device_class)
                with patch('builtins.open', mock_open()) as driver_open:
                    self.assertEqual(mouse_scroll_wheel.get_scroll_mode_options(device), expected)
                driver_open.assert_not_called()
                device.get_driver_path.assert_not_called()
        self.assertNotIn('get_scroll_mode_options', mouse.RazerNagaV2ProWired.METHODS)
        self.assertNotIn('get_scroll_mode_options', mouse.RazerBasilisk.METHODS)

    def test_scroll_endpoint_signatures(self):
        for method, name, in_sig, out_sig in (
                (mouse_scroll_wheel.set_scroll_mode, 'setScrollMode', 'y', None),
                (mouse_scroll_wheel.get_scroll_mode, 'getScrollMode', None, 'y'),
                (mouse_scroll_wheel.get_scroll_mode_options, 'getScrollModeOptions', None, 'as')):
            with self.subTest(method=name):
                self.assertEqual(method.interface, 'razer.device.scroll')
                self.assertEqual(method.name, name)
                self.assertEqual(method.in_sig, in_sig)
                self.assertEqual(method.out_sig, out_sig)

    def test_acceleration_and_smart_reel_controls_are_preserved(self):
        for device_class in (mouse.RazerBasiliskV3, mouse.RazerNagaV3ProWireless):
            for attribute in ('scroll_acceleration', 'scroll_smart_reel'):
                for enabled in (False, True):
                    with self.subTest(device=device_class.__name__, attribute=attribute, enabled=enabled):
                        device = self.make_device(device_class)
                        with patch('builtins.open', mock_open(read_data=str(int(enabled)))) as driver_open:
                            getattr(mouse_scroll_wheel, 'set_' + attribute)(device, enabled)
                            result = getattr(mouse_scroll_wheel, 'get_' + attribute)(device)
                        self.assertIs(result, enabled)
                        driver_open().write.assert_called_once_with(str(int(enabled)))
                        self.assertEqual(driver_open.call_args_list[0].args, ('/fake/mouse/' + attribute, 'w'))
                        self.assertEqual(driver_open.call_args_list[1].args, ('/fake/mouse/' + attribute, 'r'))

    def test_dpi_builder_preserves_50000_on_both_axes(self):
        compiler = shutil.which('cc')
        if compiler is None:
            self.skipTest('A C compiler is required for the isolated DPI report test')
        source = (Path(__file__).resolve().parents[2] / 'driver/razerchromacommon.c').read_text()
        builder = re.search(r'struct razer_report razer_chroma_misc_set_dpi_xy\([^)]*\)\n\{.*?\n\}', source, re.DOTALL)
        self.assertIsNotNone(builder)
        # Compile the real builder with fake kernel helpers, without loading a driver.
        harness = '''
#include <assert.h>
#include <stddef.h>
#define VARSTORE 1
#define clamp(value, low, high) ((value) < (low) ? (low) : ((value) > (high) ? (high) : (value)))
struct razer_report { unsigned char arguments[80]; };
static struct razer_report get_razer_report(unsigned char command_class,
                                           unsigned char command_id,
                                           unsigned char data_size)
{
    assert(command_class == 0x04 && command_id == 0x05 && data_size == 0x07);
    struct razer_report report = {0};
    return report;
}
''' + builder.group(0) + '''
int main(void)
{
    const unsigned short cases[][4] = {
        {0, 99, 100, 100},
        {100, 100, 100, 100},
        {45000, 45001, 45000, 45001},
        {49999, 50000, 49999, 50000},
        {50000, 50000, 50000, 50000},
        {65535, 65535, 50000, 50000},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        struct razer_report report = razer_chroma_misc_set_dpi_xy(VARSTORE, cases[i][0], cases[i][1]);
        assert(report.arguments[0] == VARSTORE);
        assert(report.arguments[1] == (cases[i][2] >> 8));
        assert(report.arguments[2] == (cases[i][2] & 0xFF));
        assert(report.arguments[3] == (cases[i][3] >> 8));
        assert(report.arguments[4] == (cases[i][3] & 0xFF));
        assert(report.arguments[5] == 0 && report.arguments[6] == 0);
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='openrazer-dpi-') as directory:
            binary = str(Path(directory) / 'test-dpi')
            compiled = subprocess.run(
                [compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-x', 'c', '-', '-o', binary],
                input=harness, capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([binary], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
