"""Downlinks must survive every AT exchange on the mDot serial port.

The mDot prints each Class C downlink ("PACKET:PORT=1,SIZE=3,DATA=219118") the
moment it arrives, also in the middle of an AT reply. Every line read from the
port goes through LoRaHandler._read_line(), which queues those lines; only the
listener decodes them, outside the serial lock.
"""
import os
import threading
import time
import types
from unittest.mock import MagicMock, patch

import pytest

import tools.lora_daemon as daemon_mod
import tools.lora_handler_concurrent as lhc

PKT = b"PACKET:PORT=1,SIZE=3,DATA=219118\r\n"


class FakeMdot:
    """A serial port: in_waiting/read_until over a byte buffer, optional reply on write."""

    def __init__(self, rx=b"", on_write=None):
        self.buf = bytearray(rx)
        self.writes = []
        self.on_write = on_write
        self.is_open = True

    @property
    def in_waiting(self):
        return len(self.buf)

    def feed(self, data):
        self.buf.extend(data)

    def write(self, data):
        self.writes.append(bytes(data))
        if self.on_write:
            self.on_write(bytes(data), self)

    def read_until(self, *a, **k):
        i = self.buf.find(b"\n")
        n = len(self.buf) if i < 0 else i + 1      # no newline: a timeout returns the part
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def reset_input_buffer(self):
        raise AssertionError("reset_input_buffer would discard a downlink")

    def reset_output_buffer(self):
        pass


class Clock:
    """Virtual time: sleep() advances it and lets the fake mdot react."""

    def __init__(self, ser=None):
        self.t, self.ser, self.at = 1000.0, ser, []

    def time(self):
        return self.t

    monotonic = time

    def sleep(self, s):
        self.t += max(s, 0.001)
        for when, data in list(self.at):
            if self.t >= when:
                self.ser.feed(data)
                self.at.remove((when, data))


def make_handler(ser, monkeypatch=None, clock=None):
    h = lhc.LoRaHandler.__new__(lhc.LoRaHandler)
    h.transmit_lock = threading.Lock()
    h._lock_fd = open(os.devnull, "w")
    h.ser = ser
    h.config = {}
    h.current_size_limit = 242
    h.last_transmission_status = None
    h.transmission_history = []
    h.decoded = []
    h.lock_held_at_decode = []

    def decode(payload):
        h.lock_held_at_decode.append(h.transmit_lock.locked())
        h.decoded.append(payload)
    h.decode = decode
    if clock is not None:
        monkeypatch.setattr(lhc, "time", types.SimpleNamespace(
            time=clock.time, monotonic=clock.monotonic, sleep=clock.sleep, strftime=lambda *a: ""))
    return h


@pytest.fixture(autouse=True)
def no_lockf():
    with patch.object(lhc.fcntl, "lockf"):
        yield


# ── a downlink in the middle of an AT exchange is kept ──────────────────────

def test_link_check_keeps_a_downlink_that_lands_in_the_reply():
    ser = FakeMdot(on_write=lambda d, s: s.feed(b"AT+NLC\r\n" + PKT + b"14,1\r\n\r\nOK\r\n"))
    h = make_handler(ser)
    assert h.link_check() == {"ok": True, "margin_db": 14, "gateways": 1, "error": None}
    assert h.decoded == []                       # not decoded under the AT lock
    h._dispatch_downlinks()
    assert h.decoded == ["219118"] and h.lock_held_at_decode == [False]


def test_downlink_waiting_before_an_exchange_is_not_discarded():
    ser = FakeMdot(rx=PKT, on_write=lambda d, s: s.feed(b"AT+ALIVE\r\nOK\r\n"))
    h = make_handler(ser)
    assert h.keepalive() is True
    h._dispatch_downlinks()
    assert h.decoded == ["219118"]


