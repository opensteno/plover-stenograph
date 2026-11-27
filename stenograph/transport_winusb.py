"""Stenograph's WinUSB driver, using only Windows-supplied DLLs."""
import ctypes
from ctypes import wintypes
import uuid

from stenograph import transport_windows as native
from stenograph.exception import ConnectionError
from stenograph.response import read_response
from stenograph.transport import MachineTransport

USB_WRITER_GUID = native.GUID(*uuid.UUID('a16fb5b8-e015-4fb6-bb7a-9cc12e6896ad').bytes_le)
FILE_FLAG_OVERLAPPED = 0x40000000
PIPE_TRANSFER_TIMEOUT = 3
USB_BULK_PIPE = 2
TIMEOUT_MS = 3000


class InterfaceDescriptor(ctypes.Structure):
    _pack_ = 1
    _fields_ = [(name, ctypes.c_ubyte) for name in (
        'length', 'type', 'number', 'alternate', 'endpoints',
        'interface_class', 'subclass', 'protocol', 'string_index')]


class PipeInformation(ctypes.Structure):
    _fields_ = [('type', ctypes.c_int), ('id', ctypes.c_ubyte),
                ('max_packet', ctypes.c_ushort), ('interval', ctypes.c_ubyte)]


def _bind(name, args):
    function = getattr(ctypes.windll.winusb, name)
    function.argtypes = args
    function.restype = wintypes.BOOL
    return function


Initialize = _bind('WinUsb_Initialize', [wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE)])
Free = _bind('WinUsb_Free', [wintypes.HANDLE])
QueryInterface = _bind('WinUsb_QueryInterfaceSettings', [
    wintypes.HANDLE, ctypes.c_ubyte, ctypes.POINTER(InterfaceDescriptor)])
QueryPipe = _bind('WinUsb_QueryPipe', [
    wintypes.HANDLE, ctypes.c_ubyte, ctypes.c_ubyte, ctypes.POINTER(PipeInformation)])
SetPipePolicy = _bind('WinUsb_SetPipePolicy', [
    wintypes.HANDLE, ctypes.c_ubyte, wintypes.ULONG, wintypes.ULONG, wintypes.LPVOID])
ReadPipe = _bind('WinUsb_ReadPipe', [
    wintypes.HANDLE, ctypes.c_ubyte, wintypes.LPVOID, wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG), wintypes.LPVOID])
WritePipe = _bind('WinUsb_WritePipe', [
    wintypes.HANDLE, ctypes.c_ubyte, wintypes.LPVOID, wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG), wintypes.LPVOID])


def _check(result, operation):
    if not result:
        raise ConnectionError('%s: %s' % (operation, ctypes.WinError()))


class WinUsbTransport(MachineTransport):
    def __init__(self):
        super().__init__()
        self._device = native.INVALID_HANDLE_VALUE
        self._interface = wintypes.HANDLE()
        self._input = self._output = None
        self._packet_size = None
        self._pending = bytearray()

    def connect(self):
        self.disconnect()
        self._device = native.WindowsUsbTransport._open_device_by_class_interface_and_instance(
            USB_WRITER_GUID, native.FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED)
        if self._device == native.INVALID_HANDLE_VALUE:
            return False
        try:
            _check(Initialize(self._device, ctypes.byref(self._interface)), 'WinUsb_Initialize')
            descriptor = InterfaceDescriptor()
            _check(QueryInterface(self._interface, 0, ctypes.byref(descriptor)),
                   'WinUsb_QueryInterfaceSettings')
            inputs, outputs = [], []
            for index in range(descriptor.endpoints):
                pipe = PipeInformation()
                _check(QueryPipe(self._interface, 0, index, ctypes.byref(pipe)), 'WinUsb_QueryPipe')
                if pipe.type == USB_BULK_PIPE:
                    if pipe.id & 0x80:
                        inputs.append((pipe.id, pipe.max_packet))
                    else:
                        outputs.append(pipe.id)
            if len(inputs) != 1 or len(outputs) != 1:
                raise ConnectionError('Expected one Stenograph bulk input and output pipe')
            self._input, self._packet_size = inputs[0]
            self._output = outputs[0]
            if not 0 < self._packet_size <= 1024:
                raise ConnectionError('Invalid writer USB packet size')
            # Reads set their timeout from the remaining response deadline.
            self._set_timeout(self._output, TIMEOUT_MS)
            return True
        except Exception:
            self.disconnect()
            raise

    def _set_timeout(self, pipe, milliseconds):
        timeout = wintypes.ULONG(milliseconds)
        _check(SetPipePolicy(self._interface, pipe, PIPE_TRANSFER_TIMEOUT,
                             ctypes.sizeof(timeout), ctypes.byref(timeout)), 'WinUsb_SetPipePolicy')

    def disconnect(self):
        interface, self._interface = self._interface, wintypes.HANDLE()
        device, self._device = self._device, native.INVALID_HANDLE_VALUE
        self._input = self._output = None
        self._packet_size = None
        self._pending.clear()
        try:
            if interface.value:
                _check(Free(interface), 'WinUsb_Free')
        finally:
            if device != native.INVALID_HANDLE_VALUE:
                _check(native.CloseHandle(device), 'CloseHandle')

    def _write(self, data):
        buffer = ctypes.create_string_buffer(data)
        written = wintypes.ULONG()
        _check(WritePipe(self._interface, self._output, buffer, len(data),
                         ctypes.byref(written), None), 'WinUsb_WritePipe')
        if written.value != len(data):
            raise ConnectionError('Short WinUSB write')

    def _read(self, capacity, timeout):
        self._set_timeout(self._input, max(1, int(timeout * 1000)))
        buffer = ctypes.create_string_buffer(capacity)
        count = wintypes.ULONG()
        _check(ReadPipe(self._interface, self._input, buffer, len(buffer),
                        ctypes.byref(count), None), 'WinUsb_ReadPipe')
        return buffer.raw[:count.value]

    def send_receive(self, request):
        if not self._interface.value:
            raise ConnectionError('WinUSB device is not open')
        self._write(request.pack())
        response = read_response(request, self._read, self._pending,
                                 self._packet_size, timeout=TIMEOUT_MS / 1000)
        return self.handle_response(response, request)
