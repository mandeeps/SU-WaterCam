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