@pytest.mark.parametrize("content", ["aabb", b"\xaa\xbb"])
def test_send_neither_discards_nor_trips_on_a_downlink(content):
    reply = b"AT+SENDB=AABB\r\n" + PKT + b"OK\r\n"
    ser = FakeMdot(rx=PKT, on_write=lambda d, s: s.feed(reply))
    h = make_handler(ser)
    assert h._attempt_transmission(content, 242) == (True, "")
    h._dispatch_downlinks()
    assert h.decoded == ["219118", "219118"]     # the stale one and the one mid-send


def test_mac_flush_and_txs_query_keep_downlinks():
    ser = FakeMdot(rx=PKT, on_write=lambda d, s: s.feed(PKT + (b"OK\r\n" if b"SENDB" in d else b"242\r\n")))
    h = make_handler(ser)
    assert h._flush_mac_commands() is True
    h._query_txs_locked()
    h._dispatch_downlinks()
    assert h.decoded == ["219118"] * 3
    assert h.current_size_limit == 242


def test_emergency_lines_are_queued_not_read_as_replies():
    ser = FakeMdot(on_write=lambda d, s: s.feed(b"EMERGENCY: PA_6 pin state: 1\r\n1\r\nOK\r\n"))
    h = make_handler(ser)
    assert h.is_joined() is True
    h._dispatch_downlinks()
    assert h.decoded == []                       # logged, nothing to decode


def test_drain_dispatches_what_the_reset_used_to_throw_away():
    ser = FakeMdot(rx=b"junk\r\n" + PKT + b"more junk\r\n")
    h = make_handler(ser)
    h._drain_input()
    assert ser.in_waiting == 0
    h._dispatch_downlinks()
    assert h.decoded == ["219118"]


def test_listener_decodes_queued_downlinks_outside_the_lock():
    ser = FakeMdot()
    h = make_handler(ser)
    h.listening = True
    h._is_valid_message = lambda m: True
    ser.feed(PKT)
    t = threading.Thread(target=h._listen_loop, daemon=True)
    t.start()
    for _ in range(100):
        if h.decoded:
            break
        time.sleep(0.02)
    h.listening = False
    t.join(3)
    assert h.decoded == ["219118"] and h.lock_held_at_decode == [False]


def test_listener_decodes_a_downlink_queued_by_an_at_path_while_it_waited():
    ser = FakeMdot()
    h = make_handler(ser)
    h.listening = True
    with h.transmit_lock:                         # an AT exchange in progress
        t = threading.Thread(target=h._listen_loop, daemon=True)
        t.start()
        ser.feed(PKT)
        raw = h._read_line()                      # the AT path reads the downlink
        assert raw is None
    for _ in range(100):
        if h.decoded:
            break
        time.sleep(0.02)
    h.listening = False
    t.join(3)
    assert h.decoded == ["219118"]


# ── strict parsing ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("line", [
    "PACKET:PORT=1,SIZE=4,DATA=219118",          # SIZE disagrees with the data
    "PACKET:PORT=1,SIZE=2,DATA=219118",
    "PACKET:PORT=1,SIZE=3,DATA=21911Z",          # not hex
    "PACKET:PORT=1,SIZE=3,DATA=21911",           # odd length
    "PACKET:PORT=1,SIZE=3",
    "PACKET:SIZE=3,DATA=219118",
    "PACKET:PORT=x,SIZE=3,DATA=219118",
    "PACKET:PORT=1,SIZE=3,DATA=219118 trailing",
    "PACKET: info",
    "PACKET:PORT=1,SIZE=3,DATA=",
])
def test_malformed_downlinks_are_ignored(line):
    h = make_handler(FakeMdot())
    h._downlink_q().put(line)
    h._dispatch_downlinks()
    assert h.decoded == []


def test_well_formed_downlinks_decode():
    h = make_handler(FakeMdot())
    for line in ("PACKET:PORT=1,SIZE=3,DATA=219118", "PACKET:PORT=2,SIZE=1,DATA=ab"):
        h._downlink_q().put(line)
    h._dispatch_downlinks()
    assert h.decoded == ["219118", "ab"]


