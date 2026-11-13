"""tools/field_setup.py: the guided field installation, against fake commands.

Nothing here touches a real unit: every command goes through a fake runner,
which answers by command and records what was run, so the tests can check that
a dry run changes nothing and that each step makes exactly the changes it says.
"""
import json
import os
import subprocess
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import field_setup as cli  # noqa: E402
import field_setup_steps as fs  # noqa: E402

#: Commands that change the unit. None may run in a dry run.
MUTATING = ("sudo", "set_parameter", "--reset", "--rejoin", "--set-from-latest", "connection modify",
            "connection up", "connection down", "--save-correction", "--mode calibrate", "systemd-run",
            "last_downlink_check", "flood_baseline.py --set")


class FakeRunner:
    """Answers commands by the first matching rule: (substring, stdout, returncode)."""

    def __init__(self, rules=(), default=("", 0)):
        self.rules = list(rules)
        self.default = default
        self.calls = []

    def __call__(self, argv, **kw):
        cmd = " ".join(str(a) for a in argv)
        self.calls.append(cmd)
        for sub, out, rc in self.rules:
            if sub in cmd:
                out = out(argv) if callable(out) else out
                return subprocess.CompletedProcess(argv, rc, out, "")
        return subprocess.CompletedProcess(argv, self.default[1], self.default[0], "")

    def ran(self, sub):
        return [c for c in self.calls if sub in c]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "SESSIONS_DIR", str(tmp_path / "field_setup"))
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "runtime_config.json").write_text(json.dumps({"ip_upload": {"device_id": "watercam-099"}}))
    (repo / "config" / "camera_calibration.json").write_text(json.dumps(
        {"K": [[1, 0, 1], [0, 1, 1], [0, 0, 1]], "D": [[0, 0, 0, 0, 0]], "img_size": [2592, 1944]}))
    (repo / "bno055_calibration.json").write_text("{}")     # the bench IMU calibration
    monkeypatch.setattr(fs, "REPO", str(repo))
    monkeypatch.setattr(fs, "CONFIG", str(repo / "runtime_config.json"))
    monkeypatch.setattr(fs, "CALIBRATION", str(repo / "config" / "camera_calibration.json"))
    monkeypatch.setattr(fs, "HOLD_FLAG", str(tmp_path / "hold-awake"))
    monkeypatch.setattr(fs, "WITTYPI_LOG", str(tmp_path / "wittyPi.log"))
    monkeypatch.setattr(fs, "POWERLOG", str(tmp_path / "powerlog.csv"))
    monkeypatch.setattr(fs.time, "sleep", lambda s: None)
    # Never the real server: no clock comparison, and a transmitter that reaches nothing
    monkeypatch.setattr(fs, "clock_offset_s", lambda: 0.0)
    from tools import transmit_ip

    class NoServer:
        server_url, device_id = "", ""

        def is_reachable(self, *a, **k):
            return False

        def wait_reachable(self, *a, **k):
            return False

        def _auth_headers(self):
            return {}
    monkeypatch.setattr(transmit_ip, "IPTransmitter", NoServer)
    return tmp_path


def ctx_for(answers, runner, dry_run=False, upload=False):
    ui = fs.ScriptedUI(answers)
    session = fs.Session.new(dry_run=dry_run)
    return fs.Context(ui, session, dry_run=dry_run, upload=upload, runner=runner), ui


NODE = [
    ("node_version.py", "software: v2026.11.1 (commit abcd1234)\n", 0),
    ("is-enabled ticktalk", "enabled\n", 0),
    ("is-active ticktalk", "inactive\n", 0),
    ("--active", "Pixel hotspot:802-11-wireless\ntailscale0:tun\n", 0),
    ("vcgencmd get_throttled", "throttled=0x0\n", 0),
    ("p.reading(p.REG_VOUT)", "4.96\n", 0),
    ("lora_health", json.dumps({"healthy": True, "margin_db": 9.5, "gateways": 2}) + "\n", 0),
]


# ── the record and the plumbing ─────────────────────────────────────────────

def test_session_resumes_the_latest_unfinished_real_session(env):
    done = fs.Session.new()
    done.data["finished"] = "x"
    done.save()
    fs.Session.new(dry_run=True)
    pending = fs.Session.new()
    pending.data["steps"]["start"] = {"status": "pass"}
    pending.save()
    found = fs.Session.latest_unfinished()
    assert found is not None and found.path == pending.path


