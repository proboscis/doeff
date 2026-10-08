#!/bin/sh
# 日次の全体検証(.agents/land-queue.toml の gate.full)が走る機体の道具の依存の宣言の 1 点と、その準備(agora-redesign #3870)。
#
# 日次の全体検証は verify-worker の Pod の中でなく、remote_check で木を zeus の上へ送って走る(遠隔が使えない時だけ Pod の中へ倒れる)。
# テストが PATH から探す道具は、走る機体の側に宣言して用意する — Pod の image に入れるだけでは zeus の回に効かない(2026-10-08 8:00 の回で
# packages/doeff-events/tests/test_notice_laws_redis.py が 14 本とも skip だった)。
#
#   sh scripts/gate_tools.sh path
#     宣言の道具を全部そろえ、PATH の前に載せる dir を「dir:」の形で標準出力へ書く(足す物が無ければ空)。道具ごとの順:
#       1. PATH に宣言の版が在れば何も足さない(verify-worker の Pod の image)。
#       2. cache の dir($HOME/.cache/<名>-<版>)に宣言の版が在れば、その dir。
#       3. docker が在れば、宣言の image(digest つき)から実行 file 1 つを取り出して cache の dir に置き、その dir。
#     そろえられなければ、道具の名・版・image・探した PATH を標準エラーへ書いて 1(処理ステージは赤 — 黙って skip にしない)。
#
# 使い手 = .agents/land-queue.toml の処理ステージ packages(make test-packages の前)。失敗ケース = tests/test_gate_tools.py。

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

case "${1:-}" in
  path) redis_path || exit 1 ;;
  *)
    printf 'usage: sh scripts/gate_tools.sh path\n' >&2
    exit 2
    ;;
esac
