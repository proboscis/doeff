# Decisions

## D1: Use SIGUSR1 for resync IPC instead of a named pipe or coproc

**Choice**: The client sends `SIGUSR1` to the daemon's pid (read from `LOCK_DIR/pid`). The daemon's trap sets a flag and kills its `orch events --follow` child to wake the blocking `read`. The watch loop checks the flag between reads and runs `do_sync`.
**Why**: bash's `read` is blocking, so a polling-based design is unworkable. Signals interrupt blocking reads cleanly. Named pipes / `coproc` would have worked but require more bash plumbing and handle accounting; SIGUSR1 is one line of trap.
**Alternatives considered**:
- A named pipe at a known path that the watch loop reads from. Rejected because we'd need a select-style loop on two FDs (events stream + control pipe) which bash does not natively support.
- `coproc` for the events stream so we can track its pid for clean kill. Rejected because the trap's `pkill -P $$ -f "events --follow"` already finds the right child, and `coproc` adds compat issues across bash versions.
**Reversible**: yes — internal IPC, no external API.

## D2: Daemon trap kills the events --follow subprocess on SIGUSR1, accepting noisy reconnect log

**Choice**: The USR1 trap does `pkill -P $$ -f "events --follow"`. This makes the daemon log "stream ended" each resync and re-establish the events connection right after `do_sync`.
**Why**: The watch loop's `read` only returns when (a) the events stream ends or (b) the stream child is killed. Without (b), SIGUSR1 sets the flag but the flag is not checked until the next event arrives — could be hours.
**Alternatives considered**:
- Use `read -t <timeout>` to make the read non-blocking and poll the flag. Rejected: adds latency to event dispatch, and short timeouts mean the read returns frequently with no event (busy loop).
**Reversible**: yes.

## D3: Split INT/TERM traps with explicit exit codes; keep EXIT trap for catch-all

**Choice**:
```bash
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
```
**Why**: bash resumes after a trapped INT unless the trap exits. The original `trap cleanup EXIT INT TERM` ran cleanup (which killed the events child) but then the outer watch loop saw the dead child as "stream ended" and reconnected — so Ctrl+C effectively did nothing. The exit codes 130/143 follow shell convention (128 + signal number).
**Alternatives considered**: `trap cleanup INT TERM EXIT` plus `set -e` — rejected, behavior under bash subshells is murky.
**Reversible**: yes.

## D4: Pivot from launchd to client/daemon architecture (per user request)

**Choice**: Drop the launchd plist. Refactor the script so `orch-cmux-bridge sync` is a client that auto-spawns a daemon if one is not running, and signals it if one is.
**Why**: User's words: *"i only care about running the bridge sync, and it automatically starts a daemon if not started. and daemon does all the work."* This mirrors the orch CLI's daemon-client UX. Side benefit: the spawned daemon inherits the cmux env vars from the client's terminal, sidestepping the cmux socket auth issue that blocked the launchd path.
**Alternatives considered**:
- Continue with launchd and inject `CMUX_SOCKET_PASSWORD`. Rejected by user request and because extracting the password from cmux Settings requires either keychain access (denied by sandbox) or asking the user to copy it from cmux's UI.
- Auto-start from `~/.zshrc`. Rejected (briefly considered) because it would race with cmux app startup and run multiple times across new shells (lock would protect against double-spawn but it's noisy).
**Reversible**: yes — launchd plist could be re-added later if hands-off auto-start across cmux restarts becomes important.

## D5: cmux_call captures stderr, logs only on failure

**Choice**: `out=$(cmux "$@" </dev/null 2>&1)` then on rc != 0, log the captured output.
**Why**: Original code did `>/dev/null 2>&1` which discarded everything. When launchd-spawned `cmux ssh` started failing, the bridge log only said "rc=1" with no clue what cmux actually said. After this change, the log showed `Error: Failed to write to socket`, which directly named the cmux socket auth issue.
**Alternatives considered**:
- Log stderr always (not just on failure). Rejected: cmux occasionally prints harmless warnings on success that would clutter the log.
**Reversible**: yes — single function.

## D6: Refactor will use `nohup ... & disown` for daemon detachment, not `setsid`

**Choice** (planned, not yet implemented): The client will spawn the daemon with `nohup "$0" daemon "$@" >> "$BRIDGE_LOG_FILE" 2>&1 & disown`.
**Why**: `nohup` immunizes the daemon to SIGHUP if the controlling terminal dies. `&` backgrounds. `disown` detaches from the client's job table so client exit does not signal the daemon. `setsid` would also work but requires installing a util on macOS and creates a new session group (overkill — we just need detachment).
**Alternatives considered**: `setsid` (overkill), `daemon` external (not standard on macOS).
**Reversible**: yes — daemon spawn is one site in the script.
