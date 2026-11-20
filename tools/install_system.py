#!/usr/bin/env python3
"""Install the system files of the checked-out release on a node: one idempotent step.

A `git checkout` of a release updates only the checkout. The copies of its files outside
it (systemd units, doas.conf, the firewall, the Witty Pi time scripts, ...) are
installed by hand from FIELD_DEPLOYMENT_GUIDE.md, and each release's notes list what
changed. This script installs the lot from one list (MANIFEST below), so a unit is
brought to a release the same way every time, by a person or an agent:

    tools/install_system.py                          # dry run: what would change (default)
    tools/install_system.py --check                  # same, exit 3 if anything would change
    sudo tools/install_system.py --apply             # make the changes
    sudo tools/install_system.py --release v2026.11.1 --apply   # update by signed tag first
    sudo tools/install_system.py --confirm-firewall  # keep a newly loaded firewall
    tools/install_system.py --json                   # machine-readable, with any mode

What it does, and only when something differs from the repo:
- copies each manifest file (content, mode and owner), keeping the replaced file under
  /var/backups/watercam/<time>/;
- doas.conf only after `doas -C` accepts the new file, so a bad file can't lock pi out;
- the firewall only with a rollback armed: the rules are syntax-checked, loaded but
  not enabled, and a systemd timer stops and disables them after 5 minutes unless
  `--confirm-firewall` (which enables them for boot) runs from a new SSH session
  first; a reboot before that also comes back without them;
- sets gpsd DEVICES="" (quectel-gnss.service hands gpsd the port);
- removes units and drop-ins that releases have retired;
- applies the Witty Pi daemon patch (tools/wittypi_patch.py);
- enables the infrastructure units and reloads systemd; restarts lora_daemon and
  segformer_daemon when their unit files or the release changed; clears the version cache.

What it never does: enable or disable ticktalk or wittypi-recovery, change
runtime_config.json, the Witty Pi schedule or registers, config.txt or cmdline.txt, or
restart networking, NetworkManager, resolved or Tailscale (their files take effect at the
next boot). Those are deployment decisions (tools/field_setup.py) or risk the session
that runs this. Missing apt packages are reported, not installed.

It refuses to download (the `--release` fetch and a requirements change) while the
default route is cellular, unless --allow-cellular: upgrades go over WiFi.

Exit codes: 0 done or nothing to do, 1 a step failed, 2 refused, 3 changes pending
(--check only).
"""

import argparse
import datetime
import filecmp
import grp
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

REPO = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
#: Prefix for every destination path; tests point it at a temporary directory.
ROOT = os.environ.get("WATERCAM_INSTALL_ROOT", "/")
BACKUP_DIR = "/var/backups/watercam"
VERSION_CACHE = "/tmp/watercam-version.json"
FIREWALL_ROLLBACK_UNIT = "watercam-fw-rollback"
FIREWALL_ROLLBACK_DELAY = "5min"
FIREWALL_RULES = "/etc/nftables.d/watercam-host.nft"

SYSTEMD = "/etc/systemd/system"
WITTYPI = "/home/pi/wittypi"


@dataclass(frozen=True)
class Item:
    """One file the release installs outside the checkout."""
    src: str                      # path in the repo
    dest: str                     # absolute path on the node
    mode: int = 0o644
    owner: str = "root"
    #: "always": install it; "if_present": only update an existing copy (optional units).
    when: str = "always"
    #: Tags that pick the follow-up actions: "systemd", "udev", "doas", "firewall",
    #: "daemon" (restart lora/segformer), "logrotate", "next_boot".
    tags: Sequence[str] = ()


def _unit(name: str, *tags: str, when: str = "always") -> Item:
    return Item(f"config/{name}", f"{SYSTEMD}/{name}", tags=("systemd",) + tags, when=when)