def test_a_decode_error_does_not_stop_the_queue():
    h = make_handler(FakeMdot())
    calls = []

    def decode(p):
        calls.append(p)
        if len(calls) == 1:
            raise RuntimeError("boom")
    h.decode = decode
    h._downlink_q().put("PACKET:PORT=1,SIZE=1,DATA=aa")
    h._downlink_q().put("PACKET:PORT=1,SIZE=1,DATA=bb")
    h._dispatch_downlinks()
    assert calls == ["aa", "bb"]


# ── a readline() timeout can cut a line in two ──────────────────────────────

def test_a_split_downlink_is_joined():
    ser = FakeMdot(rx=b"PACKET:PORT=1,SI")
    h = make_handler(ser)
    assert h._read_line() is None                # the timeout returned half a line
    ser.feed(b"ZE=3,DATA=219118\r\n")
    assert h._read_line() is None                # joined, and queued as a downlink
    h._dispatch_downlinks()
    assert h.decoded == ["219118"]


def test_a_split_ordinary_line_is_joined():
    ser = FakeMdot(rx=b"14,")
    h = make_handler(ser)
    assert h._read_line() is None
    ser.feed(b"1\r\n")
    assert h._read_line() == b"14,1\r\n"


def test_a_stale_partial_line_is_dropped(monkeypatch):
    ser = FakeMdot(rx=b"garb")
    clock = Clock(ser)
    h = make_handler(ser, monkeypatch, clock)
    assert h._read_line() is None
    clock.t += 10
    ser.feed(b"OK\r\n")
    assert h._read_line() == b"OK\r\n"


def test_drain_drops_a_junk_partial_but_keeps_a_downlink_start():
    h = make_handler(FakeMdot(rx=b"junk"))
    h._drain_input()
    assert h._rx_partial == b""
    h = make_handler(FakeMdot(rx=b"PACKET:PORT=1,SI"))
    h._drain_input()
    assert h._rx_partial == b"PACKET:PORT=1,SI"


# ── is_joined: exactly 0 or 1 ───────────────────────────────────────────────

@pytest.mark.parametrize("reply, joined", [
    (b"AT+NJS?\r\n1\r\n\r\nOK\r\n", True),
    (b"AT+NJS?\r\n0\r\n\r\nOK\r\n", False),
    (b"AT+NJS?\r\n" + PKT + b"1\r\nOK\r\n", True),                 # a downlink is not the answer
    (b"AT+NJS?\r\n+NJS: 1\r\nOK\r\n", False),                      # not exactly 0 or 1
    (b"AT+NJS?\r\n12\r\nERROR\r\n", False),                        # first number is not the reply
    (b"PACKET:PORT=1,SIZE=1,DATA=01\r\nOK\r\n", False),
])
def test_is_joined_reads_only_the_line_that_is_0_or_1(reply, joined, monkeypatch):
    ser = FakeMdot(on_write=lambda d, s: s.feed(reply))
    clock = Clock(ser)
    h = make_handler(ser, monkeypatch, clock)
    assert h.is_joined() is joined


def test_is_joined_waits_for_a_slow_answer(monkeypatch):
    ser = FakeMdot()
    clock = Clock(ser)
    clock.at.append((1002.0, b"1\r\nOK\r\n"))
    h = make_handler(ser, monkeypatch, clock)
    assert h.is_joined() is True


def test_is_joined_gives_up_after_about_three_seconds(monkeypatch):
    ser = FakeMdot()
    clock = Clock(ser)
    h = make_handler(ser, monkeypatch, clock)
    assert h.is_joined() is False
    assert 3.0 <= clock.t - 1000.0 < 4.0


# ── legacy text commands are gone ───────────────────────────────────────────

@pytest.mark.parametrize("payload", ["1090", "10910", "1292", "1393", "9900", "21", "22000001", "RX 1090", "abcd"])
def test_legacy_text_commands_change_nothing(payload):
    h = lhc.LoRaHandler.__new__(lhc.LoRaHandler)
    h.config, h.config_file, h.runtime_callback = {}, os.devnull, None
    with patch.object(lhc.LoRaHandler, "update_config") as upd, \
            patch.object(lhc.LoRaHandler, "_start_emergency") as em, \
            patch.object(lhc.LoRaHandler, "_reply_with_status") as reply:
        h.decode(payload)
    upd.assert_not_called()
    em.assert_not_called()
    if payload == "22000001":           # hex: parsed as a frame with seq 0x22 whose blocks are invalid
        reply.assert_called_once_with(ack=(0x22, 2))     # acked as rejected, nothing applied
    else:
        reply.assert_not_called()


