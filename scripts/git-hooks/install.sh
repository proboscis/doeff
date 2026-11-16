#!/bin/sh
# Installs doeff's plain commit hook entry into the hooks dir every worktree of the clone uses (git rev-parse --git-path hooks).
#   sh scripts/git-hooks/install.sh [<doeff clone or worktree>]   (default = the repo of this file)
# The entry is a small forwarder that runs the committing tree's scripts/git-hooks/pre-commit, so each commit passes the checks
# of the tree it commits to, and a fix to that file takes effect on the next commit without installing again. The entry used
# to be a copy of the original taken once (make hooks-install, the agora worker Pod at its start): a check the original gained
# later reached no machine until the copy was taken again (the internal-leftovers check landed on 2026-11-14 while the shared
# hooks dirs kept the copy of 11-13, and tracker ids kept reaching main past commit time).
# An earlier copy of the original (it names make hooks-install) and an entry installed here are replaced; any other hook in
# the same place (for example the pre-commit framework's, from make pre-commit-install) is left as is and named, with exit 1.
set -u
MARK='doeff-repo-hook'
repo=${1:-"$(dirname "$0")/../.."}
hooks=$(cd "$repo" && git rev-parse --path-format=absolute --git-path hooks 2>/dev/null) || {
  echo "install: $repo is not a git repo" >&2
  exit 1
}
mkdir -p "$hooks" || exit 1
target="$hooks/pre-commit"
if [ -e "$target" ] && ! grep -q -e "$MARK" -e 'hooks-install' "$target"; then
  echo "install: another hook is at $target -- not replaced (run scripts/git-hooks/pre-commit from that hook)" >&2
  exit 1
fi
cat >"$target" <<'HOOK'
#!/bin/sh
# doeff-repo-hook: the entry scripts/git-hooks/install.sh installed -- runs the committing tree's scripts/git-hooks/pre-commit
# (a tree without that file is not checked). Edit the tree's file, not this entry.
top=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -f "$top/scripts/git-hooks/pre-commit" ] || exit 0
exec sh "$top/scripts/git-hooks/pre-commit" "$@"
HOOK
chmod +x "$target"
