"""
Regression tests for TickTalkPython compilation of all TT source files.

Guards against TTSyntaxError regressions (undefined functions, bad module-
level calls, missing SQify decorators) and compiler crashes (AttributeError
inside the typechecker or compiler-rules visitor).

Each test compiles a single TT source file via compile.py and asserts that
the process exits 0.  A failing test means someone broke the file's TT
syntax and the issue must be fixed before merging.
"""
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_COMPILE_SCRIPT = str(_REPO_ROOT / "compile.py")
_OUTPUT_DIR = str(_REPO_ROOT / "output")

# Files with a @GRAPHify entry point that must compile as standalone TT programs.
# Helper modules (tt_take_photos.py) are @SQify-only and compiled indirectly
# when ticktalk_main.py is compiled.
_TT_SOURCES = [
    "ticktalk_main.py",
]


def _run_compile(source_file: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _COMPILE_SCRIPT, source_file, "--out", _OUTPUT_DIR],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_failure(source_file: str, result: subprocess.CompletedProcess) -> str:
    lines = [f"compile.py {source_file} exited {result.returncode}"]
    if result.stdout.strip():
        lines.append("── stdout ──")
        lines.append(result.stdout.rstrip())
    if result.stderr.strip():
        lines.append("── stderr ──")
        lines.append(result.stderr.rstrip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source_file", _TT_SOURCES)
def test_tt_compile_exits_zero(source_file):
    """compile.py must exit 0 — any TTSyntaxError or crash is a failure."""
    pytest.importorskip("astor", reason="astor not installed; TT compiler unavailable")
    result = _run_compile(source_file)
    assert result.returncode == 0, _format_failure(source_file, result)


@pytest.mark.parametrize("source_file", _TT_SOURCES)
def test_tt_compile_no_traceback(source_file):
    """compile.py must not produce a Python traceback (crash ≠ TTSyntaxError)."""
    pytest.importorskip("astor", reason="astor not installed; TT compiler unavailable")
    result = _run_compile(source_file)
    combined = result.stdout + result.stderr
    has_traceback = "Traceback (most recent call last)" in combined
    assert not has_traceback, (
        f"compile.py {source_file} crashed with a Python traceback "
        f"(exit {result.returncode}):\n{combined}"
    )


@pytest.mark.parametrize("source_file", _TT_SOURCES)
def test_tt_compile_no_syntax_error(source_file):
    """compile.py must not emit a TTSyntaxError."""
    pytest.importorskip("astor", reason="astor not installed; TT compiler unavailable")
    result = _run_compile(source_file)
    combined = result.stdout + result.stderr
    assert "TTSyntaxError" not in combined, (
        f"compile.py {source_file} raised TTSyntaxError "
        f"(exit {result.returncode}):\n{combined}"
    )


# ---------------------------------------------------------------------------
# Regression: module-level calls the typechecker must handle
# ---------------------------------------------------------------------------

class TestTypecheckerHandlesModulePatterns:
    """
    Unit-level checks for patterns that historically crashed the TT
    typechecker.  Each test feeds a minimal synthetic TT program to
    TTCompile directly and asserts it either compiles or raises a clean
    TTSyntaxError — never an unhandled AttributeError / crash.
    """

    def _compile_snippet(self, code: str) -> None:
        """Write *code* to a tmp file and run TTCompile on it."""
        import os
        import tempfile
        TTCompile = pytest.importorskip(
            "ticktalkpython.Compiler", reason="ticktalkpython not importable"
        ).TTCompile

        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", delete=False, dir=str(_REPO_ROOT)
        ) as f:
            f.write(code)
            tmp_path = f.name
        try:
            TTCompile(tmp_path, str(_REPO_ROOT))
        finally:
            os.unlink(tmp_path)
            # Remove generated pickle if any
            pickle = tmp_path.replace(".py", ".pickle")
            if os.path.exists(pickle):
                os.unlink(pickle)

    def test_attribute_call_in_graphify_does_not_crash(self):
        """Method calls (obj.method()) inside @GRAPHify must not crash the typechecker."""
        from ticktalkpython.Error import TTSyntaxError

        code = """\
from ticktalkpython.SQ import SQify, GRAPHify

@SQify
def my_sq(trigger):
    return trigger

@GRAPHify
def main(trigger):
    from ticktalkpython.Clock import TTClock
    with TTClock.root() as clk:
        result = my_sq(trigger)
        return result
"""
        try:
            self._compile_snippet(code)
        except TTSyntaxError:
            pass  # a clean TT error is acceptable
        # An AttributeError or any other non-TT exception is a bug

    def test_plain_name_call_unknown_raises_syntax_error(self):
        """Calling an un-SQified function inside @GRAPHify must raise TTSyntaxError."""
        from ticktalkpython.Error import TTSyntaxError

        code = """\
from ticktalkpython.SQ import SQify, GRAPHify

@GRAPHify
def main(trigger):
    from ticktalkpython.Clock import TTClock
    with TTClock.root() as clk:
        result = undefined_function(trigger)
        return result
"""
        with pytest.raises(TTSyntaxError):
            self._compile_snippet(code)

    def test_module_level_plain_assignment_allowed(self):
        """Simple string/int assignments at module level must not confuse the typechecker."""
        from ticktalkpython.Error import TTSyntaxError

        code = """\
import os

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

from ticktalkpython.SQ import SQify, GRAPHify

@SQify
def my_sq(trigger):
    return trigger

@GRAPHify
def main(trigger):
    from ticktalkpython.Clock import TTClock
    with TTClock.root() as clk:
        result = my_sq(trigger)
        return result
"""
        try:
            self._compile_snippet(code)
        except TTSyntaxError:
            pass
