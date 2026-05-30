"""Resolve a PyUSB libusb1 backend for Stenograph USB transport.

The optional ``pyusb_libusb1_backend`` wheel often installs without a compiled
``libusb`` extension (no vendored libusb sources at build time). In that case
PyUSB must load ``libusb-1.0`` from the system. GUI apps on macOS (e.g. signed
Plover.app) may not see Homebrew libraries via the default search path, so we
try common install locations explicitly.
"""

from __future__ import annotations

import os
import sys


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

    # Prefer packaged backend when the extension was actually built.
    try:
        from pyusb_libusb1_backend import get_pyusb_backend as packaged_backend

        backend = packaged_backend()
        if backend is not None:
            return backend
    except Exception:
        pass

    backend = libusb1.get_backend()
    if backend is not None:
        return backend

    for path in _candidate_libusb_paths():
        backend = libusb1.get_backend(find_library=lambda _name: path)
        if backend is not None:
            return backend

    return None
