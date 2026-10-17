"""How the physical runtime ends when an SQ calls sys.exit.

call_shutdown() ends a normal cycle with sys.exit(0) after the shutdown command
worked. Non-persistent SQs run in a thread pool in the execute process, which
re-raises the SystemExit from the finished job, so the execute process exits
with that status. The ensemble (ticktalkpython/Ensemble.py) treats status 0 as
a requested stop and returns, and runrtm.py exits 0 without a traceback. Any
other status (sys.exit("shutdown") when the shutdown command failed) is still
an error: ProcessError, exit status 1, and systemd restarts the runtime.

Each case compiles a two-SQ graph and runs it through runrtm.main() in a
subprocess (about 6 s each), with the sensor wait stubbed out.
"""
import os
import socket
import subprocess
import sys
import textwrap

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

GRAPH = textwrap.dedent('''
    from ticktalkpython.SQ import SQify, GRAPHify

    @SQify
    def first(trigger):
        print("first ran")
        return 1

    @SQify
    def stop(x):
        import sys
        print("stop ran")
        sys.exit(EXIT_ARG)

    @GRAPHify
    def ttmain(trigger):
        from ticktalkpython.Clock import TTClock
        with TTClock.root() as root_clock:
            a = first(trigger)
            b = stop(a)
            return b
''')

DRIVER = textwrap.dedent('''
    import sys
    repo, graph, port, log = sys.argv[1:5]
    sys.path.insert(0, repo)
    import tools.wait_for_sensors as w
    w.wait_for_sensors = lambda *a, **k: {"ready": True, "waited_s": 0, "missing": []}
    import runrtm
    sys.argv = ["runrtm.py", "-s", "0", "--timeout=60", "--log", log, graph, port]
    runrtm.main()
''')


def _free_port_pair():
    # runrtm uses port and port + 1
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as a, \
                    socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as b:
                a.bind(("127.0.0.1", port))
                b.bind(("127.0.0.1", port + 1))
            return port
        except OSError:
            continue
    pytest.skip("no free port pair")


def _run_graph(tmp_path, exit_arg):
    src = tmp_path / "exitgraph.py"
    src.write_text(GRAPH.replace("EXIT_ARG", exit_arg))
    out = tmp_path / "out"
    subprocess.run([sys.executable, "compile.py", str(src), "--out", str(out)],
                   cwd=REPO, check=True, capture_output=True, timeout=120)
    driver = tmp_path / "drive.py"
    driver.write_text(DRIVER)
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.run(
        [sys.executable, str(driver), REPO, str(out / "exitgraph.pickle"),
         str(_free_port_pair()), str(tmp_path / "runtime.log")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


def test_sys_exit_0_ends_the_runtime_cleanly(tmp_path):
    rc, output = _run_graph(tmp_path, "0")
    assert "stop ran" in output
    assert rc == 0, output[-3000:]
    assert "Traceback" not in output, output[-3000:]
    assert "ProcessError" not in output


def test_other_exit_status_is_still_an_error(tmp_path):
    rc, output = _run_graph(tmp_path, '"shutdown"')
    assert "stop ran" in output
    assert rc != 0
    assert "ProcessError" in output
