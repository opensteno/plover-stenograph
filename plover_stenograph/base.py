from plover import log
from plover.machine.base import ThreadedStenotypeBase

from stenograph import *
from stenograph.stroke import Stroke


class StenographMachine(ThreadedStenotypeBase):

    KEYS_LAYOUT = """
        #  #  #  #  #  #  #  #  #  #
        S- T- P- H- * -F -P -L -T -D
        S- K- W- R- * -R -B -G -S -Z
              A- O-   -E -U
        ^
    """
    KEYMAP_MACHINE_TYPE = "Stentura"

    def __init__(self, transport, params):
        super().__init__()
        self._transport = transport

    def _on_stroke(self, keys):
        steno_keys = self.keymap.keys_to_actions(keys)
        if steno_keys:
            self._notify(steno_keys)

    def start_capture(self):
        self.finished.clear()
        self._initializing()
        try:
            self._transport.connect()
        except MissingUsbBackendError as e:
            log.error("%s", e)
            self._error()
        except ConnectionError as e:
            log.error("Stenograph writer is not connected: %s" % e)
            self._error()
        except IOError as e:
            log.error("Lost connection to Stenograph writer: %s" % e)
            self._error()
        except Exception as e:
            log.error("Error connecting to Stenograph writer: %s" % e)
            self._error()
        else:
            # self._ready()
            self.start()

    def _reconnect(self):
        self._error()
        while not self.finished.wait(0.25):
            try:
                self._initializing()
                self._transport.connect()
            except Exception as e:
                log.debug("Stenograph writer exception: %s" % e)
                self._error()
            else:
                break

    def _send_receive(self, request):
        """Send a StenoPacket and return the response or raise exceptions."""
        log.debug("Requesting from Stenograph writer: %s", request)
        response = self._transport.send_receive(request)
        log.debug("Response from Stenograph writer: %s", response)
        return response

    def run(self):
        class ReadState:
            def __init__(self):
                self.realtime = False
                self.realtime_file_open = False
                self.waiting_for_file = False
                self.new_file_expected = False
                self.offset = 0
                self.pending_stroke = b''
                self.bad_record_warning = False

        state = ReadState()
        disconnected = False
        ready = False
        try:
            while not self.finished.is_set():
                try:
                    if not state.realtime_file_open:
                        self._send_receive(StenoPacket.make_open_request())
                        if self.finished.is_set():
                            break
                        state.realtime_file_open = True
                        # A file created after an observed no-file interval is
                        # new writing, including its first nonempty batch.
                        state.realtime = state.new_file_expected
                        if state.realtime and not ready:
                            self._ready()
                            ready = True
                    if self.finished.is_set():
                        break
                    response = self._send_receive(
                        StenoPacket.make_read_request(file_offset=state.offset)
                    )
                except NoRealtimeFileException as error:
                    if self.finished.is_set():
                        break
                    if not state.waiting_for_file:
                        log.debug("Stenograph writer has no realtime file; waiting for a job")
                    if not ready:
                        self._ready()  # An idle writer that answered is connected.
                        ready = True
                    state = ReadState()
                    state.waiting_for_file = True
                    state.new_file_expected = error.confirmed_open
                    if disconnected:
                        log.warning("Stenograph writer reconnected")
                    disconnected = False
                    self.finished.wait(0.10)
                except FinishedReadingClosedFileException:
                    if self.finished.is_set():
                        break
                    state = ReadState()
                    self.finished.wait(0.10)
                except ConnectionError as error:
                    if self.finished.is_set():
                        break
                    if not disconnected:
                        log.warning("Stenograph writer disconnected, attempting to reconnect: %s", error)
                        disconnected = True
                    log.debug("Stenograph writer exception: %s", error)
                    # No-file evidence is scoped to this connection. After a
                    # reconnect, discard existing history until caught up.
                    state = ReadState()
                    ready = False
                    self._transport.disconnect()
                    self._reconnect()
                else:
                    if self.finished.is_set():
                        break
                    if disconnected:
                        log.warning("Stenograph writer reconnected")
                        disconnected = False
                    if response.data_length:
                        state.offset += response.data_length
                        data = state.pending_stroke + response.data
                        complete = len(data) - len(data) % 8
                        state.pending_stroke = data[complete:]
                        if state.realtime:
                            strokes, skipped = Stroke.decode_records(data[:complete])
                            if skipped and not state.bad_record_warning:
                                log.warning(
                                    "Skipping malformed Stenograph stroke records; "
                                    "further warnings suppressed for this file"
                                )
                                state.bad_record_warning = True
                            for stroke in strokes:
                                if self.finished.is_set():
                                    break
                                self._on_stroke(stroke.keys)
                    else:
                        state.realtime = True
                        if not ready:
                            self._ready()
                            ready = True
                    if state.realtime:
                        self.finished.wait(0.10)
        finally:
            self._transport.disconnect()

    def stop_capture(self):
        super().stop_capture()
        self._transport = None
        self._stopped()
