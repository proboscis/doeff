# Reproduction Guide

## Environment

**Repo**: `/Users/s22625/dotfiles`
**Branch**: `master`
**Commit**: `3588f7e` (covers the bridge changes from this session)
**Date**: 2026-04-27

The bridge script lives at `/Users/s22625/dotfiles/scripts/orch-cmux-bridge` with the public symlink at `/Users/s22625/.local/bin/orch-cmux-bridge`. The orch daemon being mirrored runs on zeus at `zeus:7777` over Tailscale.

## data/bridge.log

**What**: Captured `/tmp/orch-cmux-bridge.log` at the end of the session. Shows the launchd-spawned bridge's output, which demonstrates two interesting states: (a) successful sync via Tailscale (5 projects, 2 active runs detected), (b) the cmux socket auth failure pattern (`Error: Failed to write to socket`) that motivated the pivot away from launchd.
**Command** (to reproduce a similar log against current zeus state):
```bash
rm -rf /tmp/orch-cmux-bridge.lock.d
: > /tmp/orch-cmux-bridge.log
nohup /Users/s22625/.local/bin/orch-cmux-bridge --remote zeus:7777 \
  >> /tmp/orch-cmux-bridge.log 2>&1 &
disown
sleep 25
cat /tmp/orch-cmux-bridge.log
```
**Prerequisites**:
- cmux app must be running for the cmux CLI calls to have a target socket.
- zeus must be reachable on Tailscale: `tailscale ping zeus` returns pong.
- orch CLI installed at `/Users/s22625/.local/bin/orch`.
- The bridge must NOT already be running (the lock would block a second instance).
- Run from a **cmux-spawned shell** to avoid the cmux socket auth failure that the captured log shows. To reproduce the failure path captured in the file, run from a non-cmux context (e.g. a launchd-spawned process) — but that requires re-creating the deleted plist.

**Reproducible?**: Partial.
- The "5 projects discovered, 2 active runs" line depends on what is actively running on zeus's orch daemon at the moment of capture. Re-running today will likely show different short_ids (a10227 / 83aafd were the live runs at session time).
- The `cmux ssh-create failed (rc=1)` lines specifically reproduce only when the bridge is launched without inheriting cmux env. From a cmux-spawned terminal, those lines will not appear — workspaces will create successfully.
