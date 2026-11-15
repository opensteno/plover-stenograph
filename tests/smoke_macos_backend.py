"""Run in a fresh process against an installed plugin, without opening a writer.

This deliberately forbids system discovery, so a developer/CI Homebrew install
cannot conceal a wheel without a usable libusb payload. Do not run after other
USB probes: PyUSB caches the first successfully initialized backend globally.
"""

import ctypes.util
import platform
import sys
from pathlib import Path
from unittest.mock import patch

import libusb_package
import usb.backend.libusb1 as libusb1

from stenograph import libusb_backend


if sys.platform != "darwin":
    raise SystemExit("This smoke test requires macOS")

path = libusb_package.get_library_path()
assert path is not None and path.is_file(), "The installed package has no native library"
assert libusb1._lib_object is None, "Run this test in a fresh process"

with patch.object(ctypes.util, "find_library", side_effect=AssertionError("System lookup used")), patch.object(
    libusb_backend, "_candidate_libusb_paths", side_effect=AssertionError("System fallback used")
):
    backend = libusb_backend.resolve_pyusb_backend()

assert backend is not None, "The packaged libusb could not initialize"
# Inspect the actual loaded image, not merely the backend return value: PyUSB's
# global cache can otherwise make a fallback look like a packaged-library pass.
assert Path(libusb1._lib._name).resolve() == path.resolve()
print(f"PASS: Python {platform.python_version()} ({platform.machine()}) loaded {path}")
