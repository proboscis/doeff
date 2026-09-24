#!/bin/sh
# usage: run.sh <label> <dwb: 1|unset> <prefix: set|unset> <pytest args...>
# 各 log の頭に HEAD・作業樹の差分の hash・env・命令を書く。
label=$1; dwb=$2; prefix=$3; shift 3
wt=$HOME/.worktrees/doeff-wt-ki-b02c57db9de8
log=/tmp/ki-b02c/logs/$label.log
cd "$wt" || exit 2
unset PYTHONDONTWRITEBYTECODE PYTHONPYCACHEPREFIX
[ "$dwb" = 1 ] && export PYTHONDONTWRITEBYTECODE=1
if [ "$prefix" = set ]; then pdir=$(mktemp -d /tmp/ki-b02c/prefix.XXXXXX); export PYTHONPYCACHEPREFIX=$pdir; fi
{
  echo "# label: $label"
  echo "# HEAD: $(git rev-parse HEAD)"
  echo "# worktree diff sha256 (tracked): $(git diff HEAD | sha256sum | cut -c1-16)"
  echo "# git diff --stat HEAD: $(git diff --stat HEAD | tail -1)"
  echo "# PYTHONDONTWRITEBYTECODE=${PYTHONDONTWRITEBYTECODE-<unset>} PYTHONPYCACHEPREFIX=${PYTHONPYCACHEPREFIX-<unset>}"
  echo "# command: uv run --no-sync pytest $*"
  echo
} > "$log"
PYTHONUNBUFFERED=1 timeout 900 uv run --no-sync pytest "$@" -p no:cacheprovider >> "$log" 2>&1
rc=$?
echo "# exit: $rc" >> "$log"
echo "$label: exit=$rc :: $(grep -E '^(=+ )?[0-9]+ (passed|failed|error)|(passed|failed|error).* in [0-9.]+s' "$log" | tail -1)"
