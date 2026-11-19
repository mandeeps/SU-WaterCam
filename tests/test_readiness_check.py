"""tools/readiness_check.py: the read-only deployment readiness report, on a fake unit."""
import json
import time
import os
import subprocess
import sys

import pytest

from tools import node_version, readiness_check as rc
from tools.lora_handler_concurrent import LoRaHandler
from tools import lora_daemon

ATI = "MultiTech mDot\nFirmware: 4.1.5-wc2610-sw9\nLibrary: 4.1.33"


class FakeRunner:
    def __init__(self, enabled=(), active=("lora_daemon.service", "segformer_daemon.service"),
                 quectel="no\niot.1nce.net", throttled="0x0", storage="persistent"):
        self.enabled, self.active, self.quectel, self.throttled = set(enabled), set(active), quectel, throttled
        self.storage = storage
        self.calls = []

    def __call__(self, argv, timeout=30):
        self.calls.append(list(argv))
        out, rc_ = "", 0
        if argv[:2] == ["systemctl", "is-enabled"]:
            out = "enabled" if argv[2] in self.enabled else "disabled"
        elif argv[:2] == ["systemctl", "is-active"]:
            out = "active" if argv[2] in self.active else "inactive"
        elif argv[0] == "nmcli" and "gsm.apn" in " ".join(argv):
            out, rc_ = (self.quectel, 0) if self.quectel is not None else ("", 10)
        elif argv[0] == "nmcli":
            out = "site:802-11-wireless\nQuectel:gsm"
        elif argv[0] == "systemd-analyze":
            out = f"[Journal]\nStorage=volatile\n# zz\n[Journal]\nStorage={self.storage}\n"
        elif argv[0] == "vcgencmd":
            out = f"throttled={self.throttled}"
        return subprocess.CompletedProcess(argv, rc_, out, "")


SERVICES = {"max-awake.timer", "watercam-firewall.service", "rtc-early-clock.service",
            "wittypi-next-wake.service", "remote-debug.path"}


@pytest.fixture
def unit(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "output").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "config/camera_calibration.json").write_text("{}")
    (repo / "output/ticktalk_main.pickle").write_bytes(b"pickle")
    import hashlib
    (repo / "output/ticktalk_main.pickle.sha256").write_text(hashlib.sha256(b"pickle").hexdigest() + "\n")
    cfg = {"ip_upload": {"enabled": True, "server_url": "http://server:8000", "api_key": "k", "device_id": "watercam-999"},
           "hold_awake": True}
    (repo / "runtime_config.json").write_text(json.dumps(cfg))
    (repo / "unit_config_UFO999.json").write_text(json.dumps(
        {"imu_calibration_file": "/home/pi/SU-WaterCam/bno055_calibration.json", "imu_magnetic_declination_deg": -12.0}))
    (repo / "bno055_calibration.json").write_text(json.dumps(
        {"node_id": "ufo-999", "components": {"gyro": {"x": 1}, "accel": {"x": 1}, "mag": {"x": 1}}}))
    cap = repo / "images" / "20261118-120000"
    cap.mkdir(parents=True)
    (cap / "capture_status.json").write_text(json.dumps({"nir_filter": "ok"}))
    root = tmp_path / "root"
    (root / "home/pi/wittypi").mkdir(parents=True)
    (root / "home/pi/segformer_type_pool").mkdir(parents=True)
    (root / "home/pi/segformer_type_pool/type_pool_b0_dyn_meta.onnx").write_bytes(b"m")
    monkeypatch.setattr(rc, "ROOT", str(root))
    monkeypatch.setattr(node_version, "software_version", lambda use_cache=True: {
        "describe": "v2026.11.0", "release": (2026, 11, 0), "ahead": 0, "dirty": False, "commit": "2af2c09a"})
    monkeypatch.setattr(node_version, "model_info", lambda path=None: {"name": "m.onnx", "sha256": "ab" * 32, "bands": 5})
    return repo, root


