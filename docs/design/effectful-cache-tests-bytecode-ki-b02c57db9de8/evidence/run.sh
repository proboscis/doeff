#!/usr/bin/env bash
# usage: run.sh <label> <env:1|unset> <pytest args...>
set -o pipefail
L=/tmp/ki-b02c57db9de8; W=/home/kento/.worktrees/doeff-wt-ki-b02c57db9de8-design
label=$1; e=$2; shift 2
cd $W
if [ "$e" = 1 ]; then export PYTHONDONTWRITEBYTECODE=1; else unset PYTHONDONTWRITEBYTECODE; fi
{
  echo "# label=$label"
  echo "# base=$(git rev-parse HEAD) worktree=$W"
  echo "# PYTHONDONTWRITEBYTECODE=${PYTHONDONTWRITEBYTECODE-<unset>} PYTHONPYCACHEPREFIX=${PYTHONPYCACHEPREFIX-<unset>}"
  echo "# diff: $(git status --short | tr '\n' ' ')"
  echo "# diff sha256: $(git diff | sha256sum | cut -c1-16)"
  echo "# cmd: python -m pytest $*"
  PYTHONPATH=$W:$W/packages/doeff-core-effects timeout 600 /home/kento/repos/doeff/.venv/bin/python -m pytest "$@" -W ignore -p no:randomly 2>&1
  echo "# exit=$?"
} > $L/$label.log 2>&1
grep -E "passed|failed|error|^FAILED|^ERROR|# exit=" $L/$label.log | tail -12
