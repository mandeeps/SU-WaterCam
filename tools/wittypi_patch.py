#!/home/pi/SU-WaterCam/venv/bin/python
"""Patch the Witty Pi software's rtc_to_system(): don't set the clock when it already
matches the RTC.

The Witty Pi daemon (UUGear's daemon.sh, v4.21) calls rtc_to_system() at every boot
when the RTC looks valid. It runs `date -s @<RTC seconds>`, and the RTC counts whole
seconds, so the clock goes back by the fraction of a second already passed. By then
rtc-early-clock.service (tools/rtc_early_clock.py) has already set the clock from the
same RTC about 2.5 s earlier, so the step does nothing useful. journald sees the
clock go backwards and starts a new journal file ("Time jumped backwards, rotating"),
one of the two or three new files every boot makes. With journald's file limit that
shortens how far back a unit's journal reaches (006: 100 files held about 3 days).

The patched function reads the RTC first, and only sets the clock (as before) when it
is more than SKEW_S seconds away from the system clock: when rtc-early-clock didn't
run or failed, it still works as upstream.

The Witty Pi software lives in /home/pi/wittypi, installed by UUGear's installer, not
in this repository. This tool edits utilities.sh in place, only if it finds UUGear's
exact function, keeps the original as utilities.sh.orig-rtc-skip, checks the result
with `bash -n`, and puts the original back if that fails.

    tools/wittypi_patch.py --check     # upstream / patched / unknown
    tools/wittypi_patch.py --apply
    tools/wittypi_patch.py --revert

Exit status: 0 done (or already in the wanted state), 1 not applied (unknown file,
check failed), 2 usage.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_PATH = "/home/pi/wittypi/utilities.sh"
BACKUP_SUFFIX = ".orig-rtc-skip"
MARKER = "# SU-WaterCam: skip when the clock already matches the RTC"
SKEW_S = 2

UPSTREAM = """rtc_to_system()
{
  log '  Writing RTC time to system...'
  local rtc_ts=$(get_rtc_timestamp)
  sudo timedatectl set-ntp 0 >/dev/null
  sudo date -s @$rtc_ts >/dev/null
  TIME_UNKNOWN=0
  log '  Done :-)'
}
"""

PATCHED = """rtc_to_system()
{
  %(marker)s (tools/wittypi_patch.py):
  # rtc-early-clock.service has set it from the same RTC; setting it again to the
  # RTC's whole second only steps it back and makes journald rotate.
  local rtc_ts=$(get_rtc_timestamp)
  local sys_ts=$(date +%%s)
  if [[ "$rtc_ts" =~ ^[0-9]+$ ]]; then
    local skew=$((rtc_ts - sys_ts))
    skew=${skew#-}
    if [ "$skew" -le %(skew)d ]; then
      log "  System time already matches RTC (${skew} s apart), not set"
      TIME_UNKNOWN=0
      return
    fi
  fi
  log '  Writing RTC time to system...'
  sudo timedatectl set-ntp 0 >/dev/null
  sudo date -s @$rtc_ts >/dev/null
  TIME_UNKNOWN=0
  log '  Done :-)'
}
""" % {"marker": MARKER, "skew": SKEW_S}


def state(text: str) -> str:
    if MARKER in text and PATCHED in text:
        return "patched"
    if UPSTREAM in text and text.count("rtc_to_system()") == 1:
        return "upstream"
    return "unknown"


def _bash_ok(path: str) -> bool:
    try:
        return subprocess.run(["bash", "-n", path], capture_output=True).returncode == 0
    except OSError:
        return False


def _write(path: str, text: str) -> None:
    """Atomically replace path, keeping its mode and owner."""
    st = os.stat(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".wittypi_patch.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def apply(path: str) -> int:
    text = open(path).read()
    s = state(text)
    if s == "patched":
        print(f"{path}: already patched")
        return 0
    if s != "upstream":
        print(f"{path}: rtc_to_system() isn't UUGear's v4.21 version; not changed", file=sys.stderr)
        return 1
    backup = path + BACKUP_SUFFIX
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    _write(path, text.replace(UPSTREAM, PATCHED, 1))
    if not _bash_ok(path):
        shutil.copy2(backup, path)
        print(f"{path}: bash -n failed after patching; original put back", file=sys.stderr)
        return 1
    print(f"{path}: patched (original kept as {backup})")
    return 0


def revert(path: str) -> int:
    text = open(path).read()
    s = state(text)
    if s == "upstream":
        print(f"{path}: not patched")
        return 0
    if s != "patched":
        print(f"{path}: not in the patched form; not changed", file=sys.stderr)
        return 1
    _write(path, text.replace(PATCHED, UPSTREAM, 1))
    print(f"{path}: reverted to UUGear's rtc_to_system()")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--revert", action="store_true")
    p.add_argument("--path", default=DEFAULT_PATH)
    a = p.parse_args(argv)
    if not os.path.isfile(a.path):
        print(f"{a.path}: not found", file=sys.stderr)
        return 1
    if a.check:
        print(f"{a.path}: {state(open(a.path).read())}")
        return 0
    return apply(a.path) if a.apply else revert(a.path)


if __name__ == "__main__":
    sys.exit(main())
