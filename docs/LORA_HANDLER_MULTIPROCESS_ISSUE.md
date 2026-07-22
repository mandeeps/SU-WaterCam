# LoRa Handler Multi-Process Conflict

**Status:** Root cause confirmed (2026-07-22). Pragmatic mitigation shipped
(retry-with-backoff). Proper architectural fix (single-owner daemon + IPC)
implemented (2026-07-22) — see "Implemented fix" below. **Not yet installed
on any field device**; requires deploying `config/lora_daemon.service` (see
that section for the manual install steps).

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

## Implemented fix: single-owner daemon + IPC (2026-07-22)

Mirrors the codebase's existing precedent for exactly this class of problem,
`tools/segformer_daemon.py` (an expensive/exclusive resource wanted by code
that may run in more than one process — there, ONNX model load time and
memory residency; here, exclusive ownership of one UART): **one long-lived
daemon process owns the resource permanently; every other process talks to
it over a Unix domain socket instead of touching the resource directly.**

1. **`tools/lora_daemon.py`** — a persistent process that constructs exactly
   one `LoRaHandler` at startup (via the renamed
   `create_lora_handler_with_retry()` — the same retry-on-conflict
   construction logic that used to live directly in `get_lora_handler()`)
   and holds it for its entire lifetime. Listens on `/run/lora/lora.sock`
   (matching `/run/segformer/segformer.sock`'s convention) with a
   thread-per-connection accept loop, accepting newline-delimited JSON
   requests (`is_joined`, `queue_transmit`, `queue_binary_transmit`,
   `process_transmit_queue`, `transmit`, `get_queue_depth`,
   `get_size_limit`) and replying `{"status": "ok", "result": ...}` /
   `{"status": "error", "message": ...}`. Raw bytes values (e.g. a
   compressed flood bitmap) are base64-wrapped for the JSON transport by
   `_jsonify_bytes()`/`_unjsonify_bytes()` in `lora_handler_concurrent.py`.

2. **`config/lora_daemon.service`** — modeled directly on
   `config/segformer_daemon.service`: `RuntimeDirectory=lora`,
   `Before=ticktalk.service`, `Restart=on-failure`, an `ExecStartPost`
   health-check loop. **Not yet installed on any field device** — deploying
   this fix requires `sudo cp config/lora_daemon.service
   /etc/systemd/system/`, `sudo systemctl daemon-reload`,
   `sudo systemctl enable --now lora_daemon.service` on each unit, then
   confirming `/run/lora/lora.sock` exists before `ticktalk.service` starts.

3. **Client side**: `tools/lora_handler_concurrent.py`'s `get_lora_handler()`
   now returns a `LoRaHandlerClient` (connects to the socket fresh per call,
   mirroring `_segformer_via_daemon()`'s style) or `None` if the socket is
   missing — preserving the existing "callers must check for `None`"
   contract every call site already implements. `compressed_encoding()`,
   `get_config_value()`/`.config`, are computed/read locally rather than
   proxied (pure, or a plain file read — no daemon round-trip needed).
   `get_size_limit()` **is** proxied (corrected from the original proposal
   below, which assumed it was stateless — it isn't: it reflects the mDot's
   live, radio-condition-dependent `AT+TXS` payload limit, refreshed by the
   daemon's listener thread, and `compress_bitmap()` in `ticktalk_main.py`
   depends on the real current value to size bitmaps correctly).

4. **Dual-singleton fix (prerequisite, landed first)**:
   `tools/lora_runtime_integration.py` had two independent
   `LoRaRuntimeManager` singleton factories (`get_runtime_manager()` and
   `get_lora_runtime_integration()`); consolidated into one, since incoming
   emergency-mode messages always route through `get_runtime_manager()`
   specifically (`_listen_loop()`'s fast path calls the module-level
   `set_parameter()`), so the daemon's emergency-mode callback (below) has
   to attach to that exact instance.

5. **Emergency-mode safety path**: `LoRaRuntimeManager.__init__` now accepts
   an optional `lora_handler=` — when the daemon passes its own real,
   already-constructed handler, incoming-message wiring
   (`set_runtime_callback()`/`start_listening()`) happens directly,
   in-process, right there; every other (client-mode) process's
   `LoRaRuntimeManager` does *not* wire that (decode() needs the real
   handler's own state and now runs in exactly one place — the daemon). The
   daemon publishes its manager via the new `set_runtime_manager()` so
   `get_runtime_manager()` calls from `_listen_loop()`'s fast path reach the
   same instance, then registers `register_update_callback('emergency_mode',
   ...)` calling `tools.wittypi_control.apply_emergency_schedule()` directly
   — this also fixed an independent, real, pre-existing bug: the old
   callback called the bare name `wittypi_emergency_control()` (a sibling
   top-level `@SQify` function) from inside a nested closure, which always
   raised `NameError` under TTPython's isolated per-function exec runtime,
   silently caught by an overly-broad `except Exception` — meaning the
   WittyPi shutdown-schedule-clearing safety path had likely never actually
   run in production.

6. **Parameter change-detection reload**: `LoRaRuntimeManager.get_parameter()`
   now re-reads `runtime_config.json` when its `os.stat().st_mtime` has
   advanced since last read (cheap in the common case — a wake-cycle-scale
   device, not a tight loop), diffs old vs. new per key, and fires
   `register_update_callback()` hooks for keys that changed — otherwise a
   change written by the daemon's manager would never be observed by any
   other process's cached `self.parameters`, and callbacks registered on
   client-side instances (e.g. `ticktalk_main.py`'s `lora_listener()`
   callbacks) would silently stop firing for remotely-driven changes now
   that `decode()` runs server-side.

## Testing

`tests/test_lora_daemon.py` — a real client/server round trip for every RPC
action (a test daemon bound to a `/tmp` socket, mirroring
`segformer_daemon.py`'s own `--socket /tmp/segformer_test.sock` convention),
`get_lora_handler()`'s socket-existence contract, `LoRaRuntimeManager`
daemon-mode vs. client-mode wiring, `set_runtime_manager()`/
`get_runtime_manager()` publishing, and the change-detection reload
(including the "own write doesn't self-trigger a duplicate callback" case).
`tests/test_singleton_and_filtering.py` was retargeted from
`get_lora_handler()` to `create_lora_handler_with_retry()` (which now holds
that exact retry/thread-safety/leaked-FD behavior, called only by the
daemon). `tests/test_name_resolution.py` gained an isolated-exec regression
test (see caveat below) proving the `on_emergency_mode_changed` `NameError`
was real pre-fix and is gone post-fix.

**Caveat repeated from elsewhere in this codebase's test suite**: tests that
call an `@SQify` function via `.__wrapped__()` use the real module
`__globals__` and do NOT reproduce TTPython's actual isolated per-function
exec runtime — they cannot catch "resolves via a module-level import/sibling
function reference, missing the required local import" bugs. The existing
`lora_listener()` name-resolution test used exactly this style and would not
have caught the `on_emergency_mode_changed` bug; the new test uses
`_exec_in_isolated_sq_namespace()` instead, which faithfully reproduces the
isolation.

## Remaining work

- **Deploy `config/lora_daemon.service` to field devices** — implemented but
  not yet installed anywhere; see step 2 above.
- **Live verification on a real device once deployed**: confirm no more
  "already owned by another process" (including re-running the same
  process-tree/`lsof` capture from the Evidence section above to confirm the
  conflict is genuinely gone, not just less frequent), and specifically
  confirm an incoming LoRa emergency-mode command still clears the WittyPi
  shutdown schedule promptly end-to-end on hardware.
- The retry-with-backoff mitigation (`7bfcce4`) in
  `create_lora_handler_with_retry()` remains as defense-in-depth for the
  daemon's own startup (e.g. a stale leftover process still releasing the
  port during a daemon restart) — no longer the primary mitigation now that
  only one process ever constructs a real `LoRaHandler`.

## Affected files (final state)

- **New**: `tools/lora_daemon.py`, `config/lora_daemon.service`,
  `tests/test_lora_daemon.py`
- **`tools/lora_handler_concurrent.py`** — `get_lora_handler()` now returns a
  `LoRaHandlerClient`/`None`; `create_lora_handler_with_retry()` (renamed
  from the old `get_lora_handler()`) holds the real construction/retry
  logic, called only by the daemon; `_encode_compressed_packet()` extracted
  as a module-level pure function shared by both the real handler and the
  client; `LoRaHandler.get_queue_depth()` added
- **`tools/lora_runtime_integration.py`** — dual singleton consolidated;
  `LoRaRuntimeManager(lora_handler=...)` for daemon-mode construction;
  `set_runtime_manager()`; change-detection reload in `get_parameter()`
- **`tools/wittypi_control.py`** — `apply_emergency_schedule()` (shared,
  plain-bool-callable logic extracted from `wittypi_emergency_control()`)
- **`ticktalk_main.py`** — `wittypi_emergency_control()` now a thin wrapper;
  `lora_listener()`'s `on_emergency_mode_changed` calls
  `apply_emergency_schedule()` directly; `check_lora_availability()`
  repointed to `get_runtime_manager()`
- **Unchanged**: `tools/lora_store_forward.py` — its existing "mDot not
  joined" store-and-forward path in `ticktalk_main.py` (checked via
  `handler.is_joined()` before any `queue_transmit()` call) is unaffected by
  this change and still applies. A daemon that's unreachable for an entire
  cycle (`get_lora_handler()` returns `None`) skips that cycle's
  transmission entirely — the same behavior as before this daemon existed
  (`ticktalk_main.py:288-293`'s pre-existing `None` check), not a regression;
  `LoRaHandlerClient.queue_transmit()`/`queue_binary_transmit()` do not
  additionally enqueue to store-and-forward on a mid-cycle RPC failure,
  since `enqueue()`'s hex-payload shape doesn't cleanly compose at that
  layer for both the dict (`queue_transmit`) and pre-encoded
  (`queue_binary_transmit`) cases — narrow enough (daemon reachable at
  `is_joined()` but not moments later) not to be worth forcing.

Reference implementation this mirrors: `tools/segformer_daemon.py` +
`config/segformer_daemon.service`
