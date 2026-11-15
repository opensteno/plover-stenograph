import errno

from usb import control, core, util

from stenograph.response import read_response
from stenograph.libusb_backend import resolve_pyusb_backend
from stenograph.transport import MachineTransport
from stenograph.exception import (
    ConnectionError,
    MissingUsbBackendError,
)


VENDOR_ID = 0x112b


class LibusbTransport(MachineTransport):

    def __init__(self):
        super().__init__()
        self._usb_device = None
        self._endpoint_in = None
        self._endpoint_out = None
        self._connected = False
        self._pending = bytearray()
        self._backend = resolve_pyusb_backend()

    def connect(self):
        """Attempt to and return connection"""
        # Disconnect device if it's already connected.
        if self._connected:
            self.disconnect()

        if self._backend is None:
            raise MissingUsbBackendError(
                "No USB backend (libusb-1.0) available. "
                "On macOS, install libusb (for example: brew install libusb). "
                "If it is already installed, ensure Plover can load it — "
                "see https://github.com/opensteno/plover/issues/1841"
            )

        # Find the device by the vendor ID.
        usb_device = core.find(backend=self._backend, idVendor=VENDOR_ID)
        if not usb_device:  # Device not found
            raise ConnectionError("USB device not connected")

        try:
            # SET_CONFIGURATION resets endpoint state even when unchanged.
            # Some writers enumerate unconfigured and still need activation.
            if control.get_configuration(usb_device) == 0:
                usb_device.set_configuration()
            config = usb_device.get_active_configuration()
            interface = config[(0, 0)]
            endpoints = {
                util.endpoint_direction(endpoint.bEndpointAddress): endpoint
                for endpoint in interface
                if util.endpoint_type(endpoint.bmAttributes) == util.ENDPOINT_TYPE_BULK
            }
            endpoint_out = endpoints.get(util.ENDPOINT_OUT)
            endpoint_in = endpoints.get(util.ENDPOINT_IN)
            if endpoint_out is None or endpoint_in is None:
                raise ConnectionError("Writer interface has no bulk IN/OUT pair")
            if not 0 < endpoint_in.wMaxPacketSize <= 1024:
                raise ConnectionError("Invalid writer USB packet size")
            util.claim_interface(usb_device, interface.bInterfaceNumber)
        except Exception:
            util.dispose_resources(usb_device)
            raise

        self._usb_device = usb_device
        self._endpoint_in = endpoint_in
        self._endpoint_out = endpoint_out
        self._pending.clear()
        self._connected = True

    def disconnect(self):
        self._pending.clear()
        self._connected = False
        if self._usb_device:
            util.dispose_resources(self._usb_device)
        self._usb_device = None
        self._endpoint_in = None
        self._endpoint_out = None

    def send_receive(self, request):
        assert self._connected, 'cannot read from machine if not connected'
        endpoint = self._endpoint_out
        try:
            data = request.pack()
            if self._endpoint_out.write(data, 3000) != len(data):
                raise ConnectionError("Incomplete request sent to writer")
            endpoint = self._endpoint_in
            response = read_response(
                request,
                lambda size, timeout: self._endpoint_in.read(size, max(1, int(timeout * 1000))),
                self._pending,
                self._endpoint_in.wMaxPacketSize,
            )
        except core.USBError as error:
            if error.errno == errno.EPIPE:
                # Recover an explicitly halted endpoint, without resetting the
                # device or clearing healthy endpoints on every reconnect.
                try:
                    self._usb_device.clear_halt(endpoint.bEndpointAddress)
                except core.USBError as recovery_error:
                    raise ConnectionError(
                        "USB exchange failed: %s; clearing endpoint halt failed: %s"
                        % (error, recovery_error)
                    ) from recovery_error
            raise ConnectionError("USB exchange failed: %s" % error) from error
        return self.handle_response(response, request)
