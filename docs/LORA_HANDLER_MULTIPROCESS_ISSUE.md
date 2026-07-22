# LoRa Handler Multi-Process Conflict

**Status:** Root cause confirmed (2026-07-22). Pragmatic mitigation shipped
(retry-with-backoff). Proper architectural fix not yet implemented.

**Severity:** Medium — reduces LoRa transmission reliability (a sensor-change
cycle occasionally gets skipped), but does not corrupt data or crash the
device. IP/cellular uplink, when enabled, is unaffected and provides a
working fallback path.

---

## Symptom

Devices intermittently log this even when nothing else is obviously wrong —
no other process, no stale deploy, no leftover process from a previous run:

```
🔧 Creating new LoRaHandler instance...
ℹ️ LoRa serial port already owned by another process; skipping handler creation in PID 1291
⚠️ LoRa handler unavailable (serial port busy) — skipping transmission this cycle
```

First observed and diagnosed on UFO010 (2026-07-21 through 2026-07-22).

## Root cause

`tools/lora_handler_concurrent.py` implements `get_lora_handler()` as a
process-wide singleton, guarded by a module-level `threading.Lock`
(`_lora_handler_lock`) plus an OS-level `fcntl.flock()` on
`/run/lock/watercam-lora.lock` (intended as a cross-process guard: "only one
process may hold the serial port at a time").

**The `threading.Lock` half of this design assumes all callers live inside
one process.** They don't. TickTalk's own runtime (`ticktalkpython/`)
schedules and executes the compiled dataflow graph's nodes across
**multiple, genuinely separate OS processes** — not just threads within one
process. Confirmed directly via a live process-tree capture on UFO010 (see
Evidence below): a worker process held a fully-initialized `LoRaHandler`
(and its flock), while a **child of that same worker process** independently
tried to construct its own `LoRaHandler` and got `LoRaSerialPortConflict`.

Each of these OS processes gets its own copy of the Python interpreter's
memory, including a fresh `_lora_handler = None` and a separate
`_lora_handler_lock` object. The in-process singleton pattern therefore only
ever protects callers *within a single one* of these processes — it provides
no sharing or coordination across the several processes that may
concurrently want LoRa access. The OS-level flock on
`/run/lock/watercam-lora.lock` is the *only* thing that's actually
cross-process here, and it is working exactly as designed: it correctly
prevents two processes from simultaneously holding the physical serial port.
The problem is architectural, not a bug in the locking logic itself — this
is a resource (one physical UART) that only one owner can hold, guarded by a
synchronization primitive (a per-process singleton) that was never designed
to arbitrate across several real, independent owners.

## Evidence

Confirmed via three rounds of live diagnostics run directly on UFO010 (added
as temporary instrumentation, then removed once conclusive — see git history
around commits `89d2c0f`, `be226ca`, `1b21e84`, `7bfcce4` on the `main`
branch for the false leads that were investigated and ruled out along the
way, described below).

**1. Ruled out: duplicate Python module instances.** A live diagnostic
printed `sys.modules` membership and `id(_lora_handler_lock)` at the exact
moment of conflict:

```
bare='lora_handler_concurrent' in sys.modules=False
qualified='tools.lora_handler_concurrent' in sys.modules=True
this_module='tools.lora_handler_concurrent'
lock_id=548251657856   # identical across every conflict observed
```

Only one module copy, one lock object, every time. (A *real* instance of
this class of bug — two independent module copies from an inconsistent bare
vs. package-qualified import — was found and fixed separately in
`tools/lora_runtime_integration.py` (`89d2c0f`) and
`tools/debug_status_command.py` (`be226ca`). Both were genuine bugs, but
neither explained this specific symptom once fixed — the conflict kept
recurring after both fixes shipped.)

**2. Ruled out: a leftover process from a previous manual test run.** A
full, cold `sudo reboot`, confirming `ps aux | grep -i python` showed
nothing running, then launching `runrtm.py` as the very first thing —
still reproduced the conflict on the first attempt.

**3. Ruled out: a leaked file descriptor from a prior failed construction
within the same process.** `LoRaHandler.__init__` opened the serial port
before checking the flock but never closed it on the conflict path — a real
bug, fixed in `1b21e84` — but repeated `lsof /dev/ttyAMA5` /
`lsof /run/lock/watercam-lora.lock` checks (both from the user, manually,
and programmatically from inside the failing process at the exact instant
of conflict) never showed more than one holder at a time, and that holder
was always a plausible legitimate owner, never an orphaned handle.

**4. Ruled out: ModemManager or another external service.** `ModemManager`
runs on this device (for the Quectel cellular modem) and is known to
generically probe serial ports at boot. Checked
`journalctl -u ModemManager` for any mention of `ttyAMA5` around boot —
none found. Also checked for a second, competing `watercam.service` unit
(the codebase's own older/alternate service, called out in
`docs/FIELD_DEPLOYMENT_GUIDE.md` as something that must not run alongside
`ticktalk.service`) — not installed on this device.

**5. Confirmed: separate OS processes, not threads.** A programmatic
diagnostic ran `lsof` and `ps -eo pid,ppid,etimes,cmd` from inside the
failing process at the exact moment of conflict (zero timing gap — no
reliance on a human running commands after the fact). The process snapshot
showed:

```
PID    PPID ELAPSED CMD
1277     771     2   python runrtm.py -s0 --timeout=0 output/ticktalk_main.pickle 8080
1278    1277     1   python runrtm.py -s0 --timeout=0 output/ticktalk_main.pickle 8080
1279    1277     1   python runrtm.py -s0 --timeout=0 output/ticktalk_main.pickle 8080
1280    1277     1   python runrtm.py -s0 --timeout=0 output/ticktalk_main.pickle 8080
1281    1277     1   python runrtm.py -s0 --timeout=0 output/ticktalk_main.pickle 8080
1291    1280     0   python runrtm.py -s0 --timeout=0 output/ticktalk_main.pickle 8080
```

and, at that same instant:

```
🔬 lsof /dev/ttyAMA5:
COMMAND  PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
python  1280   pi   24u   CHR 204,69      0t0  132 /dev/ttyAMA5

🔬 lsof /run/lock/watercam-lora.lock:
COMMAND  PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
python  1280   pi   29wW  REG   0,24        0    5 /run/lock/watercam-lora.lock
```

PID 1280 (a worker process spawned by the top-level `runrtm.py` invocation,
1277) held both the port and the lock. PID 1291 — **a child of 1280 itself**
— was the one reporting the conflict. Two genuinely separate OS processes,
in a parent/child relationship, both wanting the same physical hardware at
the same time.

## Current mitigation (shipped, `7bfcce4`)

`get_lora_handler()` now retries the flock acquisition up to 3 times with a
1-second delay between attempts before giving up, instead of failing
immediately on the first `LoRaSerialPortConflict`. This does not fix the
architecture — it's a pragmatic bet that whichever process currently holds
the port is short-lived (a per-node worker process that exits once its task
completes) and will release the port within a couple of seconds, giving the
losing process a real chance to succeed on a subsequent attempt rather than
unconditionally skipping the whole transmission cycle. It measurably helps
(observed successes on retry during the investigation) but does not
guarantee success under sustained contention, and adds up to ~2s of latency
to the failure path.

## Proposed proper fix: single-owner daemon + IPC

The codebase already has a working precedent for exactly this class of
problem: `tools/segformer_daemon.py`. The SegFormer ONNX model has the same
"one expensive/exclusive resource, wanted by code that may run in more than
one process" shape (there it's model load time + memory residency; here
it's exclusive ownership of one UART). The existing solution: **one
long-lived daemon process owns the resource permanently; every other
process talks to it over a Unix domain socket instead of touching the
resource directly.**

Concretely, mirroring that pattern for LoRa would look like:

1. **`tools/lora_daemon.py`** (new) — a persistent process that constructs
   exactly one `LoRaHandler` at startup and holds it for its entire
   lifetime. Listens on a Unix domain socket (e.g.
   `/run/lora/lora.sock`, matching `/run/segformer/segformer.sock`'s
   convention), accepting newline-delimited JSON requests
   (`{"action": "transmit_data", "data": {...}}`,
   `{"action": "transmit_file", ...}`, `{"action": "get_config_value", "key": ...}`,
   etc. — one action per existing `tools.lora_handler_concurrent` public
   function that callers currently invoke) and replying with
   `{"status": "ok", ...}` / `{"status": "error", "message": ...}`.

2. **`config/lora_daemon.service`** (new systemd unit) — modeled directly on
   `config/segformer_daemon.service`: `RuntimeDirectory=lora` (creates
   `/run/lora/` at boot, owned by `pi`), `Before=ticktalk.service` so the
   socket exists before the main application starts, `Restart=on-failure`,
   and an `ExecStartPost` health-check loop waiting for the socket file to
   appear before considering the unit started.

3. **Client side** — replace `tools/lora_handler_concurrent.py`'s direct
   `get_lora_handler()` singleton with a thin client (mirroring
   `ticktalk_main.py`'s existing `_segformer_via_daemon()` helper at
   line ~750): connect to the socket, send the request, read the response,
   with a short connect timeout so a missing/dead daemon fails fast rather
   than hanging a cycle. Every current call site
   (`transmit_data`, `transmit_file`, `queue_binary_transmit`,
   `get_config_value`, the LoRa command-decode path in
   `lora_handler_concurrent.LoRaHandler.decode()`, etc.) needs to route
   through this client instead of constructing/using a `LoRaHandler`
   directly.

4. **Fallback behavior**: decide whether an unreachable daemon should fall
   back to the current direct-construction path (matching `segformer()`'s
   "legacy subprocess fallback" pattern) or simply skip the cycle. Given the
   whole point is *one* process should own the hardware, a direct-construct
   fallback risks recreating this exact bug during the fallback window —
   worth deciding deliberately rather than copying the SegFormer pattern
   without thinking it through.

### Open questions for whoever implements this

- **Incoming LoRa messages**: `LoRaHandler.start_listening()` runs a
  background thread that processes *incoming* downlink messages via
  registered callbacks (`set_runtime_callback`, used by
  `tools/lora_runtime_integration.py` to apply remote parameter changes).
  In a daemon model, the daemon process holds these callbacks, but the
  callback logic (updating `runtime_config.json`) currently assumes it's
  running in the same process as everything else reading that config. Confirm
  this still behaves correctly with the daemon as a separate process (the
  file-based config with `fcntl` locking should still work correctly since
  it's already designed for multi-process access, but this needs to be
  verified, not assumed).
- **Emergency-mode/shutdown ordering**: `wittypi_emergency_control()` and
  `call_shutdown()` need the LoRa handler in specific ways (checking
  `is_joined()`, sending final status). Confirm the daemon stays alive
  through the main process's shutdown sequence, or that these call sites
  are updated to go through the client/daemon protocol too.
- **`get_config_value` / channel-decode dispatch**: some LoRa command
  handling currently lives on the `LoRaHandler` instance itself
  (`LoRaHandler.decode()`). Decide whether decode logic moves into the
  daemon (decoding happens where the handler lives) or stays client-side
  (daemon just proxies raw transmit/receive) — this affects how much of
  `tools/lora_handler_concurrent.py` moves into the new daemon file versus
  staying as shared/importable logic.
- **Testing**: the existing test suite
  (`tests/test_singleton_and_filtering.py`,
  `tests/test_lora_handler_concurrent.py`, etc.) is built around the
  in-process singleton and mocks `serial.Serial` directly. A daemon
  architecture needs a different test strategy — likely a test daemon
  bound to a `/tmp` socket path (matching `segformer_daemon.py`'s own
  `--socket /tmp/segformer_test.sock` testing convention) with a real
  client/server round trip, rather than mocking the transport away
  entirely.

## Affected files (current state)

- `tools/lora_handler_concurrent.py` — `get_lora_handler()` (singleton +
  retry mitigation), `LoRaHandler.__init__` (flock acquisition)
- `tools/lora_runtime_integration.py` — `LoRaRuntimeManager._init_lora_handler()`
  (the other main caller of `get_lora_handler()`)
- `ticktalk_main.py` — `initialize_lora_integration()`, `lora_listener()`,
  `lora_token_with_tracker()` (all call into the LoRa handler path per
  iteration)
- Reference implementation to mirror: `tools/segformer_daemon.py` +
  `config/segformer_daemon.service` + `ticktalk_main.py`'s
  `_segformer_via_daemon()` (~line 750) and `segformer()` (~line 791)
