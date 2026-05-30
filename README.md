# Plover Stenograph

A Plover plugin for using newer Stenograph machines (Stentura Protégé and
Fusion, Élan Mira and Cybra, Diamanté, Wave, Luminex) connected to the computer
over USB or Wi-Fi.

This is a work-in-progress rewrite of the
[plover-stenograph-usb](https://github.com/morinted/plover_stenograph_usb) and
[plover-stenograph-wifi](https://github.com/stanographer/plover_stenograph_wifi)
plugins to reuse code and add more protocol functionality.

## macOS (Stenograph USB)

PyUSB needs **libusb 1.0** on the system. The **`pyusb_libusb1_backend`** dependency
does not ship a compiled libusb for typical installs, so on macOS you should
install libusb yourself, for example:

```bash
brew install libusb
```

Inside the signed **Plover.app**, PyUSB may not discover Homebrew’s library via
the default search path. This plugin tries common locations (**`/opt/homebrew/lib`**,
**`/usr/local/lib`**, **`/opt/local/lib`**) after the default lookup.

### Installing this plugin from the terminal

If **Plugin Manager** fails while building (e.g. **`unrecognized arguments: -u -c`**),
install with the Python interpreter inside **Plover.app** and Plover’s plugin
prefix:

```bash
export PYTHONUSERBASE="$HOME/Library/Application Support/plover/plugins/mac"
PY="/Applications/Plover.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
"$PY" -m plover.plugins_manager install plover-stenograph
```

See [opensteno/plover#1841](https://github.com/opensteno/plover/issues/1841) for
background on **`No backend available`** on macOS.

## Releasing

Cut a release with `scripts/release.sh <version>`, e.g.:

```bash
./scripts/release.sh v2.1.1
```

The version must start with `v`. The script creates an annotated git tag,
pushes it, and creates a GitHub release with auto-generated notes, which
triggers the `publish` workflow to upload the sdist to PyPI via trusted
publishing.