def test_the_legacy_line_classifiers_are_gone():
    for name in ("_is_lora_data", "_extract_lora_data", "_is_emergency_clear_message"):
        assert not hasattr(lhc.LoRaHandler, name)


def test_a_bare_digit_line_is_not_a_downlink():
    ser = FakeMdot(rx=b"1090\r\n9999\r\nRX 1090\r\n")
    h = make_handler(ser)
    h.listening = True
    t = threading.Thread(target=h._listen_loop, daemon=True)
    t.start()
    time.sleep(0.3)
    h.listening = False
    t.join(3)
    assert h.decoded == []


# ── listener death ──────────────────────────────────────────────────────────

class BrokenPort(FakeMdot):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    @property
    def in_waiting(self):
        raise self.exc


def test_listener_gives_up_after_repeated_failures(monkeypatch):
    h = make_handler(BrokenPort(RuntimeError("weird")), monkeypatch, Clock())
    h.listening = True
    h._listen_loop()                              # returns instead of looping forever
    assert h.listener_failed is True


def test_listener_failures_must_be_consecutive(monkeypatch):
    seq = iter([RuntimeError("x")] * (lhc.LISTEN_MAX_CONSECUTIVE_FAILURES - 1))
    h = make_handler(FakeMdot(), monkeypatch, Clock())
    n = {"calls": 0}

    class Flaky(FakeMdot):
        @property
        def in_waiting(self):
            n["calls"] += 1
            if n["calls"] % 2 and n["calls"] < 200:
                raise RuntimeError("blip")        # every other call fails
            return 0
    h.ser = Flaky()
    h.listening = True
    t = threading.Thread(target=h._listen_loop, daemon=True)
    t.start()
    for _ in range(200):
        if n["calls"] > 120:
            break
        time.sleep(0.01)
    h.listening = False
    t.join(3)
    assert h.listener_failed is False


def test_listener_gives_up_when_reconnection_fails(monkeypatch):
    h = make_handler(BrokenPort(OSError("device disconnected")), monkeypatch, Clock())
    h._check_serial_connection = lambda: False
    h._attempt_reconnection = lambda: False
    h.listening = True
    h._listen_loop()
    assert h.listener_failed is True


def _run_keepalive(handler):
    stop = threading.Event()
    exits = []
    with patch.object(daemon_mod, "_exit_listener_dead", side_effect=lambda r: (exits.append(r), stop.set())):
        t = threading.Thread(target=daemon_mod._keepalive_loop, args=(handler, stop, 0.02, 0.01), daemon=True)
        t.start()
        t.join(0.5)
        stop.set()
        t.join(1)
    return exits


def test_daemon_exits_when_the_listener_gave_up():
    h = MagicMock()
    h.listener_failed = True
    assert len(_run_keepalive(h)) >= 1


def test_daemon_exits_when_the_listener_thread_died():
    h = MagicMock()
    h.listener_failed = False
    h.listening = True
    h.listener_thread.is_alive.return_value = False
    assert len(_run_keepalive(h)) >= 1


def test_daemon_keeps_running_with_a_live_or_stopped_listener():
    h = MagicMock()
    h.listener_failed = False
    h.listening = True
    h.listener_thread.is_alive.return_value = True
    assert _run_keepalive(h) == []
    h.listening = False
    h.listener_thread.is_alive.return_value = False     # stopped on purpose (refresh_size_limit)
    assert _run_keepalive(h) == []


def test_exit_listener_dead_exits_non_zero():
    with patch.object(daemon_mod.os, "_exit") as ex:
        daemon_mod._exit_listener_dead("test")
    ex.assert_called_once_with(1)
