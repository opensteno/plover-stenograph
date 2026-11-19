class ConnectionError(Exception):
    """We could not connect to the writer."""
    pass


class MissingUsbBackendError(Exception):
    """PyUSB could not load libusb (host dependency missing or not discoverable)."""
    pass


class ProtocolViolationException(ConnectionError):
    """The writer did something unexpected"""
    pass


class UnableToPerformRequestException(ConnectionError):
    """The writer cannot perform the action requested"""
    pass


class FileNotAvailableException(ConnectionError):
    """The writer cannot read from the current file"""
    pass


class NoRealtimeFileException(Exception):
    """The realtime file doesn't exist, likely because the user hasn't started writing"""
    def __init__(self, confirmed_open=False):
        super().__init__("Writer has no realtime file")
        # Only a correlated Open reply can establish that the next file is new.
        self.confirmed_open = confirmed_open


class FinishedReadingClosedFileException(Exception):
    """The closed file being read is complete and cannot be read further"""
    pass