MANIFEST: List[Item] = [
    # Guide 1.9: the application and its daemons
    _unit("lora_daemon.service", "daemon"),
    _unit("segformer_daemon.service", "daemon"),
    _unit("ticktalk.service"),
    _unit("wittypi-recovery.service"),
    _unit("wittypi-boot-mark.service"),
    _unit("wittypi-next-wake.service"),
    _unit("max-awake.service"),
    _unit("max-awake.timer"),
    _unit("usb-power-off.service"),
    _unit("quectel-gnss.service"),
    _unit("rtc-early-clock.service"),
    _unit("powerlog.service", when="if_present"),
    # Remote debug session (REMOTE_DEBUG_SESSION.md)
    _unit("remote-debug.service"),
    _unit("remote-debug.timer"),
    _unit("remote-debug.path"),
    # Cellular (guide 1.5.1, CELLULAR_DATA.md)
    _unit("watercam-cellular-filter.service"),
    _unit("watercam-cellular-usage.service"),
    _unit("watercam-firewall.service", "firewall"),
    Item("config/nftables/watercam-cellular.nft", "/etc/nftables.d/watercam-cellular.nft", tags=("cellular_filter",)),
    Item("config/nftables/watercam-host.nft", "/etc/nftables.d/watercam-host.nft", tags=("firewall",)),
    Item("config/networkmanager/dispatcher.d/90-watercam-cellular-usage",
         "/etc/NetworkManager/dispatcher.d/90-watercam-cellular-usage", mode=0o755),
    Item("config/systemd-network/10-wwan.link", "/etc/systemd/network/10-wwan.link", tags=("udev", "next_boot")),
    Item("config/polkit/50-watercam-networkmanager.rules",
         "/etc/polkit-1/rules.d/50-watercam-networkmanager.rules", tags=("next_boot",)),
    Item("config/networkmanager/90-dns-systemd-resolved.conf",
         "/etc/NetworkManager/conf.d/90-dns-systemd-resolved.conf", tags=("next_boot",)),
    Item("config/systemd-resolved/90-watercam.conf",
         "/etc/systemd/resolved.conf.d/90-watercam.conf", tags=("next_boot",)),
    # Modem GNSS (guide 1.3.3)
    Item("config/quectel-gnss-enable", "/usr/local/bin/quectel-gnss-enable", mode=0o755),
    Item("config/77-mm-quectel-ignore-gps.rules", "/etc/udev/rules.d/77-mm-quectel-ignore-gps.rules",
         tags=("udev",)),
    # Privileges, clock, logs
    Item("config/doas.conf", "/etc/doas.conf", tags=("doas",)),
    Item("config/watercam-set-clock", "/usr/local/sbin/watercam-set-clock", mode=0o755),
    Item("config/logrotate-watercam.conf", "/etc/logrotate.d/watercam", tags=("logrotate",)),
    Item("config/serial-getty-noise.conf",
         f"{SYSTEMD}/serial-getty@ttyAMA0.service.d/watercam-noise.conf", tags=("systemd", "next_boot")),
    # Witty Pi time scripts and the faster boot script (guide 1.4, 1.13)
    Item("config/wittypi/syncTime.sh", f"{WITTYPI}/syncTime.sh", mode=0o755, owner="pi"),
    Item("config/wittypi/network_time.py", f"{WITTYPI}/network_time.py", mode=0o755, owner="pi"),
    Item("config/wittypi/gps_time.py", f"{WITTYPI}/gps_time.py", mode=0o755, owner="pi"),
    Item("config/wittypi/beforeScript.sh", f"{WITTYPI}/beforeScript.sh", mode=0o755, owner="pi"),
]

#: The journal drop-in: on the card by default, in RAM with --volatile-journal (guide 1.9).
JOURNALD_DIR = "/etc/systemd/journald.conf.d"
JOURNALD_CHOICES = {
    "persistent": ("config/journald-persistent.conf", f"{JOURNALD_DIR}/zz-watercam-journal.conf"),
    "volatile": ("config/journald-volatile.conf", f"{JOURNALD_DIR}/journald-volatile.conf"),
}

#: Infrastructure units kept enabled. ticktalk and wittypi-recovery are left alone.
ENABLE = [
    "lora_daemon.service", "segformer_daemon.service", "wittypi-boot-mark.service",
    "wittypi-next-wake.service", "max-awake.timer", "usb-power-off.service", "quectel-gnss.service",
    "rtc-early-clock.service", "remote-debug.timer", "remote-debug.path",
    "watercam-cellular-filter.service", "watercam-cellular-usage.service",
]
#: Reported, never changed: field_setup.py's finish step owns them.
DEPLOYMENT_UNITS = ["ticktalk.service", "wittypi-recovery.service"]
#: Started now as well as enabled (the guide's `enable --now`).
START_NOW = {"lora_daemon.service", "segformer_daemon.service", "wittypi-next-wake.service",
             "max-awake.timer", "remote-debug.timer", "remote-debug.path"}

