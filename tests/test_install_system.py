"""tools/install_system.py: installing a release's system files, against a fake root.

Every destination goes under a temporary directory (install_system.ROOT) and every
command through a fake runner, so nothing here touches the machine running the tests.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools import install_system as ins  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MUTATING = ("enable", "disable", "restart", "daemon-reload", "systemd-run", "udevadm", "runuser",
            "fetch", "checkout", "pip")


class FakeRunner:
    """Answers systemctl/dpkg/ip like a node; remembers what was enabled."""

    def __init__(self, enabled=(), doas_ok=True, route="default via 10.0.0.1 dev wlan0"):
        self.calls = []
        self.enabled = set(enabled)
        self.doas_ok = doas_ok
        self.route = route

    def __call__(self, argv, timeout=120):
        self.calls.append(list(argv))
        out, rc = "", 0
        if argv[:2] == ["systemctl", "is-enabled"]:
            out = "enabled" if argv[2] in self.enabled else "disabled"
        elif argv[:2] == ["systemctl", "enable"]:
            self.enabled.add(argv[-1])
        elif argv[0] == "dpkg-query":
            out = "\n".join(f"{p} installed" for p in ins.PACKAGES)
        elif argv[:2] == ["doas", "-C"]:
            rc = 0 if self.doas_ok else 1
        elif argv[:2] == ["ip", "route"]:
            out = self.route
        elif argv[:2] == ["systemctl", "is-active"]:
            out = "active"
        return subprocess.CompletedProcess(argv, rc, out, "" if rc == 0 else "syntax error")

    def mutating(self):
        return [c for c in self.calls if any(m in " ".join(c) for m in MUTATING)
                and c[:2] != ["systemctl", "is-enabled"]]


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(ins, "ROOT", str(tmp_path))
    monkeypatch.setattr(ins, "REPO", REPO)
    (tmp_path / "etc/default").mkdir(parents=True)
    (tmp_path / "etc/default/gpsd").write_text('START_DAEMON="true"\nDEVICES="/dev/ttyUSB1"\n')
    (tmp_path / "etc/systemd/journald.conf.d").mkdir(parents=True)
    (tmp_path / "etc/systemd/journald.conf.d/zz-watercam-journal.conf").write_text("old\n")
    return tmp_path


def files_under(path):
    return sorted(os.path.relpath(os.path.join(d, f), path) for d, _, fs in os.walk(path) for f in fs)


def plan(run, **kw):
    inst = ins.Installer(repo=REPO, run=run, backup_stamp="T", **kw)
    inst.plan()
    return inst


def by_target(inst, target):
    return [a for a in inst.actions if a.target == target]


def test_manifest_sources_exist():
    for item in ins.MANIFEST:
        assert os.path.isfile(os.path.join(REPO, item.src)), item.src
    for src, _ in ins.JOURNALD_CHOICES.values():
        assert os.path.isfile(os.path.join(REPO, src)), src


def test_manifest_destinations_unique():
    dests = [i.dest for i in ins.MANIFEST]
    assert len(dests) == len(set(dests))


def test_dry_run_changes_nothing(root):
    before = {f: (root / f).read_bytes() for f in files_under(root)}
    run = FakeRunner()
    inst = plan(run)
    assert any(a.pending for a in inst.actions)
    assert {f: (root / f).read_bytes() for f in files_under(root)} == before
    assert run.mutating() == []


def test_apply_installs_then_nothing_left(root):
    run = FakeRunner(enabled={"ticktalk.service"})
    inst = plan(run)
    assert inst.apply()
    unit = root / "etc/systemd/system/lora_daemon.service"
    assert unit.read_bytes() == open(os.path.join(REPO, "config/lora_daemon.service"), "rb").read()
    assert oct((root / "usr/local/sbin/watercam-set-clock").stat().st_mode & 0o777) == "0o755"
    assert 'DEVICES=""' in (root / "etc/default/gpsd").read_text()
    # the replaced journal drop-in and gpsd defaults were backed up
    assert (root / "var/backups/watercam/T/etc/default/gpsd").read_text().endswith('"/dev/ttyUSB1"\n')
    assert (root / "var/backups/watercam/T/etc/systemd/journald.conf.d/zz-watercam-journal.conf").exists()
    again = plan(run)
    assert [a.target for a in again.actions if a.pending] == []


def test_powerlog_only_updated_where_installed(root):
    inst = plan(FakeRunner())
    assert by_target(inst, f"{ins.SYSTEMD}/powerlog.service")[0].kind == "info"
    p = root / "etc/systemd/system"
    p.mkdir(parents=True, exist_ok=True)
    (p / "powerlog.service").write_text("old")
    assert by_target(plan(FakeRunner()), f"{ins.SYSTEMD}/powerlog.service")[0].pending


def test_bad_doas_conf_is_not_installed(root):
    (root / "etc/doas.conf").write_text("permit nopass :pi as root cmd /old\n")
    run = FakeRunner(doas_ok=False)
    inst = plan(run)
    assert not inst.apply()
    assert (root / "etc/doas.conf").read_text() == "permit nopass :pi as root cmd /old\n"
    act = by_target(inst, "/etc/doas.conf")[0]
    assert act.status == "failed" and "doas -C" in act.error
    # independent steps still ran
    assert (root / "etc/systemd/system/ticktalk.service").exists()


def test_retired_units_removed(root):
    d = root / "etc/systemd/system/watercam-cellular-usage.service.d"
    d.mkdir(parents=True)
    (d / "standalone.conf").write_text("x")
    (root / "etc/systemd/system/usb-power-on.timer").write_text("x")
    run = FakeRunner()
    inst = plan(run)
    inst.apply()
    assert not d.exists() and not (root / "etc/systemd/system/usb-power-on.timer").exists()
    assert ["systemctl", "disable", "--now", "usb-power-on.timer"] in run.calls
    assert (root / "var/backups/watercam/T/etc/systemd/system/usb-power-on.timer").exists()


def test_deployment_units_never_enabled(root):
    run = FakeRunner()
    plan(run).apply()
    for unit in ins.DEPLOYMENT_UNITS:
        assert not any(c[:2] == ["systemctl", "enable"] and c[-1] == unit for c in run.calls)


def test_max_awake_held_back_on_an_unheld_bench_unit(root):
    run = FakeRunner()   # ticktalk disabled, no hold
    inst = plan(run)
    act = by_target(inst, "max-awake.timer")[0]
    assert act.kind == "warn" and not act.pending
    (root / "etc/watercam").mkdir(parents=True)
    (root / "etc/watercam/hold-awake").write_text("")
    assert by_target(plan(FakeRunner()), "max-awake.timer")[0].pending
    assert by_target(plan(FakeRunner(), allow_max_awake=True), "max-awake.timer")[0].pending


def test_firewall_last_with_rollback_armed_first(root):
    run = FakeRunner(enabled={"ticktalk.service"})
    plan(run).apply()
    names = [" ".join(c) for c in run.calls]
    arm = next(i for i, c in enumerate(names) if c.startswith("systemd-run"))
    load = names.index("systemctl restart watercam-firewall.service")
    assert arm < load
    assert load == max(i for i, c in enumerate(names) if "enable" in c or "restart" in c)


def test_firewall_current_means_no_reload(root):
    run = FakeRunner(enabled={"ticktalk.service"})
    plan(run).apply()
    run2 = FakeRunner(enabled=run.enabled)
    inst = plan(run2)
    assert not by_target(inst, "watercam-firewall.service")[0].pending


def test_daemons_restart_only_when_their_units_change(root):
    run = FakeRunner(enabled={"ticktalk.service"})
    plan(run).apply()
    assert ["systemctl", "restart", "lora_daemon.service", "segformer_daemon.service"] in run.calls
    run2 = FakeRunner(enabled=run.enabled)
    plan(run2).apply()
    assert ["systemctl", "restart", "lora_daemon.service", "segformer_daemon.service"] not in run2.calls


def test_check_exit_codes(root, capsys):
    assert ins.main(["--check"], run=FakeRunner()) == 3
    assert ins.main(["--dry-run"], run=FakeRunner()) == 0


def test_json_output(root, capsys):
    ins.main(["--json"], run=FakeRunner())
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["pending"] > 0
    assert {"kind", "target", "detail", "status"} <= set(out["actions"][0])


def test_release_refused_over_cellular(root, capsys):
    run = FakeRunner(route="default via 10.64.0.1 dev wwan0 proto static")
    assert ins.main(["--release", "v9999.1.0", "--apply"], run=run) in (2,)
    assert not any("fetch" in c for c in run.calls)
