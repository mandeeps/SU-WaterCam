"""The calibration tool's BNO055 driver survives garbled I2C reads (the Pi
handles the BNO055's clock stretching badly) and never saves or applies offsets
it couldn't read back consistently."""
import sys
import types

import pytest

try:
    import smbus2  # noqa: F401
except ImportError:                       # the tool imports it at module level
    sys.modules["smbus2"] = types.SimpleNamespace(SMBus=lambda *a, **k: None)

import tools.bno055_calibration as cal

GOOD = bytes(range(1, 23))


def _driver(monkeypatch, chip_ids=(), blocks=()):
    """A BNO055 driver with the bus replaced by scripted reads."""
    imu = object.__new__(cal.BNO055)
    imu.address = cal.BNO055_ADDRESS
    ids, reads = list(chip_ids), list(blocks)
    imu.writes = []
    monkeypatch.setattr(cal.time, "sleep", lambda s: None)

    def read_byte(reg):
        v = ids.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    def read_bytes(reg, n):
        v = reads.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    imu._read_byte = read_byte
    imu._read_bytes = read_bytes
    imu._set_mode = lambda mode: None
    imu._write_byte = lambda reg, val: imu.writes.append((reg, val))
    return imu


def test_chip_id_glitch_is_retried(monkeypatch, capsys):
    imu = _driver(monkeypatch, chip_ids=[0xD0, OSError("nack"), 0xA0])
    imu._verify_chip()
    assert "retried" in capsys.readouterr().out


def test_wrong_chip_still_fails(monkeypatch):
    imu = _driver(monkeypatch, chip_ids=[0xD0] * 5)
    with pytest.raises(IOError, match="expected 0xA0"):
        imu._verify_chip()


def test_offsets_need_two_matching_reads(monkeypatch):
    bad = bytes(22)
    imu = _driver(monkeypatch, blocks=[bad, GOOD, GOOD])
    assert imu.read_offsets() == GOOD


def test_unstable_offsets_are_refused(monkeypatch):
    imu = _driver(monkeypatch, blocks=[bytes([i]) * 22 for i in range(5)])
    with pytest.raises(IOError, match="not saving"):
        imu.read_offsets()


def test_save_refuses_when_offsets_cannot_be_read(monkeypatch, tmp_path):
    imu = _driver(monkeypatch, blocks=[bytes([i]) * 22 for i in range(5)])
    imu.calibration_status = lambda: {"system": 3, "gyro": 3, "accel": 2, "mag": 0}
    path = tmp_path / "c.json"
    with pytest.raises(IOError):
        cal.save_calibration(imu, str(path), "UFO-006", -11.87, ("gyro",))
    assert not path.exists()


def test_write_is_read_back_and_retried(monkeypatch):
    imu = _driver(monkeypatch, blocks=[bytes(22), bytes(22), GOOD, GOOD])   # 1st read-back wrong, then right
    imu.write_offsets(GOOD)
    assert len(imu.writes) == 2 * 22


def test_write_that_never_reads_back_fails(monkeypatch):
    imu = _driver(monkeypatch, blocks=[bytes(22)] * 6)
    with pytest.raises(IOError, match="read back"):
        imu.write_offsets(GOOD)


def test_tilt_instructions_say_which_end_goes_down():
    import inspect
    src = inspect.getsource(cal.run_calibration_procedure)
    assert "lens end DOWN" in src and "back edge raised" in src
    assert "lens end UP" in src