def test_a_dry_run_says_what_it_would_change_and_changes_nothing(env):
    runner = FakeRunner(NODE)
    ctx, ui = ctx_for(["y"], runner, dry_run=True)
    result = fs.step_start(ctx)
    assert result.status == "pass"
    assert not [c for c in runner.calls if any(m in c for m in MUTATING)]
    assert any("(dry run) would" in line for line in ui.output)
    assert ctx.session.data["changes"] and all(c["dry_run"] for c in ctx.session.data["changes"])


def test_start_holds_the_unit_and_remembers_how_it_was(env):
    runner = FakeRunner(NODE)
    ctx, _ = ctx_for(["y"], runner)
    result = fs.step_start(ctx)
    assert result.status == "pass"
    assert result.values["setup_connection"] == "Pixel hotspot"
    assert runner.ran("set_parameter('hold_awake', True)")
    assert runner.ran("sudo systemctl disable ticktalk")
    assert ctx.session.values["initial"] == {"ticktalk_enabled": True, "hold_awake": None, "hold_flag": False}


def test_quitting_early_puts_the_unit_back(env):
    (env / "repo" / "runtime_config.json").write_text(json.dumps({"hold_awake": True}))
    runner = FakeRunner(NODE)
    ctx, _ = ctx_for([], runner)
    ctx.session.values["initial"] = {"ticktalk_enabled": True, "hold_awake": None, "hold_flag": False}
    fs.restore_normal(ctx)
    assert runner.ran("set_parameter('hold_awake', False)")
    assert runner.ran("sudo systemctl enable ticktalk")


def test_new_site_no_keeps_what_the_unit_learned(env):
    runner = FakeRunner(NODE)
    ctx, _ = ctx_for(["n"], runner)
    assert fs.step_new_site(ctx).status == "skip"
    assert not runner.ran("--reset")


def test_new_site_yes_backs_up_then_resets(env, monkeypatch):
    from tools import flood_baseline, gps_home
    home = env / "gps_home.json"
    home.write_text("{}")
    monkeypatch.setattr(gps_home, "HOME_PATH", str(home))
    monkeypatch.setattr(flood_baseline, "DEFAULT_STATE_PATH", str(env / "flood_state.json"))
    runner = FakeRunner(NODE)
    ctx, _ = ctx_for(["y"], runner)
    assert fs.step_new_site(ctx).status == "pass"
    assert (env / "field_setup" / os.path.basename(ctx.session.path) / "backup" / "gps_home.json").exists()
    for tool in ("gps_home.py --reset", "flood_baseline.py --reset", "registration_orientation.py --reset"):
        assert runner.ran(tool), tool


def test_wiggle_fails_on_a_dip_and_passes_when_steady(env, monkeypatch):
    monkeypatch.setattr(fs, "WIGGLE_S", 0.05)
    ctx, _ = ctx_for([""], FakeRunner(NODE))
    assert fs.step_wiggle(ctx).status == "pass"

    flags = iter(["throttled=0x0\n", "throttled=0x50005\n"])
    dipping = FakeRunner([("vcgencmd", lambda a: next(flags, "throttled=0x50005\n"), 0)] + NODE)
    ctx, _ = ctx_for([""], dipping)
    r = fs.step_wiggle(ctx)
    assert r.status == "fail" and "dipped" in r.message

    low = FakeRunner([("p.reading(p.REG_VOUT)", "4.52\n", 0)] + NODE)
    ctx, _ = ctx_for([""], low)
    r = fs.step_wiggle(ctx)
    assert r.status == "fail" and "4.52" in r.message


def test_power_at_the_mount_catches_a_restart_since_the_wiggle_test(env):
    (env / "wittyPi.log").write_text("System starts up\n")
    ctx, _ = ctx_for([], FakeRunner(NODE))
    ctx.session.values.update(restarts_before_mount=1, wiggle_boot_id=fs.boot_id())
    assert fs.step_power_mount(ctx).status == "pass"
    (env / "wittyPi.log").write_text("System starts up\nSystem starts up\n")
    assert fs.step_power_mount(ctx).status == "fail"


