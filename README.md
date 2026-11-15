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
