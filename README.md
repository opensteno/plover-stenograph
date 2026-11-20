# Plover Stenograph

A Plover plugin for using newer Stenograph machines (Stentura Protégé and
Fusion, Élan Mira and Cybra, Diamanté, Wave, Luminex) connected to the computer
over USB or Wi-Fi.

This is a work-in-progress rewrite of the
[plover-stenograph-usb](https://github.com/morinted/plover_stenograph_usb) and
[plover-stenograph-wifi](https://github.com/stanographer/plover_stenograph_wifi)
plugins to reuse code and add more protocol functionality.

## Reading and recovery

A writer that reports no realtime file stays connected and is polled every
100 ms. When a matching Open File reply confirms that no file exists, the first
strokes of the subsequently opened file are delivered immediately. Unsolicited
sequence-zero statuses and Read errors do not prove a new job exists. On an
initial connection or after a transport failure, existing file contents are
still skipped until the reader catches up; this prevents old strokes being
replayed as new input.

Libusb replies are assembled across transfers, checked for valid framing, and
matched to the request. Stale replies and empty transfers have count limits;
all reads for a response share a three-second deadline. Writes have a separate
three-second bound; stopping waits for an in-flight exchange to finish. A
malformed or incomplete exchange reconnects, and an already configured USB device is not
reconfigured on each connection. Only an endpoint that explicitly reports a
USB stall has its halt cleared. Partial stroke records span reads; malformed
complete records are skipped during live delivery, with one warning per file
in Plover's log. History is discarded without decoding. The Windows and Wi-Fi
transports use the same writer-status handling; their underlying I/O mechanisms
are unchanged.

Run the regression suite in an isolated environment. On Debian/Ubuntu, install
`libxkbcommon-dev` first for Plover's keyboard dependency:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r tests/reading/requirements.txt
python -m build --no-isolation
python -m pip install --no-deps --force-reinstall dist/*.whl
python -m unittest discover -s tests/reading -v
```

Tests use Plover's machine base with simulated writers; no writer is opened.
CI also tests the installed wheel outside the checkout. Before release, check
an idle writer, first strokes in a new job, closing/reopening jobs, stopping
capture, and unplug/reconnect with actual hardware.

## Windows (Stenograph USB)

The **Stenograph USB** machine supports both Stenograph's WinUSB driver
(`StenographWinUsb`, driver package version 1.0.0.2) and its older
`wdfsgusbV3` driver. No additional USB library is required on Windows.
The plugin checks the WinUSB interface first and uses the legacy interface
when no WinUSB writer is present. Errors opening a present WinUSB writer
are reported rather than hidden by a legacy fallback. Close other CAT
applications before connecting to the writer.

WinUSB reads and writes use three-second transfer timeouts. Responses can
span transfers, and stale responses are discarded within a bounded exchange.
The interface handle is released before the Windows device handle on shutdown
and failed initialization. Existing job/history behavior is shared with the
other transports.

WinUSB compatibility is covered by native Windows tests with simulated API
responses. On November 19, 2026, a Luminex II connected through the existing
WinUSB driver in Plover's bundled Python 3.9.7. With output disabled, the user
confirmed first-stroke delivery in a new job and subsequent live strokes in
Paper Tape. USB unplug/replug followed by Plover's Reconnect also delivered
new strokes without replaying the existing job's history. The November 15 hardware results
used the legacy driver with the earlier reading fixes; the legacy path has
automated coverage in this revision but has not been retested on hardware.

## macOS (Stenograph USB)

The plugin first uses **libusb 1.0** supplied by **`libusb-package`**. Published
macOS wheels provide the library for Intel and Apple Silicon without a separate
Homebrew installation. Unlike the previous `pyusb_libusb1_backend` extension,
these wheels are not tied to a particular CPython minor version.

Install the plugin with the same architecture that runs Plover. The dependency's
macOS wheels each contain one architecture; switching between native Apple
Silicon and Rosetta execution in the same plugin directory can require
force-reinstalling `libusb-package` for the new architecture; see the commands
below. An ordinary install or upgrade can keep the wrong-architecture library
because pip considers the installed package version already satisfied.

If the packaged library is missing or cannot load, the plugin logs the failure
and tries system libusb. A source installation of `libusb-package` can succeed
without building its native library, so a successful pip install alone does not
prove that USB support is available. As a fallback, install libusb yourself:

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

### Switching between Apple Silicon and Rosetta

Quit Plover before replacing the library. Set the plugin location and use the
Python inside your Plover application (adjust the application path if needed):

```bash
export PYTHONUSERBASE="$HOME/Library/Application Support/plover/plugins/mac"
PY="/Applications/Plover.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
```

Choose **one** command matching how you will run Plover. The architecture of your
terminal is not necessarily the architecture of Plover.

For native Apple Silicon Plover:

```bash
arch -arm64 "$PY" -m pip install --user --force-reinstall --no-cache-dir --only-binary=:all: 'libusb-package>=1.0.30.0'
```

For Intel Plover, or Apple Silicon Plover running with **Open using Rosetta**:

```bash
arch -x86_64 "$PY" -m pip install --user --force-reinstall --no-cache-dir --only-binary=:all: 'libusb-package>=1.0.30.0'
```

Restart Plover in the chosen architecture. These commands replace the library
and its Python dependencies in Plover's user plugin directory; they do not
modify the application bundle. `--only-binary=:all:` requires a published wheel
instead of accepting a source build that might omit libusb. Switching back to
the other architecture requires running the corresponding command again.

## Testing

Use an isolated environment; these tests do not need Plover or a writer:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --only-binary=:all: -r tests/requirements.txt
python -m build --no-isolation
python -m pip install --no-deps --force-reinstall dist/*.whl
python -m unittest discover -s tests -v
```

On macOS, also run `python tests/smoke_macos_backend.py` in a fresh process. This
loads the packaged libusb through the real resolver while forbidding system
library discovery. It checks the actual loaded image path and never opens a
writer. CI runs both checks against the installed wheel, outside the checkout,
on Intel and Apple Silicon macOS, plus unit tests on Linux and Windows.

Before releasing, also validate an installed plugin inside the signed Plover
application launched through Finder on a machine without system libusb. Check
both architectures and an upgrade from the old backend. With a writer, check
connection before a realtime file is open, writing, unplug/reconnect, and starting
a new writer job. The interpreter smoke test does not establish signed-app
loading or hardware correctness.

## Releasing

Update `version` in `setup.cfg` and commit it before tagging a release. The
release script does not update the package version.

Cut a release with `scripts/release.sh <version>`, e.g.:

```bash
./scripts/release.sh v2.1.1
```

The version must start with `v`. The script creates an annotated git tag,
pushes it, and creates a GitHub release with auto-generated notes, which
triggers the `publish` workflow to upload the sdist to PyPI via trusted
publishing.
