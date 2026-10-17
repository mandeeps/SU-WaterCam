"""Every SQ in the compiled graph only uses names it has at runtime.

The runtime does not import ticktalk_main.py. It execs each SQ's source (the
function_source stored in output/ticktalk_main.pickle) in a fresh namespace
holding only SQify, STREAMify and sq_state (ticktalkpython/SQExecute.py
instantiate_at_runtime, and Engine.assign_func for persistent SQs). A name the
function gets from a module-level import or helper in ticktalk_main.py works
when the function is called from the module (tests calling __wrapped__) and
raises NameError on the node.

For each SQ in the committed pickle this test
  - execs its source in that namespace, as the runtime does, and checks the
    function is defined;
  - lists every name the source reads as a global (symtable, nested functions,
    lambdas and comprehensions included) and fails on any that is not a
    builtin, one of the runtime names, or defined by the source itself (the
    function, an import inside it, a `global` it assigns).
Static, so no hardware import has to be stubbed and no SQ body runs.

The last tests compile a graph with a deliberately broken SQ and check that it
is reported, and that it does raise NameError when run the runtime's way.
"""
import builtins
import os
import subprocess
import sys
import symtable
import textwrap

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from ticktalkpython import SQ as _SQ  # noqa: E402

PICKLE = os.path.join(REPO, "output", "ticktalk_main.pickle")

# What the runtime puts in an SQ's namespace (SQExecute.py, Engine.py).
RUNTIME_NAMES = {"SQify", "STREAMify", "sq_state"}


def runtime_namespace():
    return {"SQify": _SQ.SQify, "STREAMify": _SQ.STREAMify, "sq_state": {}}


def undefined_globals(source):
    """Names `source` reads as globals that the runtime namespace won't have."""
    top = symtable.symtable(source, "<sq>", "exec")
    tables = []

    def walk(t):
        tables.append(t)
        for child in t.get_children():
            walk(child)

    walk(top)

    defined = {s.get_name() for s in top.get_symbols()
               if s.is_assigned() or s.is_imported()}
    for t in tables[1:]:
        for s in t.get_symbols():
            if s.is_declared_global() and (s.is_assigned() or s.is_imported()):
                defined.add(s.get_name())
    available = set(dir(builtins)) | RUNTIME_NAMES | defined

    missing = set()
    for t in tables:
        for s in t.get_symbols():
            reads_global = (t is top) or s.is_global()
            if reads_global and s.is_referenced() and s.get_name() not in available:
                missing.add(s.get_name())
    return sorted(missing)


def load_sqs(path):
    from runrtm import unpack_graph
    graph = unpack_graph(path)
    return [sq for sq in graph.sqs
            if sq.interpreter is _SQ.TTInterpreter.Python3]


SQS = load_sqs(PICKLE)


def test_graph_has_sqs():
    names = {sq.function_name for sq in SQS}
    assert {"call_shutdown", "get_time", "lora_listener"} <= names


@pytest.mark.parametrize("sq", SQS, ids=[sq.sq_name for sq in SQS])
def test_sq_source_defines_its_function_in_the_runtime_namespace(sq):
    namespace = runtime_namespace()
    exec(compile(sq.function_source, "", "exec"), namespace)
    assert callable(namespace[sq.function_name])


@pytest.mark.parametrize("sq", SQS, ids=[sq.sq_name for sq in SQS])
def test_sq_uses_only_names_it_has_at_runtime(sq):
    missing = undefined_globals(sq.function_source)
    assert not missing, (
        f"{sq.function_name} uses {missing}, which the runtime does not provide "
        "(SQ bodies only get SQify, STREAMify and sq_state): import them inside "
        "the function, then rebuild the pickle with compile.py")


# --- the check catches a broken SQ ------------------------------------------------

BROKEN_GRAPH = textwrap.dedent('''
    from os.path import join
    from ticktalkpython.SQ import SQify, GRAPHify

    LIMIT = 3

    def helper(x):
        return x + 1

    @SQify
    def broken(trigger):
        import json
        return join("a", json.dumps(LIMIT)), helper(1), [undefined_name for _ in "x"]

    @SQify
    def fine(trigger):
        from os.path import join
        global counter
        counter = 1
        return join("a", "b"), counter, len(sq_state)

    @GRAPHify
    def ttmain(trigger):
        from ticktalkpython.Clock import TTClock
        with TTClock.root() as root_clock:
            a = broken(trigger)
            b = fine(a)
            return b
''')


@pytest.fixture(scope="module")
def broken_sqs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("broken")
    src = tmp / "brokengraph.py"
    src.write_text(BROKEN_GRAPH)
    subprocess.run([sys.executable, "compile.py", str(src), "--out", str(tmp)],
                   cwd=REPO, check=True, capture_output=True, timeout=120)
    return {sq.function_name: sq for sq in load_sqs(str(tmp / "brokengraph.pickle"))}


def test_broken_sq_is_reported(broken_sqs):
    assert undefined_globals(broken_sqs["broken"].function_source) == [
        "LIMIT", "helper", "join", "undefined_name"]


def test_correct_sq_is_not_reported(broken_sqs):
    assert undefined_globals(broken_sqs["fine"].function_source) == []


def test_broken_sq_raises_name_error_the_runtime_way(broken_sqs):
    sq = broken_sqs["broken"]
    namespace = runtime_namespace()
    exec(compile(sq.function_source, "", "exec"), namespace)
    with pytest.raises(NameError, match="join"):
        namespace[sq.function_name].__wrapped__(None)