def test_magnetometer_reads_the_last_calibration_status(env):
    runner = FakeRunner([("--mode log", "cal=2/3/3/2\ncal=3/3/2/3\n", 124)] + NODE)
    ctx, _ = ctx_for([""], runner)
    r = fs.step_magnetometer(ctx)
    assert r.status == "pass" and r.values["imu_cal"] == {"sys": 3, "gyro": 3, "accel": 2, "mag": 3}
    runner = FakeRunner([("--mode log", "cal=3/3/3/1\n", 124)] + NODE)
    ctx, _ = ctx_for([""], runner)
    assert fs.step_magnetometer(ctx).status == "warn"


def test_magnetometer_needs_the_bench_imu_calibration_first(env):
    os.remove(env / "repo" / "bno055_calibration.json")
    runner = FakeRunner(NODE)
    ctx, _ = ctx_for([""], runner)
    r = fs.step_magnetometer(ctx)
    assert r.status == "fail" and "never been calibrated" in r.message and "1.7" in r.message
    assert not runner.ran("bno055_calibration.py")       # no figure-eight for nothing


def test_bearing():
    assert fs.bearing_deg(0, 0, 0, 1) == pytest.approx(90.0)
    assert fs.bearing_deg(0, 0, 1, 0) == pytest.approx(0.0)
    assert fs.bearing_deg(43.0, -76.0, 42.99, -76.0) == pytest.approx(180.0)


def test_heading_is_skipped_without_rtk(env):
    ctx, _ = ctx_for([], FakeRunner(NODE))
    ctx.session.values["site"] = {"camera_lat": None}
    r = fs.step_heading(ctx)
    assert r.status == "skip" and r.values["pose"] == "provisional"


# ── the reference photo ─────────────────────────────────────────────────────

def _camera(size, colour=None, pattern=True):
    def take(argv):
        out = argv[argv.index("-o") + 1]
        img = Image.new("RGB", size, colour or (120, 140, 110))
        if pattern:
            px = img.load()
            for x in range(0, size[0], 16):
                for y in range(size[1]):
                    px[x, y] = (250, 250, 250)
        img.save(out)
        return ""
    return take


def test_reference_photo_passes_and_flags_problems(env):
    good = FakeRunner([("rpicam-still", _camera((2592, 1944)), 0)] + NODE)
    ctx, _ = ctx_for([], good)
    r = fs.step_reference_photo(ctx)
    assert r.status == "pass", r.message
    assert r.values["reference_photo_size"] == [2592, 1944]
    assert "--width 2592 --height 1944" in good.ran("rpicam-still")[0]

    small = FakeRunner([("rpicam-still", _camera((1640, 1232)), 0)] + NODE)
    ctx, _ = ctx_for([], small)
    r = fs.step_reference_photo(ctx)
    assert r.status == "warn" and "1640x1232" in r.message

    dark = FakeRunner([("rpicam-still", _camera((2592, 1944), (3, 3, 3), pattern=False), 0)] + NODE)
    ctx, _ = ctx_for([], dark)
    r = fs.step_reference_photo(ctx)
    assert r.status == "warn" and "black" in r.message


def test_reference_photo_refuses_while_the_capture_service_runs(env):
    busy = FakeRunner([("is-active ticktalk", "active\n", 0)] + NODE)
    ctx, _ = ctx_for([], busy)
    assert fs.step_reference_photo(ctx).status == "fail"
    assert not busy.ran("rpicam-still")


class FakeTx:
    def __init__(self, url="http://watercam-api.example.ts.net:8000", trusted=True):
        self.server_url, self.device_id, self._trusted = url, "watercam-099", trusted

    def _auth_headers(self):
        return {"Authorization": "Bearer wct_x"} if self._trusted else {}


