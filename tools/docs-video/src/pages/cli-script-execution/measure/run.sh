#!/usr/bin/env bash
# The runs behind every value the CLI Script Execution video shows. Run from anywhere; writes runs.log next to
# this file. Each run is `doeff run --program shop.checkout.cart.total_program -` with one of scripts/ on stdin,
# from project/ (the example project, with auto-discovery markers). doeff-indexer 0.1.3 is what
# `pip install doeff-indexer` installs; `python -m doeff run` is the same command as `doeff run`. Unbuffered, so the
# log keeps the order a terminal shows stdout and stderr in.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../../../../.." && pwd)"
cd "$here/project"
doeff_run() {
  PYTHONUNBUFFERED=1 PYTHONPATH=. uv run --quiet --project "$repo" --with doeff-indexer==0.1.3 python -m doeff run "$@" 2>&1 | grep -v '^\s*$'
  echo "(exit ${PIPESTATUS[0]})"
}
with_script() {
  echo "\$ doeff run --program shop.checkout.cart.total_program - < scripts/$1"
  doeff_run --program shop.checkout.cart.total_program - < "$here/scripts/$1"
  echo
}
{
  echo "doeff $(git -C "$repo" rev-parse --short HEAD), doeff-indexer 0.1.3"
  echo
  echo "\$ doeff run --program shop.checkout.cart.total_program --format json"
  doeff_run --program shop.checkout.cart.total_program --format json
  echo
  for script in inspect.py rerun.py handlers.py fails.py; do with_script "$script"; done
} > "$here/runs.log"
