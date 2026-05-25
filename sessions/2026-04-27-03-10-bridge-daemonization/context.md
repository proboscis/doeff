# Session Context

## Goal

Make `orch-cmux-bridge` auto-runnable so the user does not have to remember to launch it manually. Final shape requested by the user: an **orch-style client/daemon architecture** where the user only ever invokes `orch-cmux-bridge sync`, and the script auto-spawns a daemon if one is not running and signals it if one is.

## Background

`orch-cmux-bridge` mirrors live orch agent runs into cmux workspaces 1:1 (built in a prior session). It has three internal modes — `sync` (one-shot reconcile), `watch` (long-running event subscriber), and `run` (sync + watch). Until this session it was started manually as `orch-cmux-bridge --remote zeus:7777 &`, which is fragile (forgotten across reboots, dies silently with the terminal).

This session pivoted twice:

1. Started by setting up **launchd** for auto-start on Mac login.
2. Hit a blocker (cmux socket auth refuses non-cmux-spawned processes) and the user clarified the real desired shape: **client/daemon architecture, no launchd**.

The user interrupted before the client/daemon refactor was implemented.

## Scope

- `/Users/s22625/dotfiles/scripts/orch-cmux-bridge` — single bash script, ~500+ lines. This is the only file the refactor will touch.
- `/Users/s22625/.local/bin/orch-cmux-bridge` — symlink to the script above. PATH entry the user invokes.
- Bridge log file: `/tmp/orch-cmux-bridge.log` (writable by user).
- Bridge lock dir: `${TMPDIR:-/tmp}/orch-cmux-bridge.lock.d` — `pid` file inside identifies the running daemon.

## Starting State

- Branch: `master` of `/Users/s22625/dotfiles`
- Last bridge commit before this session: `4b091ea Add cc profile overrides and shared resume` (no relevant bridge changes; bridge prior to today was at `79efe45 orch-cmux-bridge: multi-project aware sync`)
- Bridge worked manually via `&` invocation
- `Ctrl+C` did NOT terminate the bridge (trap on INT ran cleanup but did not exit, so watch loop resumed)
- No `resync` command — only way to re-trigger a sync was to kill+restart
- No daemon/client split — invocation runs the loop in-process

## Current State

- Branch: `master` of `/Users/s22625/dotfiles`
- New commit: `3588f7e Update Claude hooks and dotfiles tooling` (covers many files; bridge changes are within it)
- Bridge changes that landed in `3588f7e`:
  - `resync` subcommand: reads pid from lock dir, sends `SIGUSR1` to running daemon, exits without acquiring lock.
  - `INT`/`TERM` traps now call `cleanup; exit 130/143` so Ctrl+C terminates instead of resuming the watch loop.
  - `USR1` trap inside daemon: sets `resync_requested=1` and kills `events --follow` subprocess; main `do_watch` loop checks the flag after read interruption and runs `do_sync` between reconnects.
  - `cmux_call` now captures stderr and logs it on non-zero rc (previously stderr was discarded, hiding the cmux socket auth error).
- launchd plist `~/Library/LaunchAgents/com.user.orch-cmux-bridge.plist` was created during this session, then **DELETED**. `launchctl bootout` was run. No artifact remains on disk.
- No bridge process is running at session end (`pgrep -fl orch-cmux-bridge` empty).
- Lock dir `/tmp/orch-cmux-bridge.lock.d` does not exist.
- zeus Tailscale was diagnosed as broken during this session and fixed **by the user manually** (`ip link delete tailscale0`, then `systemctl restart tailscaled`). Tailscale to zeus is back: `tailscale ping zeus` returns pong in 7ms.
- The proposed client/daemon refactor was the in-progress task when the user interrupted with `/archive-session`. **No code for the refactor was written.**

## Key Files

| File | Role |
|---|---|
| `/Users/s22625/dotfiles/scripts/orch-cmux-bridge` | The bridge script. The refactor will edit the section starting at line ~112 (current `resync` early-exit handler) and line ~167 (arg parser), and the mode dispatch at the end (~line 488). |
| `/Users/s22625/.local/bin/orch-cmux-bridge` | Symlink to above. Live without rebuild. |
| `/tmp/orch-cmux-bridge.log` | Daemon stdout/stderr destination (currently chosen ad-hoc; the refactor should use this as `BRIDGE_LOG_FILE` default). |
| `${TMPDIR:-/tmp}/orch-cmux-bridge.lock.d/pid` | Lock dir pid file. The client reads this to find the daemon. |
| `~/Library/LaunchAgents/com.user.orch-cmux-bridge.plist` | DELETED. Was the launchd unit. Not needed under client/daemon architecture. |
