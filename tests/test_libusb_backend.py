import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import usb.backend.libusb1 as libusb1

from stenograph import libusb_backend
from stenograph.exception import ConnectionError, MissingUsbBackendError
from stenograph.transport_libusb import LibusbTransport


class BackendSelectionTests(unittest.TestCase):
    def setUp(self):
        self.path = Path("/plugin directory/libusb_package/libusb-1.0.dylib")
        self.package = types.ModuleType("libusb_package")
        self.package.get_library_path = Mock(return_value=self.path)
        self.legacy = types.ModuleType("pyusb_libusb1_backend")
        self.legacy.get_pyusb_backend = Mock()
        patches = [
            patch.object(sys, "platform", "darwin"),
            patch.dict(sys.modules, {
                "libusb_package": self.package,
                "pyusb_libusb1_backend": self.legacy,
            }),
            patch.object(libusb1, "get_backend"),
            patch.object(libusb_backend, "_candidate_libusb_paths", return_value=[]),
            patch.object(libusb_backend.ctypes, "CDLL"),
            patch.object(libusb_backend.platform, "machine", return_value="x86_64"),
        ]
        started = []
        for p in patches:
            started.append(p.start())
            self.addCleanup(p.stop)
        self.get_backend = started[2]
        self.candidates = started[3]
        self.load_library = started[4]

    def test_macos_prefers_packaged_path_and_ignores_stale_legacy_extension(self):
        expected = object()

        def load(*, find_library):
            self.assertEqual(find_library("usb-1.0"), str(self.path))
            return expected

        self.get_backend.side_effect = load
        self.assertIs(libusb_backend.resolve_pyusb_backend(), expected)
        self.get_backend.assert_called_once()
        self.legacy.get_pyusb_backend.assert_not_called()
        self.candidates.assert_not_called()
        self.load_library.assert_not_called()

    def test_missing_package_uses_system_discovery(self):
        with patch.dict(sys.modules, {"libusb_package": None}), self.assertLogs(
            libusb_backend._logger.name, level="WARNING"
        ) as logs:
            self.assertIs(libusb_backend.resolve_pyusb_backend(), self.get_backend.return_value)
        self.assertIn("libusb-package is unavailable", " ".join(logs.output))
        self.get_backend.assert_called_once_with()

    def test_source_install_without_library_is_reported_then_falls_back(self):
        self.package.get_library_path.return_value = None
        with self.assertLogs(libusb_backend._logger.name, level="WARNING") as logs:
            self.assertIs(libusb_backend.resolve_pyusb_backend(), self.get_backend.return_value)
        self.assertIn("contains no native library", " ".join(logs.output))
        self.get_backend.assert_called_once_with()

    def test_unloadable_packaged_library_falls_back(self):
        expected = object()
        # PyUSB returns None but normally suppresses the underlying error.
        # The plugin must report dyld's error without requiring PYUSB_DEBUG.
        self.get_backend.side_effect = [None, expected]
        self.load_library.side_effect = OSError(
            "incompatible architecture (have 'arm64', need 'x86_64')"
        )
        with self.assertLogs("plover", level="WARNING") as logs:
            self.assertIs(libusb_backend.resolve_pyusb_backend(), expected)
        message = " ".join(logs.output)
        self.assertIn(str(self.path), message)
        self.assertIn("this x86_64 process", message)
        self.assertIn("incompatible architecture (have 'arm64', need 'x86_64')", message)
        self.load_library.assert_called_once_with(str(self.path))
        self.assertEqual(self.get_backend.call_count, 2)
        self.assertEqual(self.get_backend.call_args.args, ())
        self.assertEqual(self.get_backend.call_args.kwargs, {})

    def test_loadable_library_with_failed_backend_initialization_is_distinguished(self):
        expected = object()
        self.get_backend.side_effect = [None, expected]
        with self.assertLogs("plover", level="WARNING") as logs:
            self.assertIs(libusb_backend.resolve_pyusb_backend(), expected)
        message = " ".join(logs.output)
        self.assertIn("loads in this x86_64 process", message)
        self.assertIn("could not initialize", message)
        self.assertIn("PYUSB_DEBUG=debug before starting Plover", message)
        self.assertNotIn("cannot load", message)
        self.load_library.assert_called_once_with(str(self.path))

    def test_packaged_initialization_exception_falls_back(self):
        self.package.get_library_path.side_effect = OSError("cannot extract library")
        with self.assertLogs(libusb_backend._logger.name, level="WARNING"):
            self.assertIs(libusb_backend.resolve_pyusb_backend(), self.get_backend.return_value)
        self.get_backend.assert_called_once_with()

    def test_explicit_paths_are_tried_after_default_discovery(self):
        self.package.get_library_path.return_value = None
        paths = ["/opt/homebrew/lib/libusb-1.0.dylib", "/usr/local/lib/libusb-1.0.dylib"]
        self.candidates.return_value = paths
        expected = object()
        attempted = []

        def load(find_library=None):
            candidate = find_library("usb-1.0") if find_library else None
            attempted.append(candidate)
            return expected if candidate == paths[1] else None

        self.get_backend.side_effect = load
        with self.assertLogs(libusb_backend._logger.name, level="WARNING"):
            self.assertIs(libusb_backend.resolve_pyusb_backend(), expected)
        self.assertEqual(attempted, [None, *paths])

    def test_exhausted_backends_return_none(self):
        self.get_backend.return_value = None
        self.candidates.return_value = ["/opt/local/lib/libusb-1.0.dylib"]
        with self.assertLogs(libusb_backend._logger.name, level="WARNING"):
            self.assertIsNone(libusb_backend.resolve_pyusb_backend())
        self.assertEqual(self.get_backend.call_count, 3)

    def test_linux_retains_legacy_backend(self):
        with patch.object(sys, "platform", "linux"):
            self.assertIs(libusb_backend.resolve_pyusb_backend(), self.legacy.get_pyusb_backend.return_value)
        self.package.get_library_path.assert_not_called()
        self.get_backend.assert_not_called()

    def test_linux_legacy_failure_uses_system_backend(self):
        self.legacy.get_pyusb_backend.side_effect = ImportError("legacy extension unavailable")
        with patch.object(sys, "platform", "linux"):
            self.assertIs(libusb_backend.resolve_pyusb_backend(), self.get_backend.return_value)
        self.package.get_library_path.assert_not_called()
        self.get_backend.assert_called_once_with()


