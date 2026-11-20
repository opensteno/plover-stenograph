import ctypes
import sys
import unittest
from unittest.mock import patch
from stenograph.packet import StenoPacket
from stenograph.exception import NoRealtimeFileException, FinishedReadingClosedFileException


@unittest.skipUnless(sys.platform == 'win32', 'Windows native WinUSB transport')
class WinUsbTests(unittest.TestCase):
    def setUp(self):
        from stenograph import transport_winusb as w
        self.w = w
        self.transport = w.WinUsbTransport()
        self.transport._packet_size = 512

    def configure(self, handle, alternate, pointer):
        ctypes.cast(pointer, ctypes.POINTER(self.w.InterfaceDescriptor)).contents.endpoints = 2
        return True

    def pipe(self, handle, alternate, index, pointer):
        pipe = ctypes.cast(pointer, ctypes.POINTER(self.w.PipeInformation)).contents
        pipe.type = self.w.USB_BULK_PIPE
        pipe.id = (0x81, 0x02)[index]
        pipe.max_packet = 512
        return True

    def initialize(self, handle, pointer):
        ctypes.cast(pointer, ctypes.POINTER(self.w.wintypes.HANDLE)).contents.value = 456
        return True

    def test_connect_discovers_pipes_and_sets_bounded_io(self):
        w = self.w
        with patch.object(w.native.WindowsUsbTransport, '_open_device_by_class_interface_and_instance',
                          return_value=123) as open_device, \
                patch.object(w, 'Initialize', side_effect=self.initialize), \
                patch.object(w, 'QueryInterface', side_effect=self.configure), \
                patch.object(w, 'QueryPipe', side_effect=self.pipe), \
                patch.object(w, 'SetPipePolicy', return_value=True) as policy:
            self.assertTrue(self.transport.connect())
        self.assertEqual(open_device.call_args.args[1] & w.FILE_FLAG_OVERLAPPED, w.FILE_FLAG_OVERLAPPED)
        self.assertEqual((self.transport._input, self.transport._output), (0x81, 2))
        self.assertEqual([call.args[1] for call in policy.call_args_list], [2])

    def test_initialization_failure_closes_device(self):
        w = self.w
        with patch.object(w.native.WindowsUsbTransport, '_open_device_by_class_interface_and_instance',
                          return_value=123), patch.object(w, 'Initialize', return_value=False), \
                patch.object(w.native, 'CloseHandle', return_value=True) as close:
            with self.assertRaises(w.ConnectionError):
                self.transport.connect()
            self.transport.disconnect()
        close.assert_called_once_with(123)

    def test_pipe_failure_frees_interface_before_device(self):
        w = self.w
        events = []
        with patch.object(w.native.WindowsUsbTransport, '_open_device_by_class_interface_and_instance',
                          return_value=123), patch.object(w, 'Initialize', side_effect=self.initialize), \
                patch.object(w, 'QueryInterface', return_value=False), \
                patch.object(w, 'Free', side_effect=lambda h: events.append('free') or True), \
                patch.object(w.native, 'CloseHandle', side_effect=lambda h: events.append('close') or True):
            with self.assertRaises(w.ConnectionError):
                self.transport.connect()
            self.transport.disconnect()
        self.assertEqual(events, ['free', 'close'])

    def test_no_modern_device_uses_legacy(self):
        w = self.w
        transport = w.native.WindowsUsbTransport()
        with patch.object(w.WinUsbTransport, 'connect', return_value=False), \
                patch.object(transport, '_open_device_by_class_interface_and_instance', return_value=123):
            self.assertTrue(transport.connect())
        self.assertIsNone(transport._winusb)
        self.assertEqual(transport._usb_device, 123)

    def test_modern_failure_is_not_hidden_by_legacy_fallback(self):
        w = self.w
        with patch.object(w.WinUsbTransport, 'connect', side_effect=w.ConnectionError('busy')), \
                patch.object(w.native.WindowsUsbTransport, '_open_device_by_class_interface_and_instance') as legacy:
            with self.assertRaises(w.ConnectionError):
                w.native.WindowsUsbTransport().connect()
        legacy.assert_not_called()

    def test_modern_dispatch_and_disconnect_do_not_use_legacy_io(self):
        from unittest.mock import Mock
        w = self.w
        transport = w.native.WindowsUsbTransport()
        modern = Mock()
        modern.send_receive.return_value = 'reply'
        transport._winusb = modern
        request = StenoPacket.make_open_request()
        with patch.object(w.native, 'ReadFile') as read, \
                patch.object(w.native, 'WriteFile') as write, \
                patch.object(w.native, 'CloseHandle') as close:
            self.assertEqual(transport.send_receive(request), 'reply')
            transport.disconnect()
            transport.disconnect()
        modern.send_receive.assert_called_once_with(request)
        modern.disconnect.assert_called_once_with()
        read.assert_not_called()
        write.assert_not_called()
        close.assert_not_called()

    def test_legacy_setup_info_is_released_when_opening_fails(self):
        w = self.w
        with patch.object(w.native, 'SetupDiGetClassDevs', return_value=789), \
                patch.object(w.native.WindowsUsbTransport, '_open_device_instance',
                             side_effect=w.ConnectionError('open failed')), \
                patch.object(w.native, 'SetupDiDestroyDeviceInfoList') as destroy:
            with self.assertRaises(w.ConnectionError):
                w.native.WindowsUsbTransport._open_device_by_class_interface_and_instance(
                    w.native.USB_WRITER_GUID)
        destroy.assert_called_once_with(789)

    def test_fragmented_response_skips_stale_packet(self):
        w = self.w
        request = StenoPacket.make_open_request()
        reply = StenoPacket(sequence_number=request.sequence_number,
                             packet_type=request.packet_type).pack()
        stale = StenoPacket(sequence_number=0, packet_type=request.packet_type).pack()
        self.transport._interface.value = 456
        with patch.object(self.transport, '_write'), \
                patch.object(self.transport, '_read', side_effect=[stale + reply[:5], reply[5:]]), \
                patch.object(self.transport, 'handle_response', return_value='matched') as handler:
            self.assertEqual(self.transport.send_receive(request), 'matched')
        self.assertEqual(handler.call_args.args[0].sequence_number, request.sequence_number)

    def test_empty_reads_are_bounded(self):
        w = self.w
        self.transport._interface.value = 456
        with patch.object(self.transport, '_write'), \
                patch.object(self.transport, '_read', return_value=b'') as read:
            with self.assertRaises(w.ConnectionError):
                self.transport.send_receive(StenoPacket.make_open_request())
        self.assertEqual(read.call_count, 17)

    def test_unsolicited_statuses_reach_shared_handler(self):
        from stenograph.packet import PacketType
        self.transport._interface.value = 456
        request = StenoPacket.make_open_request()
        for status, exception in [(8, NoRealtimeFileException),
                                  (9, FinishedReadingClosedFileException)]:
            with self.subTest(status=status):
                reply = StenoPacket(sequence_number=0, packet_type=PacketType.ERROR,
                                    p1=status).pack()
                with patch.object(self.transport, '_write'), \
                        patch.object(self.transport, '_read', return_value=reply) as read:
                    with self.assertRaises(exception) as caught:
                        self.transport.send_receive(request)
                read.assert_called_once()
                if status == 8:
                    self.assertFalse(caught.exception.confirmed_open)

    def test_short_write_is_rejected(self):
        with patch.object(self.w, 'WritePipe', return_value=True):
            with self.assertRaises(self.w.ConnectionError):
                self.transport._write(b'hello')

    def test_full_endpoint_packet_never_waits_for_missing_terminator(self):
        w = self.w
        request = StenoPacket.make_read_request(file_offset=0)
        reply = StenoPacket(sequence_number=request.sequence_number,
                             packet_type=request.packet_type, data=b'x' * 480).pack()
        self.assertEqual(len(reply), 512)
        self.transport._interface.value = 456
        self.transport._packet_size = 512

        def read(handle, pipe, buffer, capacity, count, overlapped):
            self.assertEqual(capacity, 512, 'A larger transfer waits for a nonexistent ZLP')
            ctypes.memmove(buffer, reply, len(reply))
            ctypes.cast(count, ctypes.POINTER(w.wintypes.ULONG)).contents.value = len(reply)
            return True

        with patch.object(self.transport, '_write'), \
                patch.object(w, 'SetPipePolicy', return_value=True), \
                patch.object(w, 'ReadPipe', side_effect=read) as native_read:
            response = self.transport.send_receive(request)
        self.assertEqual(response.data, b'x' * 480)
        native_read.assert_called_once()
