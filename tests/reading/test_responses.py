import struct
import unittest
from collections import deque
from unittest.mock import Mock, patch

from stenograph.exception import (
    ConnectionError, FinishedReadingClosedFileException, NoRealtimeFileException,
    ProtocolViolationException,
)
from stenograph.packet import MAX_READ, PacketType, StenoPacket
from stenograph.response import MAX_SKIPPED_RESPONSES, read_response
from stenograph.transport import MachineTransport


def reply(sequence=7, kind=PacketType.READ_FILE, data=b'', status=0):
    return StenoPacket(sequence_number=sequence, packet_type=kind, data=data, p1=status).pack()


class ResponseTests(unittest.TestCase):
    def setUp(self):
        self.request = StenoPacket(sequence_number=7, packet_type=PacketType.READ_FILE)

    def read_chunks(self, chunks, packet_size=64):
        chunks = deque(chunks)
        def read(size, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 3)
            self.assertEqual(size % packet_size, 0)
            if not chunks:
                self.fail('read again after complete response')
            data = chunks.popleft()
            if len(data) > size:
                chunks.appendleft(data[size:])
            return data[:size]
        return read

    def test_every_header_split_and_full_payload(self):
        for packet_size in (8, 16, 32, 64, 512, 1024):
            for payload in (b'', b'\xc0' * 8, b'\xc0' * 32, b'\xc0' * MAX_READ):
                frame = reply(data=payload)
                for split in range(1, 33):
                    with self.subTest(packet_size=packet_size, size=len(payload), split=split):
                        result = read_response(self.request, self.read_chunks(
                            [frame[:split], frame[split:]], packet_size
                        ), bytearray(), packet_size)
                        self.assertEqual(result.data, payload)

    def test_read_does_not_wait_for_a_missing_zero_length_terminator(self):
        frame = bytearray(reply(data=b'\xc0' * 32))  # exactly one 64-byte USB packet
        def read(size, timeout):
            self.assertEqual(size, 64)
            self.assertEqual(len(frame), 64)
            result = bytes(frame)
            frame.clear()
            return result
        self.assertEqual(read_response(self.request, read, bytearray(), 64).data_length, 32)

    def test_stale_data_and_stale_errors_do_not_change_current_job(self):
        frames = [reply(6), reply(6, PacketType.ERROR, status=9), reply(7)]
        result = read_response(self.request, self.read_chunks(frames), bytearray(), 64)
        self.assertEqual(result.sequence_number, 7)
        self.assertFalse(result.is_error)

    def test_current_and_unsolicited_writer_statuses_reach_handlers(self):
        for sequence in (0, 7):
            for status, expected in ((8, NoRealtimeFileException), (9, FinishedReadingClosedFileException)):
                with self.subTest(sequence=sequence, status=status):
                    result = read_response(self.request, self.read_chunks([
                        reply(sequence, PacketType.ERROR, status=status)
                    ]), bytearray(), 64)
                    with self.assertRaises(expected):
                        MachineTransport().handle_response(result, self.request)

    def test_matching_sequence_with_wrong_kind_is_rejected(self):
        packet = StenoPacket.unpack(reply(7, PacketType.OPEN_FILE))
        with self.assertRaises(ProtocolViolationException):
            MachineTransport().handle_response(packet, self.request)

    def test_unknown_status_is_recoverable(self):
        packet = StenoPacket.unpack(reply(7, PacketType.ERROR, status=123))
        with self.assertRaisesRegex(ConnectionError, 'Unknown writer status: 123'):
            MachineTransport().handle_response(packet, self.request)

    def test_stale_and_empty_transfer_counts_are_bounded(self):
        for frame in (reply(6), b''):
            read = Mock(return_value=frame)
            with self.assertRaises(ConnectionError):
                read_response(self.request, read, bytearray(), 64)
            self.assertEqual(read.call_count, MAX_SKIPPED_RESPONSES + 1)

    def test_partial_reply_does_not_extend_deadline(self):
        read = Mock(side_effect=[reply()[:16], reply()[16:]])
        with patch('stenograph.response.monotonic', side_effect=[10, 10, 11, 13]):
            with self.assertRaisesRegex(ConnectionError, 'Timed out'):
                read_response(self.request, read, bytearray(), 64)
        self.assertEqual([c.args[1] for c in read.call_args_list], [3, 2])

    def test_trailing_bytes_survive_for_next_exchange(self):
        pending = bytearray()
        first = read_response(self.request, self.read_chunks([reply(7) + reply(8)]), pending, 64)
        self.assertEqual(first.sequence_number, 7)
        request = StenoPacket(sequence_number=8, packet_type=PacketType.READ_FILE)
        read = Mock(side_effect=AssertionError('unexpected extra USB read'))
        self.assertEqual(read_response(request, read, pending, 64).sequence_number, 8)
        self.assertFalse(pending)

    def test_bad_headers_are_rejected_before_payload_read(self):
        good = reply()
        bad = [b'XX' + good[2:], good[:6] + b'\xff\xff' + good[8:]]
        for length in (MAX_READ + 8, 0xffffffff):
            bad.append(good[:8] + struct.pack('<I', length) + good[12:])
        for frame in bad:
            with self.subTest(frame=frame):
                read = Mock(return_value=frame)
                with self.assertRaises(ProtocolViolationException):
                    read_response(self.request, read, bytearray(), 64)
                self.assertEqual(read.call_count, 1)

    def test_truncated_frame_is_recoverable(self):
        for frame in (reply(data=b'\xc0' * 8)[:-1], reply()[:16]):
            with self.assertRaises(ProtocolViolationException):
                StenoPacket.unpack(frame)

    def test_sequence_wrap_reserves_zero(self):
        with patch.object(StenoPacket, 'sequence_number', 0xffffffff):
            self.assertEqual(StenoPacket.make_open_request().sequence_number, 0xffffffff)
            self.assertEqual(StenoPacket.make_open_request().sequence_number, 1)

    def test_only_matching_open_status_proves_file_absence(self):
        for kind in (PacketType.OPEN_FILE, PacketType.READ_FILE):
            for sequence in (0, 7):
                request = StenoPacket(sequence_number=7, packet_type=kind)
                response = StenoPacket.unpack(reply(sequence, PacketType.ERROR, status=8))
                with self.assertRaises(NoRealtimeFileException) as caught:
                    MachineTransport().handle_response(response, request)
                self.assertEqual(caught.exception.confirmed_open,
                                 kind == PacketType.OPEN_FILE and sequence == 7)

    def test_nonstroke_history_and_partial_record_are_not_framing_errors(self):
        frame = reply(data=b'\0' * 8)
        self.assertEqual(StenoPacket.unpack(frame).data, b'\0' * 8)
        partial = frame[:8] + struct.pack('<I', 3) + frame[12:35]
        self.assertEqual(StenoPacket.unpack(partial).data_length, 3)
