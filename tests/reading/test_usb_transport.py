import unittest
from unittest.mock import MagicMock, Mock, patch

from usb import core
from stenograph.exception import ConnectionError, NoRealtimeFileException
from stenograph.packet import PacketType, StenoPacket
from stenograph.transport_libusb import LibusbTransport


class UsbTests(unittest.TestCase):
    def setUp(self):
        with patch('stenograph.transport_libusb.resolve_pyusb_backend', return_value=object()):
            self.transport = LibusbTransport()
        self.input = Mock(bEndpointAddress=0x81, bmAttributes=2, wMaxPacketSize=64)
        self.output = Mock(bEndpointAddress=0x02, bmAttributes=2)
        self.interface = MagicMock(bInterfaceNumber=0)
        self.interface.__iter__.side_effect = lambda: iter([self.input, self.output])
        self.device = MagicMock()
        self.device.get_active_configuration.return_value.__getitem__.return_value = self.interface
        self.find = self.enter_patch('core.find', return_value=self.device)
        self.configuration = self.enter_patch('control.get_configuration', return_value=1)
        self.claim = self.enter_patch('util.claim_interface')
        self.dispose = self.enter_patch('util.dispose_resources')

    def enter_patch(self, target, **kwargs):
        patcher = patch('stenograph.transport_libusb.' + target, **kwargs)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def test_configured_writer_is_claimed_without_resetting_it(self):
        self.transport.connect()
        self.device.set_configuration.assert_not_called()
        self.claim.assert_called_once_with(self.device, 0)
        self.transport.disconnect()
        self.dispose.assert_called_once_with(self.device)

    def test_unconfigured_writer_is_initialized(self):
        self.configuration.return_value = 0
        self.transport.connect()
        self.device.set_configuration.assert_called_once_with()
        self.claim.assert_called_once_with(self.device, 0)

    def test_permission_error_does_not_trigger_configuration_reset(self):
        self.configuration.side_effect = core.USBError('Access denied')
        with self.assertRaises(core.USBError):
            self.transport.connect()
        self.device.set_configuration.assert_not_called()
        self.claim.assert_not_called()
        self.dispose.assert_called_once_with(self.device)

    def test_missing_bulk_pair_and_claim_failure_release_resources(self):
        for fail in ('endpoint', 'claim'):
            with self.subTest(fail=fail):
                self.dispose.reset_mock()
                if fail == 'endpoint':
                    self.input.bmAttributes = 3  # interrupt, not bulk
                else:
                    self.input.bmAttributes = 2
                    self.claim.side_effect = core.USBError('Busy')
                with self.assertRaises((ConnectionError, core.USBError)):
                    self.transport.connect()
                self.dispose.assert_called_once_with(self.device)
                self.assertFalse(self.transport._connected)

    def test_status_packet_is_handled_through_real_usb_exchange(self):
        self.transport.connect()
        request = StenoPacket.make_open_request()
        self.output.write.return_value = len(request.pack())
        self.input.read.return_value = StenoPacket(
            sequence_number=request.sequence_number, packet_type=PacketType.ERROR, p1=8,
        ).pack()
        with self.assertRaises(NoRealtimeFileException):
            self.transport.send_receive(request)
        self.assertEqual(self.input.read.call_args.args[0], 64)

    def test_short_write_never_starts_read(self):
        self.transport.connect()
        self.output.write.return_value = 1
        with self.assertRaisesRegex(ConnectionError, 'Incomplete request'):
            self.transport.send_receive(StenoPacket.make_open_request())
        self.input.read.assert_not_called()

    def test_disconnect_discards_partial_frame(self):
        self.transport.connect()
        self.transport._pending.extend(b'SGpartial')
        self.transport.disconnect()
        self.assertFalse(self.transport._pending)
        self.assertIsNone(self.transport._endpoint_in)
        self.assertIsNone(self.transport._endpoint_out)

    def test_stall_clears_only_failing_endpoint_before_reconnect(self):
        import errno
        self.transport.connect()
        request = StenoPacket.make_open_request()
        for endpoint in (self.output, self.input):
            with self.subTest(endpoint=endpoint):
                self.device.clear_halt.reset_mock()
                self.output.write.side_effect = None
                self.output.write.return_value = len(request.pack())
                self.input.read.side_effect = None
                operation = endpoint.write if endpoint is self.output else endpoint.read
                operation.side_effect = core.USBError('Pipe error', errno=errno.EPIPE)
                with self.assertRaises(ConnectionError):
                    self.transport.send_receive(request)
                self.device.clear_halt.assert_called_once_with(endpoint.bEndpointAddress)

    def test_timeout_does_not_clear_halt_or_reset_device(self):
        import errno
        self.transport.connect()
        request = StenoPacket.make_open_request()
        self.output.write.return_value = len(request.pack())
        self.input.read.side_effect = core.USBError('Timeout', errno=errno.ETIMEDOUT)
        with self.assertRaises(ConnectionError):
            self.transport.send_receive(request)
        self.device.clear_halt.assert_not_called()
        self.device.reset.assert_not_called()
        self.device.set_configuration.assert_not_called()
