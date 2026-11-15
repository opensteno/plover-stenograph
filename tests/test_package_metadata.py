import unittest
from importlib.metadata import requires

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


class DependencyTests(unittest.TestCase):
    def test_usb_dependencies_are_platform_specific(self):
        dependencies = [Requirement(r) for r in requires("plover-stenograph")]
        for system, wanted in [
            ("Darwin", {"pyusb", "libusb-package"}),
            ("Linux", {"pyusb", "pyusb-libusb1-backend"}),
            ("Windows", set()),
        ]:
            with self.subTest(system=system):
                selected = {
                    canonicalize_name(r.name) for r in dependencies
                    if "usb" in r.name and (
                        r.marker is None or r.marker.evaluate({"platform_system": system})
                    )
                }
                self.assertEqual(selected, wanted)
        bundled = next(r for r in dependencies if r.name == "libusb-package")
        self.assertIn("1.0.30.0", bundled.specifier)
        self.assertNotIn("1.0.24.0", bundled.specifier)
