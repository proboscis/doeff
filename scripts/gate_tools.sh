#!/bin/sh
# 日次の全体検証(.agents/land-queue.toml の gate.full)が走る所の道具の依存の宣言の 1 点と、その準備(agora-redesign #3870・
# card ki-9338eec1d15e)。
#
# 日次の全体検証は 2026-10-10 から、日次の task が走っている所(atlas の doeff-worker の Pod)で直に走る(ADR-DOE-ENFORCE-001 R10 —
# それまでは remote_check で木を zeus の上へ送って走っていた)。テストと検査が探す道具は、走る所の側に宣言して用意する(2026-10-08 8:00
# の回で packages/doeff-events/tests/test_notice_laws_redis.py が 14 本とも skip だった — zeus の PATH に redis-server が無かった)。
#
#   sh scripts/gate_tools.sh path
#     宣言の道具を全部そろえ、PATH の前に載せる dir を「dir:」の形で標準出力へ書く(足す物が無ければ空)。道具ごとの順:
#       1. PATH に宣言の版が在れば何も足さない(日次の task が HOME の .local/bin — PATH の先頭 — に置く宣言の版)。
#       2. cache の dir($HOME/.cache/<名>-<版>)に宣言の版が在れば、その dir。
#       3. docker が在れば、宣言の image(digest つき)から実行 file 1 つを取り出して cache の dir に置き、その dir。
#     そろえられなければ、道具の名・版・image・探した PATH を標準エラーへ書いて 1(処理ステージは赤 — 黙って skip にしない)。
#
#   sh scripts/gate_tools.sh linter
#     基点の file(scripts/hook_finding_baseline/doeff-linter.json)が名乗る鍵の doeff-linter を、基点の比べ
#     (scripts/hook_finding_baseline.py --strict check doeff-linter)が探す断面の置き場に用意する。標準出力には何も書かない。
#     用意できなければ理由を標準エラーへ書いて 1(処理ステージは赤)。
#
# 使い手 = .agents/land-queue.toml の処理ステージ packages(make test-packages の前)と lint(基点の比べの前)。
# 失敗ケース = tests/test_gate_tools.py。

# redis-server — 使い手 packages/doeff-events/tests/test_notice_laws_redis.py(PATH の redis-server を一時の dir で立てて本物の Redis で
# 配達の法を確かめる)。版と image は agora の deploy/verify-worker-image/Dockerfile の REDIS_VERSION・REDIS_IMAGE と同じ値(本番
# deploy/agora-events の redis:7.4.11 と同じ版)。本番の版を上げる時は 3 か所を同じ版へ。
REDIS_VERSION=7.4.11
REDIS_IMAGE=redis:7.4.11-bookworm@sha256:4fa24486b8bcca8eec45ee0eb166edc674795e53a2b53d1a9ef263eecebaac85
REDIS_IN_IMAGE=/usr/local/bin/redis-server
REDIS_CACHE="$HOME/.cache/redis-server-$REDIS_VERSION"

# 実行 file $1 が宣言の版の redis-server か(`--version` の答えに「v=<版> 」が在る)。
is_declared_redis() {
  "$1" --version 2>/dev/null | grep -q "v=$REDIS_VERSION "
}

# 宣言の image から redis-server を取り出して cache の dir に置く(取り出した物が宣言の版でなければ置かない)。
fetch_redis() {
  staging=$(mktemp -d "${TMPDIR:-/tmp}/gate-tools.XXXXXX") || return 1
  container=$(docker create "$REDIS_IMAGE") || { rmdir "$staging"; return 1; }
  docker cp "$container:$REDIS_IN_IMAGE" "$staging/redis-server"
  copied=$?
  docker rm "$container" > /dev/null
  if [ "$copied" -eq 0 ] && chmod 755 "$staging/redis-server" && is_declared_redis "$staging/redis-server"; then
    mkdir -p "$REDIS_CACHE" && mv "$staging/redis-server" "$REDIS_CACHE/redis-server" && rmdir "$staging"
    return
  fi
  rm -rf "$staging"
  return 1
}

