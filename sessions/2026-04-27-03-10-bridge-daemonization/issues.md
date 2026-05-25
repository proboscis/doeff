# Issues

## Open

### cmux CLI refuses non-cmux-spawned invocations with "Failed to write to socket"

**Severity**: medium
**Discovered**: When the bridge ran under launchd, every `cmux ssh ...` and `cmux new-workspace ...` failed with this stderr:
```
Error: Failed to write to socket
```
**Details**:
- Manual invocation from a cmux-spawned shell works (`cmux ssh zeus --name "[ORCH-DBG] manual test" --no-focus` → `OK workspace=workspace:47 target=zeus state=connecting`).
- Same command from the launchd-spawned bridge fails.
- `cmux --help` documents Socket Auth precedence: `--password` flag, then `CMUX_SOCKET_PASSWORD` env, then password saved in cmux app Settings.
- A cmux-spawned shell has these env vars set: `CMUX_SOCKET`, `CMUX_SOCKET_PATH`, `CMUX_PANEL_ID`, `CMUX_SURFACE_ID`, `CMUX_WORKSPACE_ID`, `CMUX_BUNDLE_ID`. The CLI must use one of these for an implicit auth path that does not require a password.
- launchd's environment has none of these. The `PATH`/`HOME` set in the plist is not enough.
- `defaults read com.cmuxterm.app | grep -iE "socket|password"` returned only `socketControlPasswordMigrationVersion = 1` — implies a password was once set via Settings UI, but its value lives in keychain (extraction was denied by sandbox policy).

**Attempted fixes**:
- Setting `PATH` and `HOME` in plist (insufficient).
- Adding `BRIDGE_SSH_HOST_zeus` env to remap the ssh target (irrelevant — the failure was at cmux protocol level, not ssh).

**Hypothesis**:
- cmux CLI treats the presence of `CMUX_SURFACE_ID` (or similar) as proof the caller is a child of the cmux process tree, and uses an implicit auth token. Without it, it falls back to password auth, finds none, fails.

**Workaround chosen**:
- Pivot to client/daemon architecture (see todos.md item #1). The client is invoked from a cmux-spawned terminal and inherits the env, so when it spawns the daemon via `nohup`, the daemon also inherits the env and can talk to cmux.

### `orch --remote ...` has no client-side timeout

**Severity**: medium
**Discovered**: When zeus's tailscaled went into a stuck state, the bridge's first `orch --remote zeus:7777 daemon repo list --json` call hung indefinitely. launchd's `KeepAlive` did not help because the process was hung, not exited.
**Details**:
- The bridge calls `orch --remote zeus:7777 daemon repo list --json` from `list_orch_projects` and `orch --remote zeus:7777 --project <p> ps --json --no-alive --no-git` from `do_sync`. Neither has a timeout.
- During the zeus outage: `pgrep -fl orch.*daemon repo list` showed the call alive after 5+ minutes, no progress.
- `tailscaled` on zeus was at 98% CPU for 5.4 days; new TCP SYNs to zeus:7777 timed out at the network layer.
**Attempted fixes**: None — out of scope for this session.
**Hypothesis**: Either the orch client itself or the bridge wrapping it should set a timeout (e.g. `timeout 30 orch ...` in bash, or an `--timeout` flag if orch has one).

## Resolved

### Ctrl+C did not terminate the bridge

**Resolution**: Split the trap. Was `trap cleanup EXIT INT TERM` (cleanup ran but bash resumed). Now `trap cleanup EXIT; trap 'cleanup; exit 130' INT; trap 'cleanup; exit 143' TERM`.
**Root cause**: bash runs an INT trap and then resumes the previous command unless the trap explicitly exits. The watch loop's blocking `read` returned non-zero on signal, but the outer loop just treated it as "stream ended; reconnecting".
**Committed in**: 3588f7e

### No way to trigger a re-sync without restarting the bridge

**Resolution**: Added `orch-cmux-bridge resync` subcommand that reads the lock dir's pid file and sends `SIGUSR1` to the daemon. The daemon's USR1 trap sets `resync_requested=1` and kills the `orch events --follow` subprocess (forces `read` in the watch loop to return). The watch loop, after the inner `while read` exits, checks the flag and runs `do_sync` before re-establishing the events stream.
**Root cause**: Original design only supported sync at startup. There was no IPC into the running watch loop.
**Committed in**: 3588f7e

### zeus Tailscale was unreachable; tailscaled stuck in zombie state

**Resolution**: User ran `ip link delete tailscale0` on zeus, then restarted tailscaled. Tailscale recovered.
**Root cause**: A long-running tailscaled (uptime 5.4 days, 98% CPU) was killed during this session, became a zombie because systemd's cgroup state machine could not advance (`Failed to kill control group ... Invalid argument`). The zombie continued holding the `tailscale0` TUN device file descriptor, which blocked any replacement tailscaled from starting (`TUN device tailscale0 is busy`). Deleting the TUN interface freed it.
**Attempted fixes** (before user took over):
- `systemctl restart tailscaled` — hung
- `kill -9 2660` then `systemctl reset-failed tailscaled` — service stayed in `deactivating` state
- `tailscaled --cleanup` — ran successfully but did not unstick service state
- Spawn tailscaled directly with `--port=0` — failed because TUN was held by zombie
- `kill -CHLD 1` to nudge systemd reap — no effect
- `systemctl kill --signal=SIGKILL tailscaled.service` — `Failed to send signal SIGKILL to auxiliary processes: Invalid argument`
**Lesson**: When a TUN/socket-holding process zombifies and systemd cannot reap it, deleting the underlying network interface (`ip link delete <iface>`) can be enough to free the FD without rebooting.