def make(unit, stage="bench", transport="cellular", mdot=None, **runner):
    repo, _ = unit
    info = mdot or {"ati": ATI, "wdog": "20,0,0", "silence": "60", "cproc": "1", "fsb": "1", "njs": "0", "error": None}

    def daemon(action):
        if action == "mdot_info":
            return info
        return False
    runner.setdefault("enabled", SERVICES)
    return rc.Context(stage=stage, transport=transport, offline=True, run=FakeRunner(**runner),
                      daemon=daemon, repo=str(repo))


def by_name(checks, name):
    found = [c for c in checks if c.name == name]
    assert found, [c.name for c in checks]
    return found[0]


def test_a_ready_bench_unit_has_no_fail(unit):
    checks = rc.run_checks(make(unit))
    assert [c for c in checks if c.status == "fail"] == []
    assert by_name(checks, "mdot_firmware").status == "pass"
    assert by_name(checks, "IMU calibration").status == "pass"


def test_banned_mdot_build_fails_unless_accepted(unit):
    ctx = make(unit, mdot={"ati": "Firmware: 4.1.5-wc2610-sw7", "wdog": "20,0,0", "error": None})
    assert by_name(rc.run_checks(ctx), "mdot_firmware").status == "fail"
    accepted = by_name(rc.run_checks(ctx, accept={"mdot_firmware"}), "mdot_firmware")
    assert accepted.status == "info" and accepted.detail.startswith("ACCEPTED")


def test_watchdog_test_value_and_off(unit):
    ctx = make(unit, mdot={"ati": ATI, "wdog": "2,0,0", "error": None})
    assert by_name(rc.run_checks(ctx), "mDot watchdog").status == "warn"
    ctx = make(unit, stage="deployed", mdot={"ati": ATI, "wdog": "0,0,0", "error": None})
    assert by_name(rc.run_checks(ctx), "mDot watchdog").status == "fail"


def test_old_daemon_without_mdot_info_is_a_warning(unit):
    ctx = make(unit)
    ctx._daemon = lambda action: (_ for _ in ()).throw(RuntimeError("Unknown action: 'mdot_info'"))
    c = by_name(rc.run_checks(ctx), "mDot")
    assert c.status == "warn" and "mdot_info" in c.detail


def test_placeholder_and_missing_calibration(unit):
    repo, _ = unit
    (repo / "bno055_calibration.json").write_text(json.dumps({"node_id": "copied-from-006 PLACEHOLDER",
                                                              "timestamp": "2026-10-24"}))
    checks = rc.run_checks(make(unit))
    assert by_name(checks, "IMU calibration").status == "warn"          # old format
    assert by_name(checks, "IMU calibration source").status == "warn"
    os.remove(repo / "bno055_calibration.json")
    assert by_name(rc.run_checks(make(unit, stage="deployed")), "IMU calibration").status == "fail"


def test_zero_declination_and_no_unit_config(unit):
    repo, _ = unit
    (repo / "unit_config_UFO999.json").write_text(json.dumps({"imu_magnetic_declination_deg": 0.0}))
    assert by_name(rc.run_checks(make(unit)), "magnetic declination").status == "warn"
    os.remove(repo / "unit_config_UFO999.json")
    assert by_name(rc.run_checks(make(unit)), "unit config").status == "fail"


def test_deployed_stage_wants_the_unit_armed(unit):
    checks = rc.run_checks(make(unit, stage="deployed"))
    assert by_name(checks, "hold awake").status == "fail"
    assert by_name(checks, "ticktalk").status == "fail"
    assert by_name(checks, "Witty Pi schedule").status == "fail"
    repo, root = unit
    cfg = json.loads((repo / "runtime_config.json").read_text())
    cfg["hold_awake"] = False
    (repo / "runtime_config.json").write_text(json.dumps(cfg))
    (root / "home/pi/wittypi/schedule.wpi").write_text("BEGIN")
    checks = rc.run_checks(make(unit, stage="deployed",
                                enabled=SERVICES | {"ticktalk.service", "wittypi-recovery.service"}))
    assert [c.name for c in checks if c.status == "fail"] == []