def test_upload_sends_the_photo_and_calibration_with_the_units_token(env, monkeypatch):
    from tools import transmit_ip
    import requests
    sent = []

    class R:
        def __init__(self, code):
            self.status_code = code

    monkeypatch.setattr(transmit_ip, "IPTransmitter", lambda: FakeTx())
    monkeypatch.setattr(requests, "post", lambda url, **kw: sent.append(("POST", url, kw["headers"])) or R(200))
    monkeypatch.setattr(requests, "put", lambda url, **kw: sent.append(("PUT", url, kw["json"])) or R(200))
    photo = env / "p.jpg"
    Image.new("RGB", (8, 8)).save(photo)
    ctx, _ = ctx_for([], FakeRunner(NODE), upload=True)
    out = fs.upload_reference(ctx, str(photo))
    assert out["photo"] and out["calibration"]
    assert sent[0][:2] == ("POST", "http://watercam-api.example.ts.net:8000/ip/devices/watercam-099/reference-photo")
    assert sent[0][2] == {"Authorization": "Bearer wct_x"}
    assert sent[1][0] == "PUT" and set(sent[1][2]) == {"K", "D", "img_size"}

    # An older server: the photo is kept, and the person is told to upload it by hand
    monkeypatch.setattr(requests, "post", lambda url, **kw: R(404))
    monkeypatch.setattr(requests, "put", lambda url, **kw: R(404))
    out = fs.upload_reference(ctx, str(photo))
    assert not out["photo"] and "doesn't take photos from units yet" in out["message"]


def test_upload_never_sends_the_token_to_an_untrusted_server(env, monkeypatch):
    from tools import transmit_ip
    import requests
    monkeypatch.setattr(transmit_ip, "IPTransmitter", lambda: FakeTx("http://203.0.113.5:8000", trusted=False))
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("must not post"))
    photo = env / "p.jpg"
    Image.new("RGB", (8, 8)).save(photo)
    ctx, _ = ctx_for([], FakeRunner(NODE), upload=True)
    out = fs.upload_reference(ctx, str(photo))
    assert not out["photo"] and "https or tailnet" in out["message"]


# ── the first capture ───────────────────────────────────────────────────────

def _capture_dir(tmp, status="done", journal="IP uplink OK: 21 channels\n", doas="shutdown -h now\n",
                 captures=True, lora=("a", "b")):
    out = tmp / "capture"
    out.mkdir()
    (out / "result.json").write_text(json.dumps({"status": status, "reason": "" if status == "done" else "x"}))
    (out / "wake.journal").write_text(journal)
    (out / "doas.log").write_text(doas)
    (out / "lora_last_sent.before").write_text(lora[0])
    (out / "lora_last_sent.after").write_text(lora[1])
    if captures:
        cap = tmp / "images" / "20261110-120000"
        cap.mkdir(parents=True)
        (cap / "registration_check.json").write_text(json.dumps({"status": "adopted"}))
        (out / "captures.txt").write_text(str(cap) + "\n")
    return str(out)


def test_check_capture_reads_a_good_wake(tmp_path):
    res = fs.check_capture(_capture_dir(tmp_path))
    assert res["ok"] and res["registration"] == "adopted" and res["ip_uplinks"] == 1 and res["lora_sent"]


@pytest.mark.parametrize("kw, problem", [
    ({"status": "error"}, "x"),
    ({"captures": False}, "no capture"),
    ({"journal": "", "lora": ("a", "a")}, "nothing was sent"),
    ({"journal": "IP uplink OK\nTraceback (most recent call last):\n"}, "error(s)"),
    ({"doas": "probe\n"}, "before its normal end"),
])
def test_check_capture_names_what_went_wrong(tmp_path, kw, problem):
    res = fs.check_capture(_capture_dir(tmp_path, **kw))
    assert not res["ok"] and problem in res["problem"]


# ── the background Wi-Fi test ───────────────────────────────────────────────

def test_a_clock_far_off_is_set_from_network_time(env, monkeypatch):
    wittypi = env / "wittypi"
    wittypi.mkdir()
    (wittypi / "syncTime.sh").write_text("#!/bin/bash\n")
    monkeypatch.setattr(fs, "WITTYPI_DIR", str(wittypi))
    offsets = iter([600.0, 0.4])
    monkeypatch.setattr(fs, "clock_offset_s", lambda: next(offsets))
    runner = FakeRunner(NODE)
    ctx, _ = ctx_for(["y", "y"], runner)            # hotspot: yes; set the clock: yes
    r = fs.step_start(ctx)
    assert r.status == "pass" and r.values["clock_offset_s"] == 0.4
    assert runner.ran(f"sudo bash {wittypi}/syncTime.sh")

    monkeypatch.setattr(fs, "clock_offset_s", lambda: 600.0)    # still off: a warning
    ctx, _ = ctx_for(["y", "n"], FakeRunner(NODE))
    r = fs.step_start(ctx)
    assert r.status == "warn" and "600 s off" in r.message


