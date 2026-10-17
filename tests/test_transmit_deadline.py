"""LoRaHandler.transmit() gives up inside its deadline, below the client's RPC timeout.

A client that timed out first deferred the packet to store-and-forward while the
daemon kept trying, so the gateway could get it twice. These run transmit()
against a fake mDot on a virtual clock (time.sleep advances it).
"""
import types

import pytest

import tools.lora_handler_concurrent as lhc


class _Clock:
    def __init__(self):
        self.t = 1_000.0

    def time(self):
        return self.t

    monotonic = time

    def sleep(self, s):
        self.t += max(s, 0.001)


class _FakeMdot:
    """Answers AT+SENDB=<data> with mac_reply lines (or nothing); the flush probe
    (AT+SENDB=00) never gets an answer. Each read costs read_s of virtual time."""

    def __init__(self, clock, mac_reply=None, read_s=0.05):
        self.clock, self.mac_reply, self.read_s = clock, mac_reply, read_s
        self.lines, self.writes, self.is_open = [], [], True

    @property
    def in_waiting(self):
        return len(self.lines)

    def write(self, data):
        self.writes.append(data)
        if data.startswith(b"AT+SENDB=") and data != b"AT+SENDB=00\r\n" and self.mac_reply:
            self.lines.extend(line.encode() + b"\r\n" for line in self.mac_reply)

    def read_until(self, *a, **k):
        self.clock.sleep(self.read_s)
        return self.lines.pop(0) if self.lines else b""

    def reset_input_buffer(self):
        self.lines.clear()

    def reset_output_buffer(self):
        pass


@pytest.fixture
def clocked(monkeypatch, _mock_serial_port):
    clock = _Clock()
    fake_time = types.SimpleNamespace(time=clock.time, monotonic=clock.monotonic, sleep=clock.sleep,
                                      strftime=lambda *a: "")
    handler = lhc.LoRaHandler()
    monkeypatch.setattr(lhc, "time", fake_time)
    return handler, clock


def test_deadline_is_inside_the_client_timeout():
    assert lhc.TRANSMIT_DEADLINE_S + 10 <= lhc.LoRaHandlerClient._LONG_TIMEOUT_S


def test_silent_mdot_gives_up_by_the_deadline(clocked):
    handler, clock = clocked
    handler.ser = _FakeMdot(clock)
    start = clock.t
    assert handler.transmit(b"\x01\x02", max_retries=10) is False
    elapsed = clock.t - start
    assert elapsed <= lhc.TRANSMIT_DEADLINE_S
    sends = [w for w in handler.ser.writes if w.startswith(b"AT+SENDB=")]
    assert len(sends) == 3      # 20 s windows: a fourth wouldn't fit in 75 s


def test_mac_flush_rounds_stay_inside_the_deadline(clocked):
    handler, clock = clocked
    handler.ser = _FakeMdot(clock, mac_reply=["Tx buffer filled by MAC Commands, send data again", "ERROR"])
    start = clock.t
    assert handler.transmit(b"\x01\x02") is False
    # without the deadline: 3 attempts + up to 3 x 20 s flush rounds after each of two = 140 s+
    assert clock.t - start <= lhc.TRANSMIT_DEADLINE_S
    assert b"AT+SENDB=00\r\n" in handler.ser.writes


def test_held_uart_lock_fails_inside_the_deadline(clocked, monkeypatch):
    handler, clock = clocked
    handler.ser = _FakeMdot(clock)

    def lockf(fd, op):
        if op & lhc.fcntl.LOCK_NB:
            raise BlockingIOError("held")

    monkeypatch.setattr(lhc.fcntl, "lockf", lockf)
    start = clock.t
    assert handler.transmit(b"\x01") is False
    assert clock.t - start <= lhc.TRANSMIT_DEADLINE_S
    assert handler.ser.writes == []     # never handed to the mDot


def test_ok_still_sends(clocked):
    handler, clock = clocked
    handler.ser = _FakeMdot(clock, mac_reply=["OK"])
    assert handler.transmit(b"\x01") is True
