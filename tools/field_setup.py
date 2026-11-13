#!/home/pi/SU-WaterCam/venv/bin/python
"""Guided installation of a WaterCam unit at its site, one numbered step at a time.

Run it on the unit, over SSH (the phone hotspot your laptop is also on) or the
serial console:

    cd ~/SU-WaterCam && venv/bin/python tools/field_setup.py

It walks through the field procedure (docs/FIELD_DEPLOYMENT_GUIDE.md Phase 2,
docs/SETUP_MODE_DESIGN.md section 8) in the order you handle the unit:
A. before mounting (power wiggle test, compass calibration, signal check),
B. mounting, C. at the final position (connectivity, reference photo, first
capture, back on the schedule). Each step checks something or changes
something, says plainly whether it passed, and what to do if not.

Progress is saved in ~/field_setup/<date-time>/ (session.json, setup.log, the
reference photo, the first capture's logs). Run it again to continue where it
stopped: a dropped connection or Ctrl-C loses nothing. Type q at any prompt to
stop; it then offers to put the unit back on its schedule.

Options:
    --dry-run       check everything, change nothing (says what it would do)
    --step NAME     run one step (see --list), e.g. --step reference_photo
    --list          the steps, in order
    --new           start a new session instead of continuing the last one
    --no-upload     don't send the reference photo and calibration to the server
    --answers FILE  answers for a rehearsal: one per line, or a JSON object from a
                    piece of each prompt's text to its answer (others get Enter)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import field_setup_steps as fs  # noqa: E402

MARK = {"pass": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[SKIP]"}


def run_step(ctx: fs.Context, step: fs.Step, number: int) -> str:
    """Run one step until it passes, or the person moves on; its final status."""
    ui = ctx.ui
    while True:
        ui.heading(f"Step {number} of {len(fs.STEPS)}: {step.title}")
        try:
            result = step.fn(ctx)
        except fs.Abort:
            raise
        except Exception as e:  # noqa: BLE001 - a bug in one step must not end the session
            result = fs.StepResult("fail", f"The step stopped with an error: {e}")
        ctx.session.record(step.id, result)
        ctx.session.log(f"{step.id}: {result.status}: {result.message}")
        ui.say(f"{MARK[result.status]} {result.message}")
        if result.status in ("pass", "skip"):
            return result.status
        # A warning carries on unless asked to repeat; a failure repeats unless told to carry on
        default = "c" if result.status == "warn" else "r"
        choice = ui.ask("  r = repeat this step, c = carry on, q = stop here.", default=default).lower()
        if choice.startswith("c"):
            return result.status


def summary(session: fs.Session) -> str:
    lines = [f"Field setup {os.path.basename(session.path)}" + (" (dry run)" if session.data.get("dry_run") else "")]
    for i, step in enumerate(fs.STEPS):
        st = session.data["steps"].get(step.id)
        lines.append(f"  {i:2d}. {MARK.get(st['status'], '[    ]') if st else '[    ]'} {step.title}"
                     + (f": {st['message']}" if st else ""))
    changes = [c for c in session.data["changes"] if c.get("ok") or c.get("dry_run")]
    lines.append(f"  Changes made: {len(changes)} (all listed in session.json)")
    return "\n".join(lines)


def pick_session(ui: fs.TerminalUI, args) -> fs.Session:
    if args.dry_run:
        return fs.Session.new(dry_run=True)
    last = None if args.new else fs.Session.latest_unfinished()
    if last:
        done = [s for s in fs.STEP_IDS if (last.data["steps"].get(s) or {}).get("status") in ("pass", "skip", "warn")]
        ui.say(f"An unfinished setup from {last.data['started']} is saved ({len(done)} of {len(fs.STEPS)} steps done).")
        if ui.yes_no("Continue it?", default=True):
            return last
    return fs.Session.new()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Guided installation of a WaterCam unit at its site.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--step", choices=fs.STEP_IDS)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--new", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--answers")
    ap.add_argument("--wifi-test-job", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.wifi_test_job:
        return fs.wifi_test_job(args.wifi_test_job)
    if args.list:
        for i, s in enumerate(fs.STEPS):
            print(f"{i:2d}. {s.id:16s} {s.title}  (phase {s.phase})")
        return 0

    if args.answers:
        with open(args.answers) as f:
            text = f.read()
        try:
            answers = json.loads(text)          # {"piece of a prompt": "answer"}
        except ValueError:
            answers = text.splitlines()          # one answer per line, in order
        ui: fs.TerminalUI = fs.ScriptedUI(answers)
        ui._print = lambda text: print(text, flush=True)  # rehearsals show their output
    else:
        ui = fs.TerminalUI()

    ui.say("WaterCam field setup" + ("  -- DRY RUN: checks only, nothing on the unit is changed" if args.dry_run else ""))
    ui.say("Type q at any prompt to stop. Progress is saved; run this again to continue.")
    try:
        session = pick_session(ui, args)
    except fs.Abort:
        return 1
    ctx = fs.Context(ui, session, dry_run=args.dry_run, upload=not args.no_upload)
    ui.say(f"Session folder: {session.path}")

    # A Wi-Fi test that finished while this session was disconnected
    job = os.path.join(session.path, "wifi_test")
    if os.path.exists(os.path.join(job, "result.json")) and "connectivity" not in session.data["steps"]:
        fs.wait_wifi_result(ctx, job)

    steps = [s for s in fs.STEPS if s.id == args.step] if args.step else fs.STEPS
    try:
        phase = None
        for step in steps:
            st = (session.data["steps"].get(step.id) or {}).get("status")
            if not args.step and st in ("pass", "skip", "warn"):
                continue
            if step.phase != phase and not args.step:
                phase = step.phase
                ui.heading(fs.PHASES[phase])
            run_step(ctx, step, fs.STEP_IDS.index(step.id))
        if not args.step and "finish" in session.data["steps"]:
            session.data["finished"] = fs._now()
            session.save()
    except fs.Abort:
        ui.say("")
        ui.say("Stopped. Progress is saved; run field_setup.py again to continue.")
        held = fs.read_config().get("hold_awake") is True
        if held and not args.dry_run and "finish" not in session.data["steps"]:
            ui.say("The unit is still held awake: it will drain its battery if you leave it like this.")
            try:
                if ui.yes_no("Put it back on its normal schedule now?", default=False):
                    fs.restore_normal(ctx)
            except fs.Abort:
                pass
    ui.say("")
    ui.say(summary(session))
    with open(os.path.join(session.path, "summary.txt"), "w") as f:
        f.write(summary(session) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