def test_cellular_profile_must_be_on_demand(unit):
    assert by_name(rc.run_checks(make(unit, quectel="yes\niot.1nce.net")), "cellular profile").status == "fail"
    assert by_name(rc.run_checks(make(unit, quectel=None)), "cellular profile").status == "fail"
    assert by_name(rc.run_checks(make(unit, transport="lora", quectel=None)), "cellular profile").status == "info"


def test_cellular_site_needs_ip_upload(unit):
    repo, _ = unit
    (repo / "runtime_config.json").write_text(json.dumps({"ip_upload": {"enabled": False}}))
    assert by_name(rc.run_checks(make(unit)), "IP upload").status == "fail"


def test_stuck_nir_filter_fails(unit):
    repo, _ = unit
    (repo / "images/20261118-120000/capture_status.json").write_text(json.dumps({"nir_filter": "not_switching"}))
    assert by_name(rc.run_checks(make(unit)), "IR-cut filter").status == "fail"


def test_under_voltage_since_boot_warns(unit):
    assert by_name(rc.run_checks(make(unit, throttled="0x50000")), "power flags").status == "warn"


def test_nothing_mutating_is_run(unit):
    ctx = make(unit, stage="deployed")
    rc.run_checks(ctx)
    for argv in ctx.run.calls:
        line = " ".join(argv)
        assert not any(w in line for w in ("enable ", "start", "stop", "modify", "connection up", "AT+")), line


def test_a_broken_check_doesnt_hide_the_rest(unit, monkeypatch):
    def boom(ctx):
        raise ValueError("bad")
    monkeypatch.setattr(rc, "CHECKS", [boom, rc.check_power])
    checks = rc.run_checks(make(unit))
    assert checks[0].status == "warn" and "bad" in checks[0].detail and len(checks) > 1


def test_json_and_exit_codes(unit, capsys):
    assert rc.main(["--json"], ctx=make(unit)) == 0
    out = json.loads(capsys.readouterr().out)
    assert {"name", "status", "detail", "fix"} <= set(out["checks"][0])
    assert rc.main([], ctx=make(unit, throttled="0x50000")) == 0
    assert rc.main(["--strict"], ctx=make(unit, throttled="0x50000")) == 1
    assert rc.main([], ctx=make(unit, stage="deployed")) == 1


def test_mdot_info_reads_without_setting():
    sent = []

    def query(cmd, timeout, done):
        sent.append(cmd)
        if cmd == b"ATI\r\n":
            return ["ATI", "MultiTech mDot", "Firmware: 4.1.5-wc2610-sw9", "OK"]
        if cmd == b"AT+WDOG?\r\n":
            return ["AT+WDOG?", "20,3,0", "OK"]
        return ["ERROR"]
    h = LoRaHandler.__new__(LoRaHandler)
    h._at_query = query
    info = h.mdot_info()
    assert info["ati"] == "MultiTech mDot\nFirmware: 4.1.5-wc2610-sw9" and info["wdog"] == "20,3,0"
    assert info["silence"] == "ERROR"
    assert all(c.endswith(b"?\r\n") or c == b"ATI\r\n" for c in sent)
    assert b"ATZ\r\n" not in sent
    assert lora_daemon.ACTIONS["mdot_info"] is lora_daemon._do_mdot_info


def test_journal_in_ram_warns_on_the_bench_and_fails_deployed(unit):
    assert by_name(rc.run_checks(make(unit)), "journal").status == "pass"
    assert by_name(rc.run_checks(make(unit, storage="volatile")), "journal").status == "warn"
    assert by_name(rc.run_checks(make(unit, stage="deployed", storage="volatile")), "journal").status == "fail"


