"""
Regression tests for TickTalkPython compilation of all TT source files.

Guards against TTSyntaxError regressions (undefined functions, bad module-
level calls, missing SQify decorators) and compiler crashes (AttributeError
inside the typechecker or compiler-rules visitor).

Each test compiles a single TT source file via compile.py and asserts that
the process exits 0.  A failing test means someone broke the file's TT
syntax and the issue must be fixed before merging.

Compiles go to a temporary directory: writing to output/ overwrote the
committed pickle every time the suite ran. Nodes run that committed pickle
(config/ticktalk.service), not the .py source, so a further test checks it is
what the current source compiles to. Compilation is deterministic, so any
difference means a source change was merged without rebuilding the pickle,
and the nodes are still running the old graph.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_COMPILE_SCRIPT = str(_REPO_ROOT / "compile.py")
_COMMITTED_DIR = _REPO_ROOT / "output"

# Files with a @GRAPHify entry point that must compile as standalone TT programs.
# Helper modules (tt_take_photos.py) are @SQify-only and compiled indirectly
# when ticktalk_main.py is compiled.
_TT_SOURCES = [
    "ticktalk_main.py",
]


@pytest.fixture(scope="module")
def compile_out(tmp_path_factory):
    return tmp_path_factory.mktemp("tt_compile")


def _run_compile(source_file: str, out_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, _COMPILE_SCRIPT, source_file, "--out", str(out_dir)],
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
def test_tt_compile_exits_zero(source_file, compile_out):
    """compile.py must exit 0 — any TTSyntaxError or crash is a failure."""
    pytest.importorskip("astor", reason="astor not installed; TT compiler unavailable")
    result = _run_compile(source_file, compile_out)
    assert result.returncode == 0, _format_failure(source_file, result)


@pytest.mark.parametrize("source_file", _TT_SOURCES)
def test_tt_compile_no_traceback(source_file, compile_out):
    """compile.py must not produce a Python traceback (crash ≠ TTSyntaxError)."""
    pytest.importorskip("astor", reason="astor not installed; TT compiler unavailable")
    result = _run_compile(source_file, compile_out)
    combined = result.stdout + result.stderr
    has_traceback = "Traceback (most recent call last)" in combined
    assert not has_traceback, (
        f"compile.py {source_file} crashed with a Python traceback "
        f"(exit {result.returncode}):\n{combined}"
    )


@pytest.mark.parametrize("source_file", _TT_SOURCES)
def test_tt_compile_no_syntax_error(source_file, compile_out):
    """compile.py must not emit a TTSyntaxError."""
    pytest.importorskip("astor", reason="astor not installed; TT compiler unavailable")
    result = _run_compile(source_file, compile_out)
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


@pytest.mark.parametrize("source_file", _TT_SOURCES)
def test_committed_pickle_matches_source(source_file, compile_out):
    """output/<name>.pickle must be what the current source compiles to."""
    pytest.importorskip("astor", reason="astor not installed; TT compiler unavailable")
    result = _run_compile(source_file, compile_out)
    assert result.returncode == 0, _format_failure(source_file, result)
    name = Path(source_file).stem + ".pickle"
    fresh = hashlib.sha256((compile_out / name).read_bytes()).hexdigest()
    committed = hashlib.sha256((_COMMITTED_DIR / name).read_bytes()).hexdigest()
    recorded = (_COMMITTED_DIR / (name + ".sha256")).read_text().split()[0]
    assert committed == recorded, (
        f"output/{name}.sha256 doesn't match output/{name}; commit both files together.")
    assert fresh == committed, (
        f"output/{name} is stale: {source_file} compiles to something else, so nodes "
        f"would run old code. Rebuild and commit both files:\n"
        f"    python compile.py {source_file} --out output\n"
        f"    git add output/{name} output/{name}.sha256")


# ── SQ bodies must be self-contained ────────────────────────────────────────

# Files whose @SQify/@STREAMify functions end up in the compiled graph.
_SQ_SOURCES = ["ticktalk_main.py", "tt_take_photos.py"]


def _module_level_names_used(path: Path):
    """(function, decorators, names) for each SQ that reads a module-level name.

    TickTalk runs each @SQify/@STREAMify function in its own process from that
    function's body alone, so a helper defined at module level is a NameError
    at run time, even though the file imports and compiles fine.
    """
    import ast
    import builtins
    import symtable

    src = path.read_text()
    tables = {t.get_name(): t for t in
              symtable.symtable(src, str(path), "exec").get_children()}

    def globals_read(table):
        names = {s.get_name() for s in table.get_symbols()
                 if s.is_global() and s.is_referenced()}
        for child in table.get_children():
            names |= globals_read(child)
        return names

    for node in ast.parse(src).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = {d.id for d in node.decorator_list if isinstance(d, ast.Name)}
        if not decorators & {"SQify", "STREAMify"}:
            continue
        names = sorted(globals_read(tables[node.name]) - set(dir(builtins)))
        if names:
            yield node.name, names


@pytest.mark.parametrize("source_file", _SQ_SOURCES)
def test_sq_bodies_use_no_module_level_names(source_file):
    offenders = list(_module_level_names_used(_REPO_ROOT / source_file))
    assert not offenders, (
        f"{source_file}: these SQs read names that only exist at module level, "
        f"which are undefined when TickTalk runs them; import them inside the "
        f"function instead: {offenders}")
