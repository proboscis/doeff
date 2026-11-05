#!/bin/sh
# Run doeff-linter in the packages/doeff-cluster dir (one place called by both the commit hook and make lint-doeff).
#
# Why enter the dir: doeff-linter looks upward from the current dir for [tool.doeff-linter]. The hook and make lint-doeff, which run at doeff's root,
# read the root pyproject.toml, and do not read this package's settings (root = "src", architecture = "architecture.hy") or the service and invariant declarations.
#
# Usage:
#   scripts/lint-doeff-cluster.sh                     apply to the whole package (make lint-doeff)
#   scripts/lint-doeff-cluster.sh <path from repo root>...  apply to changed files (commit hook -- pre-commit passes the paths)
# When passing files, always add architecture.hy: the declaration rules (DOEFF163 etc.) are judged only when the declaration file is passed, so to stop a commit that
# removes :invariants the declaration must be applied too (confirmed 2026-10-01 -- passing only coordinator.hy outputs nothing).
set -eu

# The location is derived from where the script is (it points at the package even when called from another dir with make -f).
TOP="$(cd "$(dirname "$0")/.." && pwd)"
PACKAGE="packages/doeff-cluster"

# warning (major) exits 0 in the linter, so compare with the rule x file baseline: red if it grew, and red if it fell but the baseline was not lowered
# (scripts/doeff_cluster_warning_baseline.py). error is stopped by the linter's exit code.
BASELINE_CHECK="$TOP/scripts/doeff_cluster_warning_baseline.py"

if [ "$#" -eq 0 ]; then
  # make lint-doeff: apply the whole package with the linter on the search path (the one installed by hand).
  if ! LINTER="$(command -v doeff-linter)"; then
    echo "doeff-linter is not installed, so $PACKAGE cannot be checked. This is not treated as success." >&2
    echo "To install: make sync puts .venv/bin/doeff-linter in place -- run with 'uv run make lint-doeff' (uv run puts .venv/bin at the front of PATH)" >&2
    exit 127
  fi
  (cd "$TOP/$PACKAGE" && "$LINTER" --no-log)
  # Pass the same linter to the baseline comparison too -- if not, the comparison script re-resolves the linter on uv run's search path, and since uv run
  # puts .venv/bin in front, it becomes a comparison of two versions: errors judged by the hand-installed linter and warnings counted by the .venv linter
  # (since 8ea159c5e, when doeff-linter entered the dev dependencies, even the fake linter of the make lint-doeff entry check was replaced by the real one in the comparison).
  exec uv run --no-project python "$BASELINE_CHECK" --root "$TOP" --linter "$LINTER" check
fi

# commit hook: measure not with the search-path linter but with the binary of the key of HEAD's build inputs (land-arm dev build -> snapshot store)
# (scripts/doeff_linter_locked.py) -- the warning baseline has no version, so if not counted with the tree's linter, red and green
# silently change with the version difference. If absent from the stores, name it "unmeasurable" and pass (nothing is built inside the hook -- the same as the root Python entry).
if ! LINTER="$(cd "$TOP" && uv run --no-project python scripts/doeff_linter_locked.py which)"; then
  echo "doeff-linter($PACKAGE): 測れない (unmeasurable) -- HEAD's linter binary is not in the stores (see the line above). Nothing is built inside the hook" \
    "-- wait for land-arm to rebuild main's linter, or merge main" >&2
  exit 0
fi

# The baseline comparison takes paths from the repo root as they are (the side that compares owns the translation to the package dir), so apply it before fixing the paths below.
uv run --no-project python "$BASELINE_CHECK" --root "$TOP" --linter "$LINTER" check "$@"

# Translate paths from the repo root to paths from the package dir (paths outside the package are dropped -- pre-commit's files narrows them, but this
# is for when called by hand). Add architecture.hy only once at the end (what was passed is dropped, not stacked).
for path in "$@"; do
  shift
  case "$path" in
    "$PACKAGE/architecture.hy") ;;
    "$PACKAGE"/*) set -- "$@" "${path#"$PACKAGE"/}" ;;
  esac
done
cd "$TOP/$PACKAGE"
exec "$LINTER" --no-log "$@" architecture.hy
