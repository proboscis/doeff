# Findings

## Bridge script state

- The bridge script committed at `3588f7e` lives at `/Users/s22625/dotfiles/scripts/orch-cmux-bridge`. Verified via `git -C /Users/s22625/dotfiles show --stat 3588f7e`. The commit added 57 lines and removed 3 across the bridge script, plus changes to other files in the same commit.
- `/Users/s22625/.local/bin/orch-cmux-bridge` is a symbolic link to `/Users/s22625/dotfiles/scripts/orch-cmux-bridge`. Verified via `ls -la` (`lrwxr-xr-x`).
- The script's lock dir constant is `LOCK_DIR="${TMPDIR:-/tmp}/orch-cmux-bridge.lock.d"`. The pid file is `$LOCK_DIR/pid`. Verified via direct file read of the script.
- The `cmux_call` helper at the time of session end has stderr capture: `out=$(cmux "$@" </dev/null 2>&1)`. Verified via `git show 3588f7e -- scripts/orch-cmux-bridge`.

## cmux CLI

- cmux's default socket path is `/Users/s22625/Library/Application Support/cmux/cmux.sock`. Verified via `env | grep CMUX_SOCKET` from a cmux-spawned shell.
- cmux CLI documents three socket auth sources (in order): `--password` flag, `CMUX_SOCKET_PASSWORD` env, password saved in app Settings. Verified via `cmux --help`.
- `defaults read com.cmuxterm.app | grep socket` shows `socketControlPasswordMigrationVersion = 1` and no plaintext password.
- A cmux-spawned shell has these env vars set: `CMUX_SOCKET`, `CMUX_SOCKET_PATH`, `CMUX_PANEL_ID`, `CMUX_SURFACE_ID`, `CMUX_WORKSPACE_ID`, `CMUX_TAB_ID`, `CMUX_BUNDLE_ID`, `CMUX_PORT`, `CMUX_PORT_END`, `CMUX_PORT_RANGE`, `CMUX_BUNDLED_CLI_PATH`, `CMUX_SHELL_INTEGRATION_DIR`, `CMUX_LOAD_GHOSTTY_ZSH_INTEGRATION`. Verified via `env | grep CMUX`.
- A launchd-spawned bash process does NOT inherit cmux env. Verified by observing that `cmux ssh zeus ...` from the launchd-bridge produced `Error: Failed to write to socket` while the same command from the cmux-spawned shell produced `OK workspace=workspace:47 target=zeus state=connecting`.
- `cmux ping`, `cmux capabilities`, `cmux version` are listed in `cmux --help` and could serve as health probes.

## orch on zeus

- zeus's orch daemon listens on `0.0.0.0:7777`. Verified via `ssh zeus pgrep -fl 'orch daemon'`: `orch daemon run --listen 0.0.0.0:7777`.
- `orch --remote zeus:7777 daemon repo list --json` returns 32 projects (entries) when zeus is reachable. Verified via the command after Tailscale recovery.
- The bridge's `list_orch_projects` filter (excludes `/tmp/`-rooted projects and names starting with `example-run-control-` / `orch-zeus-target-` / `orch-e2e-`) reduces the 32 entries to 5 active projects. Verified via `tail /tmp/orch-cmux-bridge.log` after sync: "sync: 5 projects discovered".
- During this session, 2 active runs were detected across all projects: short_id `a10227` and `83aafd`, both `TRD-142` in `proboscis-proboscis-ema`. Verified via the bridge log.

## zeus host state during the session

- zeus has 36 cores. Verified via `nproc`.
- zeus has 125 GiB RAM, 76 GiB used, 49 GiB available, 1.5 GiB swap used of 8 GiB. Verified via `free -h`.
- zeus uptime at session time: 5 days, 9 hours. Verified via `uptime`.
- zeus load average peaked at 756 / 731 / 656 during the session. Verified via `cat /proc/loadavg`.
- Total threads on zeus: 7829–7865. Verified via `ps -eLf | wc -l`.
- Top thread-spawning processes on zeus: minio (1685 threads), clickhouse-serv (1071), influxd (289), beam.smp/rabbitmq (88). Verified via `ps -eo pid,nlwp,pcpu,comm --sort=-nlwp`.
- Old tailscaled (pid 2660) had ELAPSED 464620 sec (5.4 days) at 98.7% CPU continuous before the user killed and restarted it. Verified via `ps -p 2660 -o pid,stat,pcpu,etimes`.
- After kill, pid 2660 became zombie (`Zsl`) and was not reaped by systemd within 30+ minutes despite multiple `systemctl reset-failed` and `kill -CHLD 1` attempts. Verified via repeated `ps -p 2660`.
- The `tailscaled.service` cgroup at `/sys/fs/cgroup/system.slice/tailscaled.service/cgroup.procs` continued to list pid 2660 even after `echo 1 > cgroup.kill`. Verified via `cat`.
- User resolved the stuck tailscaled by deleting the `tailscale0` TUN interface (`ip link delete tailscale0`) and restarting the service. After resolution, `tailscale ping zeus` from the Mac returned `pong from zeus (100.101.166.7) via [...]:41641 in 7ms`. Verified via `tailscale ping`.

## Network paths to zeus from this Mac

- Tailscale path: `zeus.taildd050.ts.net` → `100.101.166.7`. After user fix, RTT 7ms via DERP-Tokyo relay or direct UDP 41641. Verified via `tailscale ping zeus`.
- Home LAN path: `zeus.local` → `192.168.100.1` on `eno1`. Verified via `dscacheutil -q host -a name zeus.local` and `ping zeus.local` (3.5ms).
- mDNS resolution of `zeus.local` returns both IPv4 (192.168.100.1) and IPv6 (`2400:4051:4a20:7400:1a31:bfff:fe6b:4f50`). The IPv6 path was non-routable in this session ("no route to host"). Verified via `orch --remote zeus.local:7777 ...` failure log.
- ssh to zeus over home LAN requires `~/.ssh/id_zeus` identity (`ssh -G zeus` shows `identityfile ~/.ssh/id_zeus`). The Tailscale path uses the same identity via the `Host zeus` block in `~/.ssh/config`.

## Bridge log file

- The bridge writes to stderr-style format `[HH:MM:SS] <level>: <msg>` via `log()` and `err()` helpers. `log` goes to fd 2 (no separation between info and error in the file).
- During this session, the log path was `/tmp/orch-cmux-bridge.log` whenever the bridge was launched non-interactively (launchd plist's `StandardOutPath`/`StandardErrorPath`). Interactive invocations write to the calling terminal's stderr.

## Bash trap behavior

- bash's `read -r line < <(child)` returns non-zero with `$? > 128` when interrupted by a signal. Verified by observation: SIGUSR1 sent to the bridge while watch loop's read was blocked caused the read to return and the inner `while` loop to exit.
- `pkill -P $$` kills children of the current bash. `pkill -P $$ -f "events --follow"` filters to children whose argv contains "events --follow". Verified by observing that the trap killed `orch events --follow` without affecting other children.
- A trapped `INT` does NOT cause bash to exit — it runs the trap and resumes. Verified by the original `trap cleanup EXIT INT TERM` not terminating the bridge on Ctrl+C.