# PATH の前に載せる redis-server の dir を書く(PATH に宣言の版が在れば何も書かない)。
redis_path() {
  found=$(command -v redis-server)
  if [ -n "$found" ] && is_declared_redis "$found"; then
    return
  fi
  if [ -x "$REDIS_CACHE/redis-server" ] && is_declared_redis "$REDIS_CACHE/redis-server"; then
    printf '%s:' "$REDIS_CACHE"
    return
  fi
  if command -v docker > /dev/null && fetch_redis; then
    printf '%s:' "$REDIS_CACHE"
    return
  fi
  printf 'gate_tools: redis-server %s を用意できない — 探した PATH(%s)にも %s にも宣言の版が無く、image %s からも取り出せない(docker が無いか、取り出しに失敗した)\n' \
    "$REDIS_VERSION" "$PATH" "$REDIS_CACHE" "$REDIS_IMAGE" >&2
  return 1
}

# doeff-linter — 使い手 = 処理ステージ lint の基点の比べ。比べは基点の file が名乗る組み立ての入力の鍵と同じ鍵の binary を、land-arm の
# 開発版($HOME/.local/share/doeff-linter-dev)→ 断面の置き場($HOME/.cache/doeff-linter-snapshots)の順に探し、組まない
# (scripts/doeff_linter_locked.py — 置き場は HOME の下の決まった場所で環境変数では替えない)。zeus では land-arm と断面の置き場が
# 在ったが、日次の task の HOME は走行ごとに空の dir で、どちらも無い — 用意しないと --strict の「測れない」(rc 3)で段が赤になる。
# 基点の鍵は linter を変えた後の commit の鍵と同じとは限らない(2026-10-10: 基点 = 780ef711a の鍵・main の先端の鍵は別)ので、組むのは
# HEAD でなく基点の file を最後に書いた commit。組み方は断面の道具 packages/doeff-linter/scripts/linter_snapshot.py の 1 か所(sha の
# dir に在れば組まない・同じ鍵の linter が在れば写す・無ければ cargo で 1 度だけ組む — 冷えた組み立ては 3〜5 分)。
# 呼び手が走行をまたいで残る置き場を DOEFF_LINTER_SNAPSHOT_DIR で渡せば(日次の task は worker の作業の根の
# daily-verify-cache/doeff-linter-snapshots)、断面の置き場をそこへの symlink にして、組んだ linter を次の走行へ残す。
LINTER_BASELINE=scripts/hook_finding_baseline/doeff-linter.json
LINTER_STORE="$HOME/.cache/doeff-linter-snapshots"

# 基点の鍵の doeff-linter を断面の置き場に用意する。
linter_prepare() {
  top=$(cd "$(dirname "$0")/.." && pwd) || return 1
  kept="${DOEFF_LINTER_SNAPSHOT_DIR:-}"
  if [ -n "$kept" ] && [ "$kept" != "$LINTER_STORE" ] && [ ! -e "$LINTER_STORE" ] && [ ! -L "$LINTER_STORE" ]; then
    mkdir -p "$kept" "$(dirname "$LINTER_STORE")" && ln -s "$kept" "$LINTER_STORE" || return 1
  fi
  commit=$(git -C "$top" log -1 --format=%H -- "$LINTER_BASELINE")
  if [ -z "$commit" ]; then
    printf 'gate_tools: doeff-linter の基点 %s を書いた commit が %s の git に無い — 基点の鍵の linter を組めない\n' \
      "$LINTER_BASELINE" "$top" >&2
    return 1
  fi
  if ! DOEFF_LINTER_SNAPSHOT_DIR="$LINTER_STORE" uv run --script "$top/packages/doeff-linter/scripts/linter_snapshot.py" \
      "$top" "$commit" > /dev/null; then
    printf 'gate_tools: doeff-linter を基点の commit %s から置き場 %s に用意できない(linter_snapshot.py の理由は上)\n' \
      "$commit" "$LINTER_STORE" >&2
    return 1
  fi
}

case "${1:-}" in
  path) redis_path || exit 1 ;;
  linter) linter_prepare || exit 1 ;;
  *)
    printf 'usage: sh scripts/gate_tools.sh path|linter\n' >&2
    exit 2
    ;;
esac
