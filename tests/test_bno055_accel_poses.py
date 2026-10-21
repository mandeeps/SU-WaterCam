"""The accelerometer stage: six faces at 90 degrees, each held until the chip counts it."""
import inspect

from tools import bno055_calibration as bc


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeIMU:
    """accel status by time: [(from_s, status), ...]; gravity fixed."""
    def __init__(self, clock, steps, vec=(0.1, 0.2, 9.8)):
        self.clock, self.steps, self.vec = clock, steps, vec

    def calibration_status(self):
        status = [st for at, st in self.steps if self.clock() >= at][-1]
        return {"system": 0, "gyro": 3, "accel": status, "mag": 0}

    def acceleration(self):
        return self.vec


def hold(imu, clock, lines=None):
    out = (lambda *a, **k: lines.append(a[0])) if lines is not None else (lambda *a, **k: None)
    return bc.hold_accel_pose(imu, clock=clock, sleep=clock.sleep, out=out)


def test_gravity_axis():
    assert bc.gravity_axis((0.1, 0.2, -9.8))[:2] == ("-z", bc.gravity_axis((0.1, 0.2, -9.8))[1])
    axis, share, mag = bc.gravity_axis((-0.08, 1.27, -9.72))     # 006, lid down
    assert axis == "-z" and share > 0.98 and 9.7 < mag < 9.9
    axis, share, _ = bc.gravity_axis((0.35, -6.37, 5.30))        # 006, a 50 degree tilt
    assert axis == "-y" and share < bc.POSE_AXIS_SHARE
    assert bc.gravity_axis((-0.04, -0.01, -0.01))[0] is None     # a garbled I2C read


def test_a_counted_pose_is_still_held_for_the_minimum():
    c = Clock()
    r = hold(FakeIMU(c, [(0, 1), (2, 2)]), c)
    assert r["counted"] and r["accel"] == 2 and bc.POSE_MIN_S <= c.t < bc.POSE_MIN_S + 1


def test_an_uncounted_pose_gives_up_at_the_maximum():
    c = Clock()
    r = hold(FakeIMU(c, [(0, 1)]), c)
    assert not r["counted"] and bc.POSE_MAX_S <= c.t < bc.POSE_MAX_S + 1


def test_full_calibration_ends_the_pose_at_once():
    c = Clock()
    assert hold(FakeIMU(c, [(0, 2), (1, 3)]), c)["accel"] == 3 and c.t < 2


def test_a_tilt_is_flagged_after_the_minimum():
    c, lines = Clock(), []
    hold(FakeIMU(c, [(0, 0)], vec=(0.35, -6.37, 5.30)), c, lines)
    assert any("not near 90 degrees" in l for l in lines)
    assert not any("not near 90 degrees" in l for l in lines[:bc.POSE_MIN_S * 2 - 1])
    assert "Not counted" in lines[-1]


def test_a_garbled_reading_doesnt_stop_the_pose():
    c, lines = Clock(), []
    r = hold(FakeIMU(c, [(0, 0), (6, 1)], vec=(-0.04, -0.01, -0.01)), c, lines)
    assert r["counted"] and any("garbled" in l for l in lines)


def test_the_six_poses_are_faces_at_90_degrees():
    assert len(bc.ACCEL_FACES) == 6
    text = " ".join(d for _, d in bc.ACCEL_FACES).lower()
    assert "45" not in text and "tilt" not in text and "antennas hang clear" in text
    src = inspect.getsource(bc.run_calibration_procedure)
    assert "ACCEL_POSITIONS = ACCEL_FACES" in src and "hold_accel_pose(imu)" in src
    assert "HOLD_SECONDS" not in inspect.getsource(bc)


def test_holds_are_short_and_the_faces_go_round_again():
    assert bc.POSE_MIN_S <= 3 and bc.POSE_MAX_S <= 10 and bc.ACCEL_ROUNDS >= 2
    src = inspect.getsource(bc.run_calibration_procedure)
    assert "for rnd in range(1, ACCEL_ROUNDS + 1):" in src


def test_a_stage_below_its_minimum_keeps_the_earlier_offsets(tmp_path):
    import json
    from tests.test_bno055_staged_calibration import FakeIMU, _block, ACC, GYR
    path = str(tmp_path / "bno055_calibration.json")
    bc.save_calibration(FakeIMU(_block(10, 0, 30, 40, 0)), path, "UFO-006", 0.0, ("gyro", "accel"))
    # a later run where the accelerometer never got past 0: gyro saved, accel kept
    low = FakeIMU(_block(77, 0, 31, 41, 0), status={"system": 3, "gyro": 3, "accel": 0, "mag": 3})
    bc.save_calibration(low, path, "UFO-006", 0.0, ("gyro", "accel"))
    data = json.load(open(path))
    saved = bytes(data["offsets_bytes"])
    assert saved[ACC] == bytes([10] * 6) and saved[GYR] == bytes([31] * 6)
    assert data["components"]["accel"]["status"] == 2      # the earlier run's record


def test_a_first_run_below_the_minimum_saves_nothing_for_that_part(tmp_path, capsys):
    import json
    from tests.test_bno055_staged_calibration import FakeIMU, _block, ACC
    path = str(tmp_path / "bno055_calibration.json")
    low = FakeIMU(_block(77, 0, 31, 41, 0), status={"system": 3, "gyro": 3, "accel": 1, "mag": 0})
    bc.save_calibration(low, path, "UFO-006", 0.0, ("gyro", "accel"))
    data = json.load(open(path))
    assert bytes(data["offsets_bytes"])[ACC] == bytes(6) and data["components"]["accel"] is None
    assert "accel: status 1, below 2: not saved, left uncalibrated" in capsys.readouterr().out


def test_a_face_that_starts_at_3_says_so():
    c, lines = Clock(), []
    r = hold(FakeIMU(c, [(0, 3)]), c, lines)
    assert r["accel"] == 3 and lines[-1].strip().startswith("Fully calibrated: accel = 3/3")