#: Retired by earlier releases: (path, unit to disable first or None).
RETIRED = [
    (f"{SYSTEMD}/usb-power-on.timer", "usb-power-on.timer"),
    (f"{SYSTEMD}/usb-power-on.service", None),
    (f"{SYSTEMD}/watercam.service", "watercam.service"),
    # the stand-alone cellular counter installed ahead of v2026.10.8
    (f"{SYSTEMD}/watercam-cellular-usage.service.d", None),
    ("/usr/local/lib/watercam", None),
]

PACKAGES = ["i2c-tools", "gpsd", "opendoas", "nftables", "uhubctl", "tio"]
GPSD_DEFAULTS = "/etc/default/gpsd"


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@dataclass
class Action:
    kind: str                 # install | edit | remove | enable | patch | release | info | warn
    target: str
    detail: str
    run: Optional[Callable[[], None]] = None   # None: nothing to do (already current) or report only
    tags: Sequence[str] = ()
    status: str = "pending"   # pending | ok | done | failed | skipped
    error: str = ""

    @property
    def pending(self) -> bool:
        # a release step found in a dry run has nothing to run here but is still to do
        return self.status == "pending" and (self.run is not None or self.kind == "release")

    def as_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "target": self.target, "detail": self.detail,
                "status": self.status, **({"error": self.error} if self.error else {})}


def node_path(path: str) -> str:
    return os.path.join(ROOT, path.lstrip("/"))


class Runner:
    """Runs commands; a test replaces it with a fake that records them."""

    def __call__(self, argv: List[str], timeout: float = 120) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return subprocess.CompletedProcess(argv, 127, "", str(e))


