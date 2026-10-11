#!/bin/sh
# 使い方: run.sh <variant>  — 変種ごとに空の根・置き場・pycache を作り、setup → first → edit → second を別の process で走らせる。
set -e
v="$1"
base="/tmp/blind-a-sep/runs/$v"
rm -rf "$base"
mkdir -p "$base/root" "$base/store" "$base/pycache"
for phase in setup first edit second; do
  DOEFF_HY_CODE_STORE="$base/store" PYTHONPYCACHEPREFIX="$base/pycache" \
    uv run --project /home/ubuntu/.worktrees/doeff-at-ce9c19488 --no-sync python /tmp/blind-a-sep/probe.py "$v" "$phase" "$base/root" 2>"$base/$phase.err" || { echo "FAILED $phase"; cat "$base/$phase.err"; exit 1; }
  # mtime の解像度で .pyc の timestamp が source と同じ秒にならないよう 1 秒置く
  sleep 1
done
