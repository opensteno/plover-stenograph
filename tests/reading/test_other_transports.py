import sys
import unittest
from unittest.mock import Mock, patch

from stenograph.exception import NoRealtimeFileException
from stenograph.packet import PacketType, StenoPacket
from stenograph.transport_wifi import WiFiTransport


class WiFiStatusTests(unittest.TestCase):
    def test_no_file_status_reaches_shared_handler(self):
        transport = WiFiTransport()
        transport._connected = True
        transport._sock = Mock()
        request = StenoPacket.make_open_request()
        transport._sock.recv.return_value = StenoPacket(
            sequence_number=request.sequence_number, packet_type=PacketType.ERROR, p1=8,
        ).pack()
        with self.assertRaises(NoRealtimeFileException):
            transport.send_receive(request)


@unittest.skipUnless(sys.platform == 'win32', 'Windows native transport')
class WindowsStatusTests(unittest.TestCase):
    def test_no_file_status_reaches_shared_handler(self):
        from stenograph.transport_windows import WindowsUsbTransport
        transport = WindowsUsbTransport()
        transport._usb_device = 123
        request = StenoPacket.make_open_request()
        response = StenoPacket(
            sequence_number=request.sequence_number, packet_type=PacketType.ERROR, p1=8,
        )
        with patch.object(transport, '_usb_write_packet', return_value=len(request.pack())), patch.object(
            transport, '_usb_read_packet', return_value=response
        ):
            with self.assertRaises(NoRealtimeFileException):
                transport.send_receive(request)

    def test_disconnect_closes_original_handle_once(self):
        from stenograph.transport_windows import INVALID_HANDLE_VALUE, WindowsUsbTransport
        transport = WindowsUsbTransport()
        transport._usb_device = 123
        with patch('stenograph.transport_windows.CloseHandle', return_value=True) as close:
            transport.disconnect()
            transport.disconnect()
        close.assert_called_once_with(123)
        self.assertEqual(transport._usb_device, INVALID_HANDLE_VALUE)
