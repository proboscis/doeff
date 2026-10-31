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

# warning(major)は linter が 0 で終わるので、規則 × file の基点と比べて増えたら赤・減ったのに基点を下げていなければ赤
# (scripts/doeff_cluster_warning_baseline.py)。error は linter の終了コードで止める。
BASELINE_CHECK="$TOP/scripts/doeff_cluster_warning_baseline.py"

if [ "$#" -eq 0 ]; then
  # make lint-doeff: 探し道の linter(手で入れた物)で package を丸ごと当てる。
  if ! LINTER="$(command -v doeff-linter)"; then
    echo "doeff-linter が未導入のため $PACKAGE を検査できません。成功として扱いません。" >&2
    echo "導入: make sync で .venv/bin/doeff-linter が入る — 「uv run make lint-doeff」 で走らせる(uv run が .venv/bin を PATH の先に置く)" >&2
    exit 127
  fi
  (cd "$TOP/$PACKAGE" && "$LINTER" --no-log)
  # warning の基点の比べにも同じ linter を渡す — 渡さないと比べの script は uv run の探し道で linter を引き直し、uv run が
  # .venv/bin を前に置くので、手で入れた linter で error を判じ .venv の linter で warning を数える 2 つの版の比べになる
  # (dev の依存に doeff-linter が入った 8ea159c5e から、make lint-doeff の入口の検の偽の linter も比べで本物に替わっていた)。
  exec uv run --no-project python "$BASELINE_CHECK" --root "$TOP" --linter "$LINTER" check
fi

# commit の hook: 探し道の linter でなく、HEAD の組み立ての入力の鍵の binary(land-arm の開発版 → 断面の置き場)で測る
# (scripts/doeff_linter_locked.py)— warning の基点は版を持たないので、木の linter で数えないと版の差で
# 赤・緑が黙って変わる。置き場に無ければ「測れない」と名指して通す(hook の中では組まない — 根の Python の項と同じ)。
if ! LINTER="$(cd "$TOP" && uv run --no-project python scripts/doeff_linter_locked.py which)"; then
  echo "doeff-linter($PACKAGE): 測れない — HEAD の linter の binary が置き場に無い(上の 1 行)。hook の中では組まない" \
    "— land-arm が main の linter を組み直すのを待つか、main を取り込む" >&2
  exit 0
fi

# 基点の比べは repo の根からの path をそのまま受ける(package の dir への読み替えは比べの側が持つ)ので、下で path を直す前に当てる。
uv run --no-project python "$BASELINE_CHECK" --root "$TOP" --linter "$LINTER" check "$@"

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
exec "$LINTER" --no-log "$@" architecture.hy
