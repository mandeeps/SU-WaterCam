#!/usr/bin/env python3
"""Store this unit's IP API token in runtime_config.json (ip_upload.api_key).

Generate the token on the dashboard (Management -> select the unit -> IP access
token -> Generate), then run this in a terminal on the unit and paste it at the
hidden prompt:

    ~/SU-WaterCam/venv/bin/python ~/SU-WaterCam/tools/set_ip_token.py

From another machine, `tailscale ssh -t pi@<unit> '<that command>'` gives it the
terminal it needs. The token is never echoed, logged or passed on the command
line; only its last four characters are printed. runtime_config.json is made
readable by its owner only (0600), which config_io.write_json keeps on later writes.
No restart is needed: the token is used from the next IP upload or downlink poll.
"""
import argparse
import getpass
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tools import config_io  # noqa: E402

PREFIX = "wct_"
MIN_LENGTH = 40


def looks_like_token(token):
    return token.startswith(PREFIX) and len(token) >= MIN_LENGTH and not any(c.isspace() for c in token)


def store_token(path, token):
    """Save token as ip_upload.api_key, make the file owner-only; returns the device_id."""
    with config_io.locked(path):
        cfg = config_io.read_json(path)
        cfg.setdefault("ip_upload", {})["api_key"] = token
        config_io.write_json(path, cfg)
        os.chmod(path, 0o600)
    return cfg["ip_upload"].get("device_id", "this unit")


def main(argv=None, prompt=getpass.getpass):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=os.path.join(REPO, "runtime_config.json"))
    args = ap.parse_args(argv)
    if not os.path.exists(args.config):
        print(f"{args.config} not found. Nothing changed.", file=sys.stderr)
        return 1
    token = prompt("Paste the token from the dashboard (input hidden): ").strip()
    if not looks_like_token(token):
        print(f"That does not look like a WaterCam token (they start with {PREFIX}). Nothing changed.",
              file=sys.stderr)
        return 1
    device = store_token(args.config, token)
    print(f"Saved for {device} (token ends ...{token[-4:]}). It is used from the next IP upload; "
          "no restart needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
