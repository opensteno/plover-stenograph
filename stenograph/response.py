"""Read one matching response without mistaking USB transfers for frames."""

from time import monotonic

from stenograph.exception import ConnectionError, ProtocolViolationException
from stenograph.packet import StenoPacket


MAX_SKIPPED_RESPONSES = 16


def read_response(request, read, pending, packet_size, timeout=3.0):
    """Accumulate bounded frames within one deadline.

    ``read(size, timeout_seconds)`` returns bytes. Keep trailing bytes for the
    next exchange; a timeout or malformed frame requires transport reconnect.
    Read one endpoint packet for the header, then only the remaining payload
    rounded to whole USB packets. A large speculative read can wait for a ZLP
    that the writer never sends, even after the complete response has arrived.
    """
    started = monotonic()
    skipped = 0
    while True:
        remaining = timeout - (monotonic() - started)
        if remaining < 0.001:
            raise ConnectionError("Timed out awaiting writer response")
        if len(pending) >= StenoPacket.HEADER_SIZE:
            size = StenoPacket.response_size(pending)
            if len(pending) >= size:
                packet = StenoPacket.unpack(pending[:size])
                del pending[:size]
                if packet.sequence_number == request.sequence_number or (
                    packet.is_error and packet.sequence_number == 0
                ):
                    return packet
                skipped += 1
                if skipped > MAX_SKIPPED_RESPONSES:
                    raise ProtocolViolationException("Too many stale writer replies")
                continue
            needed = size - len(pending)
        else:
            needed = packet_size
        capacity = ((needed + packet_size - 1) // packet_size) * packet_size
        chunk = read(capacity, remaining)
        if not chunk:
            skipped += 1
            if skipped > MAX_SKIPPED_RESPONSES:
                raise ConnectionError("Too many empty USB transfers")
        elif len(chunk) > capacity:
            raise ProtocolViolationException("Writer transfer exceeded read capacity")
        pending.extend(chunk)
