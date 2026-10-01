#!/bin/sh
# packages/doeff-cluster の dir で doeff-linter を走らせる(commit の hook と make lint-doeff の両方が呼ぶ 1 か所)。
#
# なぜ dir に入るか: doeff-linter は今の dir から上へ [tool.doeff-linter] を探す。doeff の根で走る hook と make lint-doeff は根の
# pyproject.toml を読み、この package の設定(root = "src"・architecture = "architecture.hy")と service・不変条件の宣言を読まない。
#
# 使い方:
#   scripts/lint-doeff-cluster.sh                     package を丸ごと当てる(make lint-doeff)
#   scripts/lint-doeff-cluster.sh <repo の根からの path>…  変えた file を当てる(commit の hook — pre-commit が path を渡す)
# file を渡す時は architecture.hy を必ず足す: 宣言の規則(DOEFF163 など)は宣言の file を渡した時だけ判じるので、:invariants を
# 消す commit を止めるには宣言も当てる必要がある(2026-10-01 に確かめた — coordinator.hy だけを渡すと何も出ない)。
set -eu

TOP="$(git rev-parse --show-toplevel)"
PACKAGE="packages/doeff-cluster"

if ! command -v doeff-linter >/dev/null 2>&1; then
  echo "doeff-linter が未導入のため $PACKAGE を検査できません。成功として扱いません。" >&2
  echo "導入: cd packages/doeff-linter && cargo install --path ." >&2
  exit 127
fi

cd "$TOP/$PACKAGE"
if [ "$#" -eq 0 ]; then
  exec doeff-linter --no-log
fi

# repo の根からの path を package の dir からの path に直す(package の外の path は捨てる — pre-commit の files が絞るが、手で
# 呼んだ時のため)。architecture.hy は最後に 1 回だけ足す(渡された分は捨てて重ねない)。
for path in "$@"; do
  shift
  case "$path" in
    "$PACKAGE/architecture.hy") ;;
    "$PACKAGE"/*) set -- "$@" "${path#"$PACKAGE"/}" ;;
  esac
done
exec doeff-linter --no-log "$@" architecture.hy