def test_a_warning_carries_on_and_a_failure_repeats(env, monkeypatch):
    calls = {"n": 0}

    def flaky(ctx):
        calls["n"] += 1
        return fs.StepResult("fail" if calls["n"] == 1 else "warn", "x")
    ctx, ui = ctx_for(["", ""], FakeRunner(NODE))   # Enter at both prompts
    assert cli.run_step(ctx, fs.Step("t", "A", "test", flaky), 0) == "warn"
    assert calls["n"] == 2                          # the failure repeated, the warning carried on


def test_wifi_job_always_comes_back_to_the_setup_network(tmp_path, monkeypatch):
    job = tmp_path / "wifi"
    job.mkdir()
    (job / "job.json").write_text(json.dumps({"setup": "Pixel hotspot", "site": "SiteNet"}))
    calls = []

    def run(argv, **kw):
        calls.append(" ".join(argv))
        if "up" in argv and "SiteNet" in argv:
            raise RuntimeError("radio fell over")
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(fs.subprocess, "run", run)
    monkeypatch.setattr(fs.time, "sleep", lambda s: None)
    assert fs.wifi_test_job(str(job)) == 1
    res = json.loads((job / "result.json").read_text())
    assert res["setup_restored"] and not res["ok"] and "radio fell over" in res["reason"]
    assert calls[-1].endswith("connection up Pixel hotspot")


def test_wifi_job_reports_a_reachable_site_network(tmp_path, monkeypatch):
    from tools import transmit_ip
    job = tmp_path / "wifi"
    job.mkdir()
    (job / "job.json").write_text(json.dumps({"setup": "Pixel hotspot", "site": "SiteNet"}))
    monkeypatch.setattr(fs.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, "*:67\n" if "IN-USE,SIGNAL" in argv else "", ""))
    monkeypatch.setattr(fs.time, "sleep", lambda s: None)

    class Tx:
        def wait_reachable(self, s):
            return True
    monkeypatch.setattr(transmit_ip, "IPTransmitter", Tx)
    assert fs.wifi_test_job(str(job)) == 0
    res = json.loads((job / "result.json").read_text())
    assert res["ok"] and res["signal"] == 67 and res["setup_restored"]


# ── the whole thing, as a rehearsal ─────────────────────────────────────────

def test_a_full_dry_run_rehearsal_changes_nothing(env, monkeypatch):
    runner = FakeRunner([("rpicam-still", _camera((2592, 1944)), 0), ("--mode log", "cal=3/3/3/3\n", 124),
                         ("connection show", "Pixel hotspot:802-11-wireless\n", 0)] + NODE)
    monkeypatch.setattr(fs, "WIGGLE_S", 0.05)
    real_init = fs.Context.__init__

    def init(self, ui, session, **kw):
        kw["runner"] = runner
        real_init(self, ui, session, **kw)
    monkeypatch.setattr(fs.Context, "__init__", init)
    answers = ["y",                         # start: that's my hotspot
               "n",                         # not a new site
               "",                          # wiggle: Enter
               "",                          # magnetometer: at a clear spot
               "",                          # coverage: at the spot
               "", "2.5", "45", "wood post", "", "n",   # mount
               "y", "y",                    # connectivity: rejoin, cellular
               "y", "y", "y"]               # finish: schedule saved, recovery kept, hotspot off
    f = env / "answers.txt"
    f.write_text("\n".join(answers) + "\n")
    assert cli.main(["--dry-run", "--answers", str(f)]) == 0
    assert not [c for c in runner.calls if any(m in c for m in MUTATING)]
    session = fs.Session.load(sorted((env / "field_setup").iterdir())[-1])
    statuses = {k: v["status"] for k, v in session.data["steps"].items()}
    assert set(statuses) == set(fs.STEP_IDS)
    assert "fail" not in statuses.values(), statuses
    assert session.values["site"]["mount_height_m"] == 2.5