class CandidatePathTests(unittest.TestCase):
    def test_only_existing_macos_paths_are_returned_in_priority_order(self):
        paths = [
            os.path.join("/opt/homebrew/lib", "libusb-1.0.dylib"),
            os.path.join("/opt/local/lib", "libusb-1.0.0.dylib"),
        ]
        with patch.object(sys, "platform", "darwin"), patch.object(
            libusb_backend.os.path, "isfile", side_effect=lambda p: p in paths
        ):
            self.assertEqual(libusb_backend._candidate_libusb_paths(), paths)

    def test_non_macos_does_not_scan_homebrew(self):
        with patch.object(sys, "platform", "linux"), patch.object(
            libusb_backend.os.path, "isfile"
        ) as isfile:
            self.assertEqual(libusb_backend._candidate_libusb_paths(), [])
        isfile.assert_not_called()


class ConnectionDiagnosisTests(unittest.TestCase):
    def test_missing_backend_does_not_attempt_device_discovery(self):
        with patch("stenograph.transport_libusb.resolve_pyusb_backend", return_value=None):
            transport = LibusbTransport()
        with patch("stenograph.transport_libusb.core.find") as find:
            with self.assertRaisesRegex(
                MissingUsbBackendError,
                "force-reinstall.*#switching-between-apple-silicon-and-rosetta",
            ):
                transport.connect()
        find.assert_not_called()

    def test_working_backend_without_writer_is_a_different_error(self):
        backend = object()
        with patch("stenograph.transport_libusb.resolve_pyusb_backend", return_value=backend):
            transport = LibusbTransport()
        with patch("stenograph.transport_libusb.core.find", return_value=None) as find:
            with self.assertRaisesRegex(ConnectionError, "USB device not connected"):
                transport.connect()
        find.assert_called_once_with(backend=backend, idVendor=0x112b)


if __name__ == "__main__":
    unittest.main()
