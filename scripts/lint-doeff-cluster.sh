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

# 置き場は script の在り処から求める(make -f で別の dir から呼ばれても package を指す)。
TOP="$(cd "$(dirname "$0")/.." && pwd)"
PACKAGE="packages/doeff-cluster"

if ! command -v doeff-linter >/dev/null 2>&1; then
  echo "doeff-linter が未導入のため $PACKAGE を検査できません。成功として扱いません。" >&2
  echo "導入: cd packages/doeff-linter && cargo install --path ." >&2
  exit 127
fi

# warning(major)は linter が 0 で終わるので、規則 × file の基点と比べて増えたら赤・減ったのに基点を下げていなければ赤
# (scripts/doeff_cluster_warning_baseline.py・agora-redesign #2683)。error は linter の終了コードで止める。
BASELINE_CHECK="$TOP/scripts/doeff_cluster_warning_baseline.py"

if [ "$#" -eq 0 ]; then
  (cd "$TOP/$PACKAGE" && doeff-linter --no-log)
  exec uv run --no-project python "$BASELINE_CHECK" --root "$TOP" check
fi

# 基点の比べは repo の根からの path をそのまま受ける(package の dir への読み替えは比べの側が持つ)ので、下で path を直す前に当てる。
uv run --no-project python "$BASELINE_CHECK" --root "$TOP" check "$@"

# repo の根からの path を package の dir からの path に直す(package の外の path は捨てる — pre-commit の files が絞るが、手で
# 呼んだ時のため)。architecture.hy は最後に 1 回だけ足す(渡された分は捨てて重ねない)。
for path in "$@"; do
  shift
  case "$path" in
    "$PACKAGE/architecture.hy") ;;
    "$PACKAGE"/*) set -- "$@" "${path#"$PACKAGE"/}" ;;
  esac
done
cd "$TOP/$PACKAGE"
exec doeff-linter --no-log "$@" architecture.hy
