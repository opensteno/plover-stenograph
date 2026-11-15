"""Resolve a PyUSB libusb1 backend for Stenograph USB transport.

On macOS, prefer the library shipped by ``libusb-package``. Its library is
independent of the Python extension ABI, unlike the older backend's CPython-
version-specific extension. Keep system discovery and explicit Homebrew /
MacPorts paths as fallbacks for installations without a usable packaged library.
Other platforms retain the existing ``pyusb_libusb1_backend`` selection.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import sys


# Plover attaches its handlers to the "plover" logger, not the root logger.
_logger = logging.getLogger("plover.stenograph.libusb_backend")


def _log_packaged_backend_failure(path):
    # PyUSB suppresses its loader errors unless PYUSB_DEBUG was set before
    # import. Probe the same image only after PyUSB fails, so the ordinary
    # Plover log includes dyld's reason (e.g. an incompatible architecture).
    try:
        ctypes.CDLL(str(path))
    except OSError as error:
        _logger.warning(
            "Packaged libusb at %s cannot load in this %s process: %s; "
            "trying system libusb", path, platform.machine(), error
        )
    else:
        _logger.warning(
            "Packaged libusb at %s loads in this %s process, but PyUSB could "
            "not initialize its backend; trying system libusb. Set "
            "PYUSB_DEBUG=debug before starting Plover for additional details.",
            path, platform.machine()
        )


def _macos_packaged_backend(libusb1):
    try:
        import libusb_package

        path = libusb_package.get_library_path()
        if path is None:
            # Source builds can succeed without including the native library.
            # Do not let the package silently fall back to system discovery:
            # that would hide a broken package in both diagnostics and tests.
            _logger.warning("libusb-package contains no native library; trying system libusb")
            return None

        # PyUSB can return a cached backend without loading this path.
        _logger.debug("Requesting PyUSB backend with packaged library candidate %s", path)
        backend = libusb1.get_backend(find_library=lambda _name: str(path))
        if backend is None:
            _log_packaged_backend_failure(path)
        return backend
    except ImportError:
        _logger.warning("libusb-package is unavailable; trying system libusb", exc_info=True)
    except Exception:
        _logger.warning("Could not initialize packaged libusb; trying system libusb", exc_info=True)
    return None


def _candidate_libusb_paths() -> list[str]:
    if not sys.platform.startswith("darwin"):
        return []
    prefixes = (
        "/opt/homebrew/lib",
        "/usr/local/lib",
        "/opt/local/lib",
    )
    names = (
        "libusb-1.0.dylib",
        "libusb-1.0.0.dylib",
    )
    paths: list[str] = []
    for prefix in prefixes:
        for name in names:
            p = os.path.join(prefix, name)
            if os.path.isfile(p):
                paths.append(p)
    return paths


def resolve_pyusb_backend():
    """Return a working libusb1 backend, or ``None`` if none could be loaded."""
    import usb.backend.libusb1 as libusb1

    if sys.platform.startswith("darwin"):
        backend = _macos_packaged_backend(libusb1)
        if backend is not None:
            return backend
    else:
        # Retain the existing backend on non-macOS platforms.
        try:
            from pyusb_libusb1_backend import get_pyusb_backend as packaged_backend

            backend = packaged_backend()
            if backend is not None:
                return backend
        except Exception:
            _logger.debug("Legacy packaged libusb is unavailable", exc_info=True)

    backend = libusb1.get_backend()
    if backend is not None:
        _logger.debug("Resolved libusb through default PyUSB discovery")
        return backend

    for path in _candidate_libusb_paths():
        _logger.debug("Trying system libusb at %s", path)
        backend = libusb1.get_backend(find_library=lambda _name: path)
        if backend is not None:
            return backend

    return None
