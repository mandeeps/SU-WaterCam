"""The tools the guides tell people to run work as ./tools/<name>.py on a node:
executable in git, the node venv's Python as interpreter (system Python lacks
smbus2 and friends), and the repo root on sys.path when they import tools.*."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHEBANG = "#!/home/pi/SU-WaterCam/venv/bin/python"
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


def _documented_tools():
    names = set()
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        names |= set(re.findall(r"python3? (?:-m tools\.|(?:\./)?tools/)([a-z_0-9]+)", text))
    return sorted(n for n in names if (ROOT / "tools" / f"{n}.py").exists())


TOOLS = _documented_tools()


def test_the_guides_name_tools():
    assert "bno055_calibration" in TOOLS and len(TOOLS) >= 10


@pytest.mark.parametrize("name", TOOLS)
def test_executable_in_git_with_the_venv_shebang(name):
    path = f"tools/{name}.py"
    mode = subprocess.run(["git", "ls-files", "-s", path], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.split()[0]
    assert mode == "100755", f"{path}: git mode {mode}"
    assert (ROOT / path).read_text(encoding="utf-8").split("\n", 1)[0] == SHEBANG


@pytest.mark.parametrize("name", TOOLS)
def test_repo_root_on_path_before_importing_tools(name):
    src = (ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
    first_import = re.search(r"^(from tools[ .]|import tools\.)", src, re.M)
    if first_import:
        assert "sys.path.insert(0" in src[:first_import.start()], f"{name}: no repo-root bootstrap before tools imports"


@pytest.mark.parametrize("name", ["gps_home", "registration_orientation", "flood_baseline"])
def test_runs_as_a_script(name):
    """`python tools/<name>.py --help` from the repo root: the script's directory,
    not the repo, is sys.path[0], so this only works with the bootstrap."""
    r = subprocess.run([sys.executable, f"tools/{name}.py", "--help"], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and "ModuleNotFoundError" in r.stderr and "tools" not in r.stderr.split("ModuleNotFoundError")[-1][:60]:
        pytest.skip(f"a hardware dependency is missing here: {r.stderr.strip().splitlines()[-1]}")
    assert r.returncode == 0, r.stderr[-600:]     # gps_home has no --help; it prints its status