class Installer:
    def __init__(self, repo: Optional[str] = None, run: Optional[Runner] = None, backup_stamp: Optional[str] = None,
                 allow_max_awake: bool = False, restart: bool = True,
                 volatile_journal: bool = False):
        self.repo = repo or REPO
        self.run = run or Runner()
        self.stamp = backup_stamp or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.allow_max_awake = allow_max_awake
        self.restart = restart
        self.volatile_journal = volatile_journal
        self.actions: List[Action] = []
        self.release_changed = False

    # -- helpers -----------------------------------------------------------
    def add(self, *a, **kw) -> Action:
        act = Action(*a, **kw)
        if act.run is None and act.kind not in ("warn", "info"):
            act.status = "ok"
        self.actions.append(act)
        return act

    def src(self, rel: str) -> str:
        return os.path.join(self.repo, rel)

    @staticmethod
    def _owner_ok(path: str, owner: str) -> bool:
        try:
            return pwd.getpwuid(os.stat(path).st_uid).pw_name == owner
        except (KeyError, OSError):
            return False

    def file_state(self, src: str, dest: str, mode: int, owner: str) -> str:
        """'' when current, else what differs."""
        if not os.path.lexists(dest):
            parent = os.path.dirname(dest)
            if os.path.isdir(parent) and not os.access(parent, os.R_OK | os.X_OK):
                return "unreadable here (run with sudo to compare)"   # e.g. /etc/polkit-1/rules.d
            return "missing"
        try:
            same = os.path.isfile(dest) and filecmp.cmp(src, dest, shallow=False)
        except PermissionError:
            return "unreadable here (run with sudo to compare)"
        if not same:
            return "content differs"
        diffs = []
        if os.stat(dest).st_mode & 0o7777 != mode:
            diffs.append(f"mode {oct(os.stat(dest).st_mode & 0o7777)} -> {oct(mode)}")
        if ROOT == "/" and not self._owner_ok(dest, owner):
            diffs.append(f"owner -> {owner}")
        return ", ".join(diffs)

    def backup(self, dest: str) -> None:
        if not os.path.lexists(dest):
            return
        rel = os.path.relpath(dest, ROOT)
        target = os.path.join(node_path(BACKUP_DIR), self.stamp, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isdir(dest) and not os.path.islink(dest):
            shutil.copytree(dest, target, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(dest, target, follow_symlinks=False)

    def copy(self, src: str, dest: str, mode: int, owner: str) -> None:
        self.backup(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".watercam-new"
        shutil.copyfile(src, tmp)
        os.chmod(tmp, mode)
        if ROOT == "/":
            os.chown(tmp, pwd.getpwnam(owner).pw_uid, grp.getgrnam(owner).gr_gid)
        os.replace(tmp, dest)

    def check(self, argv: List[str], what: str) -> None:
        r = self.run(argv)
        if r.returncode != 0:
            raise RuntimeError(f"{what}: {(r.stderr or r.stdout).strip()[:300]}")

    def unit_state(self, unit: str) -> str:
        r = self.run(["systemctl", "is-enabled", unit], timeout=20)
        return (r.stdout or "").strip() or "not-found"

    # -- the plan ----------------------------------------------------------
    def plan(self) -> List[Action]:
        self.plan_packages()
        for item in MANIFEST:
            self.plan_item(item)
        self.plan_journald()
        self.plan_gpsd()
        self.plan_retired()
        self.plan_wittypi_patch()
        self.plan_units()
        self.plan_deployment_units()
        return self.actions

    def plan_packages(self) -> None:
        r = self.run(["dpkg-query", "-W", "-f", "${Package} ${db:Status-Status}\\n"] + PACKAGES, timeout=30)
        have = {l.split()[0] for l in (r.stdout or "").splitlines() if l.endswith(" installed")}
        missing = [p for p in PACKAGES if p not in have]
        if missing:
            self.add("warn", "apt packages", "missing (install over WiFi): sudo apt install " + " ".join(missing))

    def plan_item(self, item: Item) -> None:
        src, dest = self.src(item.src), node_path(item.dest)
        if not os.path.isfile(src):
            self.add("warn", item.dest, f"{item.src} not in this release; skipped")
            return
        if item.when == "if_present" and not os.path.lexists(dest):
            self.add("info", item.dest, "optional, not installed on this unit; left alone")
            return
        diff = self.file_state(src, dest, item.mode, item.owner)
        if not diff:
            self.add("install", item.dest, "current", tags=item.tags)
            return
        if "doas" in item.tags:
            run = lambda: self.install_doas(src, dest, item)  # noqa: E731
        else:
            run = lambda: self.copy(src, dest, item.mode, item.owner)  # noqa: E731
        note = " (takes effect at the next boot)" if "next_boot" in item.tags else ""
        self.add("install", item.dest, f"{diff}: install {item.src}{note}", run=run, tags=item.tags)

    def install_doas(self, src: str, dest: str, item: Item) -> None:
        """Validate the new doas.conf before it replaces the one pi relies on."""
        self.check(["doas", "-C", src], f"doas -C rejects {item.src}; /etc/doas.conf left as it was")
        self.copy(src, dest, item.mode, item.owner)

    def plan_journald(self) -> None:
        """Keep the journal on the card by default (persistent drop-in), so a failed
        wake can be read back on the unit; RAM only with --volatile-journal."""
        want = "volatile" if self.volatile_journal else "persistent"
        other = "persistent" if want == "volatile" else "volatile"
        s, d = JOURNALD_CHOICES[want]
        diff = self.file_state(self.src(s), node_path(d), 0o644, "root")
        if diff:
            self.add("install", d, f"{diff}: install {s} (journal {'in RAM' if want == 'volatile' else 'on the card'})",
                     run=lambda s=s, d=d: self.copy(self.src(s), node_path(d), 0o644, "root"),
                     tags=("journald",))
        else:
            self.add("install", d, "current")
        od = node_path(JOURNALD_CHOICES[other][1])
        if os.path.lexists(od):
            def remove(od=od):
                self.backup(od)
                os.remove(od)
            self.add("remove", JOURNALD_CHOICES[other][1], f"switch the journal to {want}", run=remove,
                     tags=("journald",))
        if not os.path.isdir(node_path(JOURNALD_DIR)):
            return
        others = sorted(set(os.listdir(node_path(JOURNALD_DIR))) -
                        {os.path.basename(d) for _, d in JOURNALD_CHOICES.values()})
        if others:
            self.add("warn", JOURNALD_DIR, "other drop-ins may override the WaterCam one: " + ", ".join(others))

    def plan_gpsd(self) -> None:
        path = node_path(GPSD_DEFAULTS)
        try:
            text = open(path).read()
        except OSError:
            self.add("warn", GPSD_DEFAULTS, "missing (is gpsd installed?)")
            return
        if re.search(r'^DEVICES=""\s*$', text, re.M):
            self.add("edit", GPSD_DEFAULTS, 'DEVICES="" already')
            return

        def fix():
            self.backup(path)
            new = re.sub(r"^DEVICES=.*$", 'DEVICES=""', text, flags=re.M)
            if new == text:
                new = text.rstrip("\n") + '\nDEVICES=""\n'
            with open(path, "w") as f:
                f.write(new)
        self.add("edit", GPSD_DEFAULTS, 'set DEVICES="" (quectel-gnss.service hands gpsd the port)',
                 run=fix, tags=("gpsd",))

    def plan_retired(self) -> None:
        for path, unit in RETIRED:
            p = node_path(path)
            if not os.path.lexists(p):
                continue

            def remove(p=p, unit=unit):
                if unit:
                    self.run(["systemctl", "disable", "--now", unit])
                self.backup(p)
                shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
            self.add("remove", path, "retired by an earlier release", run=remove, tags=("systemd",))

    def plan_wittypi_patch(self) -> None:
        sys.path.insert(0, self.repo)
        try:
            from tools import wittypi_patch as wp
        except Exception as e:  # noqa: BLE001
            self.add("warn", "wittypi patch", f"tools/wittypi_patch.py unavailable: {e}")
            return
        path = node_path(wp.DEFAULT_PATH)
        try:
            st = wp.state(open(path).read())
        except OSError:
            self.add("warn", wp.DEFAULT_PATH, "Witty Pi daemon script not found")
            return
        if st == "patched":
            self.add("patch", wp.DEFAULT_PATH, "patched already")
        elif st == "upstream":
            def apply():
                if wp.apply(path) != 0:
                    raise RuntimeError("tools/wittypi_patch.py --apply failed")
            self.add("patch", wp.DEFAULT_PATH, "apply the RTC double-set patch (tools/wittypi_patch.py)", run=apply)
        else:
            self.add("warn", wp.DEFAULT_PATH, f"unrecognised daemon script ({st}); patch not applied")

    def max_awake_would_power_off(self) -> bool:
        """True if enabling max-awake.timer now could power off a bench unit held up by hand."""
        if self.unit_state("ticktalk.service") == "enabled":
            return False
        try:
            cfg = json.load(open(os.path.join(self.repo, "runtime_config.json")))
        except (OSError, ValueError):
            cfg = {}
        return not (cfg.get("hold_awake") is True or os.path.exists(node_path("/etc/watercam/hold-awake")))

    def plan_units(self) -> None:
        for unit in ENABLE:
            state = self.unit_state(unit)
            if state in ("enabled", "static", "masked", "enabled-runtime"):
                note = " (masked on purpose: left alone)" if state == "masked" else ""
                self.add("enable", unit, state + note)
                continue
            if unit == "max-awake.timer" and not self.allow_max_awake and self.max_awake_would_power_off():
                self.add("warn", unit, "not enabled: ticktalk is disabled and hold_awake is not set, so the timer "
                         "would power this unit off once it has been up max_awake_minutes. Set hold_awake "
                         "first or pass --allow-max-awake")
                continue
            now = ["--now"] if unit in START_NOW else []
            self.add("enable", unit, f"{state}: enable{' and start' if now else ''}",
                     run=lambda unit=unit, now=now: self.check(["systemctl", "enable", *now, unit],
                                                              f"enable {unit}"),
                     tags=("enable",))
        fw = "watercam-firewall.service"
        fw_files = [a for a in self.actions if "firewall" in a.tags and a.pending]
        state = self.unit_state(fw)
        active = (self.run(["systemctl", "is-active", fw], timeout=20).stdout or "").strip()
        trial = (self.run(["systemctl", "is-active", f"{FIREWALL_ROLLBACK_UNIT}.timer"], timeout=20).stdout
                 or "").strip() == "active"
        if state != "enabled" and active == "active" and trial and not fw_files:
            self.add("warn", fw, "on trial, not yet kept: run --confirm-firewall from a new SSH session "
                     f"before the rollback stops it")
        elif state != "enabled" or fw_files or active != "active":
            what = ("load the input firewall" if state != "enabled" or active != "active"
                    else "reload the changed firewall")
            state = f"{state}, {active or 'unknown'}"
            self.add("enable", fw, f"{state}: {what}, rollback armed ({FIREWALL_ROLLBACK_DELAY}); keep it with "
                     "--confirm-firewall from a new SSH session", run=self.load_firewall, tags=("firewall_load",))
        else:
            self.add("enable", fw, "enabled")

    def load_firewall(self) -> None:
        """Load the rules for a trial: started, NOT enabled, with a rollback armed.

        Only --confirm-firewall enables it. So if the rules cut SSH off, the rollback
        (or, failing that, any reboot or power cycle) brings the unit back without them."""
        self.check(["nft", "-c", "-f", node_path(FIREWALL_RULES)],
                   f"nft -c rejects {FIREWALL_RULES}; firewall not loaded")
        self.run(["systemctl", "stop", f"{FIREWALL_ROLLBACK_UNIT}.timer"])
        self.check(["systemd-run", f"--unit={FIREWALL_ROLLBACK_UNIT}", f"--on-active={FIREWALL_ROLLBACK_DELAY}",
                    "/bin/systemctl", "disable", "--now", "watercam-firewall.service"],
                   "arm the firewall rollback")
        self.check(["systemctl", "daemon-reload"], "daemon-reload")
        # an earlier, confirmed copy may be enabled: the new rules must not come back at boot unconfirmed
        self.check(["systemctl", "disable", "watercam-firewall.service"], "hold the firewall off at boot")
        self.check(["systemctl", "restart", "watercam-firewall.service"], "load the firewall")

    def plan_deployment_units(self) -> None:
        states = ", ".join(f"{u} {self.unit_state(u)}" for u in DEPLOYMENT_UNITS)
        self.add("info", "deployment units", f"{states} (left alone: tools/field_setup.py sets them)")

    # -- apply -------------------------------------------------------------
    def apply(self) -> bool:
        """Files first, then reloads, then enables, then restarts; the firewall last of all."""
        def phase(pred):
            ok = True
            for a in list(self.actions):
                if a.pending and pred(a):
                    try:
                        a.run()
                        a.status = "done"
                    except Exception as e:  # noqa: BLE001 - report it, carry on with independent steps
                        a.status, a.error, ok = "failed", str(e), False
            return ok

        ok = phase(lambda a: a.kind not in ("enable",))
        ok = self.reload() and ok
        ok = phase(lambda a: a.kind == "enable" and "firewall_load" not in a.tags) and ok
        ok = self.restart_daemons() and ok
        ok = phase(lambda a: "firewall_load" in a.tags) and ok
        if any(a.status == "done" for a in self.actions) or self.release_changed:
            try:
                os.remove(node_path(VERSION_CACHE))
            except OSError:
                pass
        return ok

    def _steps(self, steps: List[List[str]]) -> bool:
        ok = True
        for argv in steps:
            r = self.run(argv)
            act = self.add("info", " ".join(argv), "ok" if r.returncode == 0 else
                           f"failed: {(r.stderr or r.stdout).strip()[:200]}")
            act.status = "done" if r.returncode == 0 else "failed"
            ok = ok and r.returncode == 0
        return ok

    def reload(self) -> bool:
        tags = {t for a in self.actions if a.status == "done" for t in a.tags}
        steps = []
        if tags & {"systemd", "firewall"} or any(a.kind == "enable" and a.pending for a in self.actions):
            steps.append(["systemctl", "daemon-reload"])
        if "udev" in tags:
            steps.append(["udevadm", "control", "--reload"])
        if "journald" in tags:
            steps.append(["systemctl", "restart", "systemd-journald"])
        if "gpsd" in tags:
            steps.append(["systemctl", "restart", "gpsd.socket"])
            steps.append(["systemctl", "try-restart", "gpsd.service"])
        if "cellular_filter" in tags:
            # only while wwan0 is up; otherwise the new rules load with the next cellular link
            steps.append(["systemctl", "try-restart", "watercam-cellular-filter.service"])
        if "logrotate" in tags:
            steps.append(["logrotate", "-d", "/etc/logrotate.d/watercam"])
        return self._steps(steps)

    def restart_daemons(self) -> bool:
        tags = {t for a in self.actions if a.status == "done" for t in a.tags}
        if self.restart and ("daemon" in tags or self.release_changed):
            return self._steps([["systemctl", "restart", "lora_daemon.service", "segformer_daemon.service"]])
        return True


# ---------------------------------------------------------------------------
# Release update (guide 1.3.1, RELEASES.md "Updating a node")
# ---------------------------------------------------------------------------

def default_route_is_cellular(run: Runner) -> bool:
    r = run(["ip", "route", "show", "default"], timeout=10)
    first = (r.stdout or "").strip().splitlines()[:1]
    return bool(first) and re.search(r"\bdev (wwan\d+|ppp\d+)\b", first[0]) is not None


def as_repo_owner(argv: List[str]) -> List[str]:
    """Run git and pip as the checkout's owner, not root (no root-owned files in it)."""
    if os.geteuid() != 0:
        return argv
    owner = pwd.getpwuid(os.stat(REPO).st_uid).pw_name
    return ["runuser", "-u", owner, "--"] + argv


def update_release(tag: str, apply: bool, allow_cellular: bool, run: Runner, out: List[Action]) -> Optional[str]:
    """Check out a signed tag. Returns the old commit when the checkout moved, else None."""
    def add(detail, status="ok", error=""):
        out.append(Action("release", tag, detail, status=status, error=error))

    git = lambda *a: run(as_repo_owner(["git", "-C", REPO, *a]), timeout=300)  # noqa: E731
    old = (git("rev-parse", "HEAD").stdout or "").strip()
    branch = (git("symbolic-ref", "-q", "--short", "HEAD").stdout or "").strip()
    target = (git("rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}").stdout or "").strip()
    if target and target == old:
        add("already checked out")
        return None
    if not target or apply:
        if default_route_is_cellular(run) and not allow_cellular:
            add("refused: the default route is cellular; update over WiFi (or --allow-cellular)", "failed")
            raise SystemExit(2)
    if not apply:
        add(f"would fetch, verify and check out {tag} (now {old[:8]})", "pending")
        return None
    r = git("fetch", "-q", "--depth", "1", "origin", "tag", tag)
    if r.returncode:
        add("fetch failed", "failed", (r.stderr or "").strip()[:300])
        raise SystemExit(1)
    r = git("verify-tag", tag)
    if r.returncode:
        add("NOT signed by a listed release key: not checked out", "failed", (r.stderr or "").strip()[:300])
        raise SystemExit(2)
    r = git("checkout", "-q", tag)
    if r.returncode:
        add("checkout failed (local edits in the way?)", "failed", (r.stderr or "").strip()[:300])
        raise SystemExit(1)
    pickle = os.path.join(REPO, "output", "ticktalk_main.pickle")
    try:
        import hashlib
        have = hashlib.sha256(open(pickle, "rb").read()).hexdigest()
        want = open(pickle + ".sha256").read().split()[0]
    except (OSError, IndexError):
        have, want = "", "?"
    if have != want:
        back = git("checkout", "-q", branch or old)
        add("pickle SHA-256 does not match output/ticktalk_main.pickle.sha256: " +
            (f"back on {branch or old[:8]}" if back.returncode == 0 else
             f"AND going back to {branch or old[:8]} failed: check the checkout by hand"),
            "failed", (back.stderr or "").strip()[:300])
        raise SystemExit(1)
    add(f"checked out (was {old[:8]}), tag verified, pickle matches", "done")
    if git("diff", "--quiet", old, "HEAD", "--", "requirements.txt").returncode:
        r = run(as_repo_owner([os.path.join(REPO, "venv", "bin", "pip"), "install", "-q", "-r",
                               os.path.join(REPO, "requirements.txt")]), timeout=1800)
        add("requirements.txt changed: venv updated" if r.returncode == 0 else "pip install failed",
            "done" if r.returncode == 0 else "failed", (r.stderr or "").strip()[-300:] if r.returncode else "")
    return old


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def report(actions: List[Action], dry_run: bool) -> str:
    lines = []
    for a in actions:
        if a.status in ("done", "failed"):
            m = {"done": " done ", "failed": "FAILED"}[a.status]
        elif a.kind in ("warn", "info"):
            m = " WARN " if a.kind == "warn" else " info "
        elif a.pending:
            m = "WOULD " if dry_run else " TODO "
        else:
            m = "  ok  "
        lines.append(f"[{m}] {a.target}: {a.detail}" + (f" -- {a.error}" if a.error else ""))
    pending = sum(a.pending for a in actions)
    failed = sum(a.status == "failed" for a in actions)
    warns = sum(a.kind == "warn" for a in actions)
    tail = (f"{pending} change(s) would be made; run with sudo and --apply." if dry_run and pending else
            "Nothing to change." if dry_run else
            f"{sum(a.status == 'done' and a.kind != 'info' for a in actions)} change(s) made, {failed} failed.")
    lines.append(f"{tail} {warns} warning(s).")
    if any("firewall_load" in a.tags and a.status == "done" for a in actions):
        lines.append(f"FIREWALL: it is stopped again in {FIREWALL_ROLLBACK_DELAY} unless you open a NEW ssh "
                     "session and run: sudo tools/install_system.py --confirm-firewall")
    return "\n".join(lines)


def confirm_firewall(run: Runner) -> int:
    """Keep a trial firewall: stop the rollback, then enable it for boot."""
    r = run(["systemctl", "is-active", "watercam-firewall.service"])
    if (r.stdout or "").strip() != "active":
        print("watercam-firewall.service is not active (the rollback may have run): nothing to confirm")
        return 1
    r = run(["systemctl", "stop", f"{FIREWALL_ROLLBACK_UNIT}.timer"])
    if r.returncode:
        print(f"could not stop the rollback timer ({(r.stderr or r.stdout).strip()[:200]}): the firewall "
              f"will still be stopped. Run with sudo.", file=sys.stderr)
        return 1
    r = run(["systemctl", "enable", "watercam-firewall.service"])
    if r.returncode:
        print(f"rollback stopped, but enabling the firewall failed ({(r.stderr or r.stdout).strip()[:200]}): "
              "it runs now but won't load at boot", file=sys.stderr)
        return 1
    print("firewall kept: rollback stopped, enabled for boot")
    return 0


def rebuild_args(a: argparse.Namespace) -> List[str]:
    """The options to hand the new release's copy of this script (never --release)."""
    flags = {"apply": "--apply", "check": "--check", "dry_run": "--dry-run", "allow_cellular": "--allow-cellular",
             "allow_max_awake": "--allow-max-awake", "volatile_journal": "--volatile-journal",
             "no_restart": "--no-restart", "json": "--json"}
    return [f for k, f in flags.items() if getattr(a, k, False)]


def main(argv=None, run: Optional[Runner] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="show what would change (the default)")
    mode.add_argument("--check", action="store_true", help="dry run; exit 3 if anything would change")
    mode.add_argument("--apply", action="store_true", help="make the changes (as root)")
    mode.add_argument("--confirm-firewall", action="store_true", help="keep a firewall loaded by --apply")
    p.add_argument("--release", metavar="TAG", help="first fetch, verify and check out this signed tag")
    p.add_argument("--allow-cellular", action="store_true", help="allow downloads over cellular")
    p.add_argument("--allow-max-awake", action="store_true",
                   help="enable max-awake.timer even on a unit held up with ticktalk disabled")
    p.add_argument("--volatile-journal", action="store_true",
                   help="keep the journal in RAM (lost at each shutdown) instead of on the card")
    p.add_argument("--no-restart", action="store_true", help="don't restart lora_daemon/segformer_daemon")
    p.add_argument("--json", action="store_true", help="print the actions as JSON")
    p.add_argument("--_after-release", dest="after_release", help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    run = run or Runner()

    if a.confirm_firewall:
        return confirm_firewall(run)
    if a.apply and ROOT == "/" and os.geteuid() != 0:
        print("--apply needs root: sudo tools/install_system.py --apply", file=sys.stderr)
        return 2

    pre: List[Action] = []
    if a.release and not a.after_release:
        try:
            old = update_release(a.release, a.apply, a.allow_cellular, run, pre)
        except SystemExit as e:
            print(json.dumps({"actions": [x.as_dict() for x in pre]}, indent=1) if a.json else report(pre, not a.apply))
            return int(e.code or 1)
        if old:
            # The checkout now holds the new release's copy of this script and manifest: run that one.
            script = os.path.join(REPO, "tools", "install_system.py")
            rest = rebuild_args(a) + ["--_after-release", old]
            env = dict(os.environ, WATERCAM_INSTALL_PRE=json.dumps([x.as_dict() for x in pre]))
            code = subprocess.call([sys.executable, script] + rest, env=env)
            if code == 0 and any(x.status == "failed" for x in pre):
                return 1      # e.g. pip failed: the new release may not run
            return code

    inst = Installer(run=run, allow_max_awake=a.allow_max_awake, restart=not a.no_restart,
                     volatile_journal=a.volatile_journal)
    inst.release_changed = bool(a.after_release)
    inst.actions.extend(pre)
    if a.after_release:
        # the release step ran in the old copy of this script: report it here too
        try:
            for d in json.loads(os.environ.get("WATERCAM_INSTALL_PRE", "[]")):
                inst.actions.append(Action(d["kind"], d["target"], d["detail"], status=d["status"],
                                           error=d.get("error", "")))
        except (ValueError, KeyError, TypeError):
            pass
    inst.plan()
    ok = inst.apply() if a.apply else True
    ok = ok and not any(x.status == "failed" for x in inst.actions)
    if a.json:
        print(json.dumps({"dry_run": not a.apply, "pending": sum(x.pending for x in inst.actions),
                          "actions": [x.as_dict() for x in inst.actions]}, indent=1))
    else:
        print(report(inst.actions, not a.apply))
    if not ok:
        return 1
    if a.check and any(x.pending for x in inst.actions):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
