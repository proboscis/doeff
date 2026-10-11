#!/bin/sh
# 使い方: run_a.sh <doeff の木> <variant> <作業 dir> — 盲検 A の run.sh の写し(木を引数にした・中身の手順は同じ)
set -e
tree="$1"; v="$2"; base="$3/runs/$v"
rm -rf "$base"; mkdir -p "$base/root" "$base/store" "$base/pycache"
for phase in setup first edit second; do
  DOEFF_HY_CODE_STORE="$base/store" PYTHONPYCACHEPREFIX="$base/pycache" \
    uv run --project "$tree" --no-sync python "$(dirname "$0")/probe_a.py" "$v" "$phase" "$base/root" 2>"$base/$phase.err" || { echo "FAILED $phase"; cat "$base/$phase.err"; exit 1; }
  sleep 1
done
