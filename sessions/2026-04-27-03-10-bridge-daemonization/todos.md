# Todos

## Completed (this session)

- [x] Add `resync` subcommand that signals a running daemon via SIGUSR1 (committed in 3588f7e)
- [x] Fix Ctrl+C: split INT/TERM traps with explicit exit codes (committed in 3588f7e)
- [x] Have `cmux_call` capture and log stderr on failure so future debugging shows the actual cmux error (committed in 3588f7e)
- [x] Wire `resync_requested` flag through `do_watch` so SIGUSR1 triggers `do_sync` between watch iterations (committed in 3588f7e)
- [x] Diagnose zeus Tailscale outage and surface root cause (zombie tailscaled holding tailscale0 TUN). User fixed manually with `ip link delete tailscale0`.

## Prioritized Backlog

| # | Priority | Task | Status |
|---|----------|------|--------|
| 1 | high | Refactor bridge to client/daemon architecture (see "Refactor design" below) | next |
| 2 | medium | Add a timeout to all `orch --remote ...` invocations inside the bridge so an unreachable daemon does not cause the bridge to hang forever (root cause of the zeus outage hang during this session) | next |
| 3 | low | Decide whether to add a `cmux ping` health probe at daemon start so the daemon exits cleanly if cmux app is not running (avoids spawning a daemon that will only emit "Failed to write to socket" forever) | later |
| 4 | low | If the user later wants launchd-style hands-off auto-start, document the cmux socket auth path: either set `CMUX_SOCKET_PASSWORD` env (requires extracting the password from cmux Settings) or have launchd shell out via cmux's startup hook so the env is inherited. Not needed under #1. | later |

## Refactor design (item #1)

User's exact words: *"i only care about running the bridge sync, and it automatically starts a daemon if not started. and daemon does all the work."*

### Target CLI

| Command | Behavior |
|---|---|
| `orch-cmux-bridge sync [flags]` | **Client.** If a daemon is alive, send SIGUSR1 (resync). If not, spawn the daemon detached, wait for the lock to appear, return. |
| `orch-cmux-bridge` (no args) | Same as `sync`. The default invocation is the client path. |
| `orch-cmux-bridge status` | Print "running (pid=N)" or "not running". |
| `orch-cmux-bridge stop` | Send SIGTERM to the running daemon, wait, escalate to SIGKILL after 2.5s. |
| `orch-cmux-bridge logs` | `tail -F /tmp/orch-cmux-bridge.log`. |
| `orch-cmux-bridge daemon [flags]` | **Internal.** The long-running loop (existing `run` mode body: acquire lock, do_sync, do_watch). Spawned by the client, not normally invoked by hand. |
| `orch-cmux-bridge resync` | Alias for `sync` (kept for backward compat — already in 3588f7e as a SIGUSR1-only path). |
| `orch-cmux-bridge watch` | Existing — kept for debugging, no change. |
| `orch-cmux-bridge run` | Existing — keep as alias for `daemon`. |

### Implementation notes

- Spawn pattern in the client: `nohup "$0" daemon "$@" >> "$BRIDGE_LOG_FILE" 2>&1 & disown`. The script must propagate `--remote`, `--issue`, `--run`, `--orch` to the spawned daemon. Strip the `sync` arg before the spawn.
- After spawn, the client polls `$LOCK_DIR/pid` for up to 5s (10× 500ms). If the lock does not appear, report failure and point at the log file.
- Client modes (`sync`, `resync`, `status`, `stop`, `logs`) **must NOT call `acquire_lock`** — they are dispatched before the lock acquisition block at line 133. Daemon modes (`daemon`, `run`, `watch`) acquire the lock as today.
- The existing early-exit `if [[ "${1:-}" == "resync" ]]` block at lines 112–131 should be replaced with a dispatcher that handles all client modes. Argument parsing (lines 167–184) needs to add `daemon|status|stop|logs` to the recognized mode list.
- Default mode (line 165) changes from `mode="run"` to `mode="sync"` so a no-arg invocation is the client path.
- Mode dispatch at end of file (~line 488) needs a new branch for client modes that delegates to the dispatcher and exits.
- Spawned daemon will inherit the env of the cmux-spawned shell that ran the client, which solves the cmux socket auth issue that blocked launchd.

### Testing checklist for the refactor

1. From a cmux terminal: `orch-cmux-bridge sync` — expect it to spawn, sync 5 projects/2 runs (zeus state), return to prompt; daemon visible in `pgrep -fl orch-cmux-bridge`.
2. `orch-cmux-bridge sync` again — expect "daemon alive (pid=N); requesting resync" log line, no new process spawned.
3. `orch-cmux-bridge status` — expect "running (pid=N)".
4. `orch-cmux-bridge logs` — expect tail of /tmp/orch-cmux-bridge.log.
5. `orch-cmux-bridge stop` — expect SIGTERM, daemon gone, lock cleared.
6. `orch-cmux-bridge sync` after stop — expect re-spawn.
7. `Ctrl+C` on the daemon (from a `daemon` foreground invocation) — expect immediate termination (regression check on the trap fix).

## Blocked

- [ ] None.
