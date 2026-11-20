import unittest
from collections import deque
from unittest.mock import Mock

from plover_stenograph.base import StenographMachine
from stenograph.exception import (
    ConnectionError, FinishedReadingClosedFileException, NoRealtimeFileException,
    ProtocolViolationException,
)
from stenograph.packet import PacketType, StenoPacket

OPEN = PacketType.OPEN_FILE
READ = PacketType.READ_FILE
STOP = object()


def packet(keys=False):
    # S- plus a timestamp, encoded like a writer stroke.
    return StenoPacket(sequence_number=1, packet_type=READ,
                       data=b'\xc8\xc0\xc0\xc0\x01\0\0\0' if keys else b'')


class Event:
    """Deterministic stop event: record polling waits without sleeping."""
    def __init__(self):
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, timeout):
        self.waits.append(timeout)
        return self.stopped


class ReadingTests(unittest.TestCase):
    def run_script(self, script, notify=None):
        script = deque(script)
        transport = Mock()
        machine = StenographMachine(transport, {})
        machine.finished = Event()
        delivered = []
        machine.add_stroke_callback(notify or delivered.append)
        states = []
        machine.add_state_callback(states.append)

        def exchange(request):
            self.assertTrue(script, 'unexpected extra writer command')
            kind, offset, response = script.popleft()
            self.assertEqual((request.packet_type, request.p1), (kind, offset))
            if response is STOP:
                machine.finished.set()
                return packet()
            if isinstance(response, Exception):
                raise response
            return response
        transport.send_receive.side_effect = exchange
        machine.run()
        self.assertFalse(script)
        return machine, transport, delivered, states

    def test_new_file_first_batch_is_delivered_before_empty_read(self):
        machine, transport, delivered, _ = self.run_script([
            (OPEN, 65, NoRealtimeFileException(confirmed_open=True)),
            (OPEN, 65, packet()), (READ, 0, packet(True)),
            (READ, 8, packet()), (READ, 8, packet(True)), (READ, 16, STOP),
        ])
        self.assertEqual(delivered, [['S-'], ['S-']])
        self.assertEqual(machine.finished.waits, [0.1] * 4)
        transport.connect.assert_not_called()
        transport.disconnect.assert_called_once()

    def test_existing_history_is_discarded_until_empty_read(self):
        _, _, delivered, _ = self.run_script([
            (OPEN, 65, packet()), (READ, 0, packet(True)),
            (READ, 8, packet()), (READ, 8, packet(True)), (READ, 16, STOP),
        ])
        self.assertEqual(delivered, [['S-']])

    def test_no_file_polling_is_paced_and_only_announced_once(self):
        machine, transport, delivered, states = self.run_script([
            (OPEN, 65, NoRealtimeFileException(confirmed_open=True)),
            (OPEN, 65, NoRealtimeFileException(confirmed_open=True)), (OPEN, 65, STOP),
        ])
        self.assertEqual(machine.finished.waits, [0.1, 0.1])
        self.assertEqual(delivered, [])
        # STOP arriving during Open must not publish a new connected state.
        self.assertEqual(len(states), 1)
        transport.connect.assert_not_called()

    def test_closed_job_reopens_and_no_file_starts_new_live_job(self):
        _, transport, delivered, _ = self.run_script([
            (OPEN, 65, packet()), (READ, 0, packet()),
            (READ, 0, FinishedReadingClosedFileException()),
            (OPEN, 65, NoRealtimeFileException(confirmed_open=True)),
            (OPEN, 65, packet()), (READ, 0, packet(True)), (READ, 8, STOP),
        ])
        self.assertEqual(delivered, [['S-']])
        transport.connect.assert_not_called()

    def test_reconnect_clears_no_file_evidence_and_does_not_replay_history(self):
        _, transport, delivered, _ = self.run_script([
            (OPEN, 65, NoRealtimeFileException(confirmed_open=True)),
            (OPEN, 65, ConnectionError('unplugged')),
            (OPEN, 65, packet()), (READ, 0, packet(True)),
            (READ, 8, packet()), (READ, 8, packet(True)), (READ, 16, STOP),
        ])
        self.assertEqual(delivered, [['S-']])
        transport.connect.assert_called_once()
        self.assertEqual(transport.disconnect.call_count, 2)

    def test_protocol_failure_reconnects_instead_of_ending_thread(self):
        _, transport, _, _ = self.run_script([
            (OPEN, 65, ProtocolViolationException('bad frame')),
            (OPEN, 65, packet()), (READ, 0, STOP),
        ])
        transport.connect.assert_called_once()

    def test_callback_failure_still_releases_device(self):
        transport = Mock()
        machine = StenographMachine(transport, {})
        machine.finished = Event()
        transport.send_receive.side_effect = [packet(), packet(), packet(True)]
        machine.add_stroke_callback(Mock(side_effect=RuntimeError('consumer failed')))
        with self.assertRaisesRegex(RuntimeError, 'consumer failed'):
            machine.run()
        transport.disconnect.assert_called_once()

    def test_stop_interrupts_no_file_wait_without_another_request(self):
        transport = Mock()
        machine = StenographMachine(transport, {})
        machine.finished = Event()
        def wait(timeout):
            machine.finished.set()
            return True
        machine.finished.wait = wait
        transport.send_receive.side_effect = NoRealtimeFileException
        machine.run()
        transport.send_receive.assert_called_once()
        transport.disconnect.assert_called_once()

    def test_read_no_file_is_not_evidence_to_replay_history(self):
        _, _, delivered, _ = self.run_script([
            (OPEN, 65, packet()), (READ, 0, packet()),
            (READ, 0, NoRealtimeFileException()),
            (OPEN, 65, packet()), (READ, 0, packet(True)),
            (READ, 8, packet()), (READ, 8, packet(True)), (READ, 16, STOP),
        ])
        self.assertEqual(delivered, [['S-']])

    def test_uncorrelated_open_status_does_not_replay_existing_job(self):
        _, _, delivered, _ = self.run_script([
            (OPEN, 65, NoRealtimeFileException()),
            (OPEN, 65, packet()), (READ, 0, packet(True)),
            (READ, 8, packet()), (READ, 8, STOP),
        ])
        self.assertEqual(delivered, [])

    def test_malformed_history_is_discarded_without_reconnect(self):
        bad = packet(True)
        bad.data = b'\0' * 8
        _, transport, delivered, _ = self.run_script([
            (OPEN, 65, packet()), (READ, 0, bad), (READ, 8, packet()),
            (READ, 8, packet(True)), (READ, 16, STOP),
        ])
        self.assertEqual(delivered, [['S-']])
        transport.connect.assert_not_called()

    def test_bad_live_record_is_logged_without_losing_neighboring_strokes(self):
        batch = packet(True)
        batch.data = batch.data + b'\0' * 8 + batch.data
        batch.data_length = 24
        with self.assertLogs('plover', level='WARNING') as logs:
            _, transport, delivered, _ = self.run_script([
                (OPEN, 65, packet()), (READ, 0, packet()),
                (READ, 0, batch), (READ, 24, batch), (READ, 48, STOP),
            ])
        self.assertEqual(delivered, [['S-']] * 4)
        self.assertEqual(len(logs.output), 1)
        self.assertIn('Skipping malformed', logs.output[0])
        transport.connect.assert_not_called()

    def test_record_split_across_replies_and_empty_poll_is_preserved(self):
        record = packet(True).data
        first, last = packet(), packet()
        first.data, first.data_length = record[:3], 3
        last.data, last.data_length = record[3:], 5
        _, _, delivered, _ = self.run_script([
            (OPEN, 65, NoRealtimeFileException(confirmed_open=True)),
            (OPEN, 65, packet()), (READ, 0, first),
            (READ, 3, packet()), (READ, 3, last), (READ, 8, STOP),
        ])
        self.assertEqual(delivered, [['S-']])

    def test_connection_is_not_announced_until_initial_catchup_finishes(self):
        transport = Mock()
        machine = StenographMachine(transport, {})
        machine.finished = Event()
        states = []
        machine.add_state_callback(states.append)
        replies = iter([packet(), packet(True), packet()])
        def exchange(request):
            try:
                response = next(replies)
                self.assertEqual(states, [])
                return response
            except StopIteration:
                self.assertEqual(len(states), 1)
                machine.finished.set()
                return packet()
        transport.send_receive.side_effect = exchange
        machine.run()