def test_a_broken_check_fails_at_the_deployed_stage(unit, monkeypatch):
    def boom(ctx):
        raise ValueError("bad")
    monkeypatch.setattr(rc, "CHECKS", [boom])
    assert rc.run_checks(make(unit, stage="deployed"))[0].status == "fail"


def test_unreadable_mdot_fails_at_the_deployed_stage(unit):
    ctx = make(unit, stage="deployed", mdot={"error": "LoRa daemon: timed out"})
    c = by_name(rc.run_checks(ctx), "mDot")
    assert c.status == "fail" and "timed out" in c.detail


@pytest.mark.parametrize("wdog,status,text", [(None, "warn", "timed out"), ("ERROR", "warn", "not supported"),
                                              ("garbage", "warn", "unreadable")])
def test_watchdog_replies(unit, wdog, status, text):
    info = {"ati": ATI, "wdog": wdog, "silence": "60", "fsb": "1", "njs": "1", "error": None}
    c = by_name(rc.run_checks(make(unit, mdot=info)), "mDot watchdog")
    assert c.status == status and text in c.detail


def test_daemon_error_is_not_reported_as_not_joined(unit):
    ctx = make(unit, transport="lora", mdot={"error": "LoRa daemon: connection refused"})
    c = by_name(rc.run_checks(ctx), "LoRa session")
    assert "unknown" in c.detail and "refused" in c.detail and "not joined" not in c.detail


def test_joined_read_from_njs(unit):
    info = {"ati": ATI, "wdog": "20,0,0", "njs": "0", "error": None}
    assert "not joined" in by_name(rc.run_checks(make(unit, transport="lora", mdot=info)), "LoRa session").detail


def test_install_warnings_are_shown(unit, monkeypatch):
    # tools/install_system.py comes with its own PR: stand in for it
    import types
    from dataclasses import dataclass

    @dataclass
    class Action:
        kind: str
        target: str
        detail: str
        pending: bool = False

    class Fake:
        def __init__(self, **kw):
            self.actions = [Action("warn", "max-awake.timer", "not enabled: held")]

        def plan(self):
            pass
    mod = types.ModuleType("tools.install_system")
    mod.Installer = Fake
    monkeypatch.setitem(sys.modules, "tools.install_system", mod)
    import tools
    monkeypatch.setattr(tools, "install_system", mod, raising=False)
    checks = rc.check_system_files(make(unit))
    assert checks[0].status == "pass" and any("held" in c.detail and c.status == "warn" for c in checks)


def test_unknown_accept_name_is_an_error(unit, capsys):
    assert rc.main(["--accept", "mdot_firmwear"], ctx=make(unit)) == 2
    assert "no check named mdot_firmwear" in capsys.readouterr().err


def test_accepted_check_has_no_fix_line(unit):
    info = {"ati": "Firmware: 4.1.5-wc2610-sw7", "wdog": "20,0,0", "njs": "1", "error": None}
    c = by_name(rc.run_checks(make(unit, mdot=info), accept={"mdot_firmware"}), "mdot_firmware")
    assert c.status == "info" and c.fix == ""


def test_old_nir_verdict_warns(unit):
    repo, _ = unit
    f = repo / "images/20261118-120000/capture_status.json"
    old = time.time() - 40 * 86400
    os.utime(f, (old, old))
    assert by_name(rc.run_checks(make(unit)), "IR-cut filter").status == "warn"


def test_mdot_info_timeout_and_partial_replies_are_no_answer():
    replies = {b"ATI\r\n": ["ATI", "MultiTech mDot"],          # cut off before OK
               b"AT+WDOG?\r\n": [],                              # timed out
               b"AT+SILENCE?\r\n": ["AT+SILENCE?", "ERROR"]}     # unknown command
    h = LoRaHandler.__new__(LoRaHandler)
    h._at_query = lambda cmd, timeout, done: replies.get(cmd, [cmd.decode().strip(), "1", "OK"])
    info = h.mdot_info()
    assert info["ati"] is None and info["wdog"] is None and info["silence"] == "ERROR" and info["fsb"] == "1"
