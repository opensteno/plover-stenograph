from stenograph.packet import ErrorType, PacketType
from stenograph.exception import *

class MachineTransport:
    """Simple interface to connect with and send data to a Stenograph machine"""

    def connect(self):
        """Connect to machine, raise an exception if an error occurred"""
        raise NotImplementedError('connect() is not implemented')

    def disconnect(self):
        """Disconnect from the machine"""
        raise NotImplementedError('disconnect() is not implemented')

    def send_receive(self, request):
        """Send a StenoPacket to the machine and return the response"""
        raise NotImplementedError('send_receive() is not implemented')

    def handle_response(self, response, request=None):
        """Read the response, and raise an exception if an error occurred"""
        if request is not None:
            if response.sequence_number != request.sequence_number and not (
                response.is_error and response.sequence_number == 0
            ):
                raise ProtocolViolationException("Unexpected writer sequence")
            if response.packet_type != request.packet_type and not response.is_error:
                raise ProtocolViolationException("Unexpected writer response type")
        if response.is_error:
            try:
                error_type = ErrorType(response.p1)
            except ValueError:
                raise ProtocolViolationException("Unknown writer status: %s" % response.p1)
            if error_type == ErrorType.UNABLE_TO_PERFORM:
                raise UnableToPerformRequestException("Writer refused request (status 3)")
            elif error_type == ErrorType.FILE_NOT_AVAILABLE:
                raise FileNotAvailableException("Writer file is unavailable (status 7)")
            elif error_type == ErrorType.NO_REALTIME_FILE:
                raise NoRealtimeFileException(confirmed_open=(
                    request is not None
                    and request.packet_type == PacketType.OPEN_FILE
                    and response.sequence_number == request.sequence_number
                ))
            elif error_type == ErrorType.FINISHED_READING_CLOSED_FILE:
                raise FinishedReadingClosedFileException
        return response
