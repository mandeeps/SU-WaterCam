#!/home/pi/SU-WaterCam/venv/bin/python
"""Snapshot what this node runs, as a CycloneDX SBOM, for vulnerability triage.

When an advisory comes out (OpenSSL, Tailscale, onnxruntime, ...), the question is
which units are affected. Nodes are set up by hand and their apt packages drift, so
this records each unit as it actually is, at each release (docs/RELEASES.md):

- every installed Debian package (dpkg), Tailscale included;
- the Python packages installed in the venv itself (the rest come from apt);
- the SU-WaterCam release and commit (tools/node_version.py);
- the segmentation model segformer_daemon loaded, by SHA-256;
- the Witty Pi firmware revision (I2C register 12, read-only) and the mDot
  firmware, which only the bench can read (pass --mdot-version).

The output is CycloneDX 1.5 JSON, the format a pi-gen image's build SBOM will use
too, so either can be scanned the same way, e.g. `grype sbom:<file>`.

    python -m tools.node_inventory                        # writes inventory-<host>-<date>.cdx.json
    python -m tools.node_inventory --mdot-version 4.1.21 --out -
"""

import os as _os, sys as _sys  # noqa: E401
_REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO not in _sys.path:
    _sys.path.insert(0, _REPO)   # so ./tools/node_inventory.py can import the tools package

import argparse
import datetime
import json
import os
import socket
import subprocess
import sys
import uuid
from typing import Any, Dict, List, Optional

REPO = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
VENV_PYTHON = os.path.join(REPO, "venv", "bin", "python")
WITTYPI_I2C_BUS = "1"
WITTYPI_I2C_ADDR = "0x08"
WITTYPI_FW_REVISION_REG = "12"        # I2C_FW_REVISION in ~/wittypi/utilities.sh


def _run(cmd: List[str], timeout: int = 30) -> Optional[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def os_release(path: str = "/etc/os-release") -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep:
                    out[key] = value.strip('"')
    except OSError:
        pass
    return out


def deb_packages(dpkg_output: Optional[str], distro: str) -> List[Dict[str, Any]]:
    """Installed packages from `dpkg-query -W -f '${db:Status-Abbrev}\\t${Package}\\t${Version}\\t${Architecture}\\n'`."""
    out = []
    for line in (dpkg_output or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or not parts[0].startswith("ii"):
            continue                     # only fully installed packages
        _, name, version, arch = parts
        out.append({"type": "library", "name": name, "version": version,
                    "purl": f"pkg:deb/debian/{name}@{version}?arch={arch}&distro={distro}"})
    return out


def pip_packages(pip_json: Optional[str]) -> List[Dict[str, Any]]:
    """The venv's own packages from `pip list --local --format=json`."""
    try:
        items = json.loads(pip_json or "[]")
    except ValueError:
        return []
    return [{"type": "library", "name": p["name"], "version": p["version"],
             "purl": f"pkg:pypi/{p['name'].lower()}@{p['version']}"}
            for p in items if isinstance(p, dict) and p.get("name") and p.get("version")]


def wittypi_firmware(i2cget_output: Optional[str]) -> Optional[str]:
    value = (i2cget_output or "").strip()
    return value if value.startswith("0x") else None


def build_bom(debs: List[Dict[str, Any]], pips: List[Dict[str, Any]],
              software: Optional[Dict[str, Any]], model: Optional[Dict[str, Any]],
              wittypi_fw: Optional[str], mdot_version: Optional[str],
              hostname: str, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    describe = software["describe"] if software else "unknown"
    components: List[Dict[str, Any]] = [
        {"type": "application", "name": "SU-WaterCam", "version": describe,
         "properties": [{"name": "watercam:commit", "value": (software or {}).get("commit") or "unknown"}]},
    ]
    if model:
        components.append({"type": "machine-learning-model", "name": model.get("name", "model"),
                           "hashes": [{"alg": "SHA-256", "content": model["sha256"]}],
                           "properties": [{"name": "watercam:path", "value": model.get("path", "")}]})
    components.append({"type": "firmware", "name": "Witty Pi 4 firmware",
                       "version": wittypi_fw or "unknown"})
    components.append({"type": "firmware", "name": "MultiTech mDot firmware",
                       "version": mdot_version or "unknown"})
    components += debs + pips
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": now.isoformat(timespec="seconds"),
            "tools": [{"name": "SU-WaterCam tools/node_inventory.py"}],
            "component": {"type": "device", "name": hostname, "version": describe},
        },
        "components": components,
    }


def collect(mdot_version: Optional[str] = None) -> Dict[str, Any]:
    osr = os_release()
    distro = f"{osr.get('ID', 'debian')}-{osr.get('VERSION_ID', 'unknown')}"
    debs = deb_packages(_run(["dpkg-query", "-W", "-f",
                              "${db:Status-Abbrev}\t${Package}\t${Version}\t${Architecture}\n"]),
                        distro)
    pips = pip_packages(_run([VENV_PYTHON, "-m", "pip", "list", "--local", "--format=json"], 60))
    try:
        from tools.node_version import model_info, software_version
        software, model = software_version(use_cache=False), model_info()
    except Exception:   # noqa: BLE001 - an inventory without these is still useful
        software, model = None, None
    fw = wittypi_firmware(_run(["/usr/sbin/i2cget", "-y", WITTYPI_I2C_BUS, WITTYPI_I2C_ADDR,
                                WITTYPI_FW_REVISION_REG]))
    return build_bom(debs, pips, software, model, fw, mdot_version, socket.gethostname())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--mdot-version", help="mDot firmware version, read on the bench (ATI)")
    p.add_argument("--out", help="file to write, or - for stdout "
                                 "(default: inventory-<host>-<date>.cdx.json)")
    a = p.parse_args(argv)
    bom = collect(a.mdot_version)
    text = json.dumps(bom, indent=1)
    if a.out == "-":
        print(text)
        return 0
    out = a.out or f"inventory-{socket.gethostname()}-{datetime.date.today().isoformat()}.cdx.json"
    with open(out, "w") as f:
        f.write(text)
    kinds = {}
    for c in bom["components"]:
        kinds[c["type"]] = kinds.get(c["type"], 0) + 1
    print(f"{out}: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
