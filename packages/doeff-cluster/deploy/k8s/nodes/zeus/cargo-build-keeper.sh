#!/bin/sh
# cargo の build の中間物の置き場(memory の emptyDir /cargo-build)の掃除 1 回(cargo-build-in-memory.yaml の頭の註)。
#
# 走らせる者: zeus の worker の Pod の sidecar cargo-build-keeper。10 秒ごとに、この file を読み直して走らせる(名の固定した ConfigMap から
# 置くので、値を替えても Pod は作り直されない)。
#
# 置き場の形: cargo は CARGO_BUILD_BUILD_DIR の {workspace-path-hash} を `<16 進 2 字>/<16 進>` に展開する。この形の dir だけを「1 つの作業木の
# 中間物」と読み、ほかの物には触らない。消してよい訳: どれも cargo が source から作り直せる物で、作業木の file は /cargo-build の外に在る。
#
# 決まり(上から順に):
#   使っている物は消さない: cargo が build の間 flock で持つ .cargo-build-lock が持たれている・中の dir が GRACE 秒の内に変わった。cargo は
#     test を走らせる間は lock を放すので、GRACE は長めに取る(流れている cargo test の次の binary を消さないため)。
#   使わなくなった物: 最後の変更から IDLE 秒を過ぎたら消す(memory を返す。作業木は残るので、次の build が組み直す)。
#   溢れそう: tmpfs の使いが HIGH% 以上か、機体の MemAvailable が線 MiB 未満なら、古い順に消す。使いが LOW% 未満かつ線以上で止める。
#   満杯の手前: 使いが FULL% 以上で上の決まりでは足りなければ、猶予を FULL_GRACE 秒に縮め、lock の持たれていない物を古い順に、使いが HIGH% 未満に
#     なるまで消す。訳: 2026-11-05 16:00 JST の zeus で使いが 93〜97% に留まり、中間物のどれもが 15 分の内に変わっていて何も消せなかった(大きい物から 3.5・3.4・2.5 GiB)。
#     流れている cargo test の次の binary を消しうる(やり直しで直る)が、全部の build を ENOSPC で落とすより小さい。
#   消せる物が無い: 何も消さず、使いが FULL% 以上なら 1 行出す。その先の書きは ENOSPC で build が失敗する。SSD へは自動で戻さない(途中で置き場を
#     替えると全部を組み直す書きになり、減らす物を増やす)。`env -u CARGO_BUILD_BUILD_DIR cargo …` でその 1 回を作業木の target/ に組める。
#     MemAvailable が線を割ったままなら SHORT_SAID 秒に 1 行出す。使っている物は線を割っていても消さないので、その間は新しいターンが返される
#     (機体の上で動いている build と会話のターンのどちらも、途中で壊さない)。
#
# 機体の MemAvailable の線: 置き場の直下の file(既定 .keep-available-mib — 数 1 つ・MiB)を毎回読む。書くのは新しいターンを受ける係 turn-host
# (この worker の上で動く系の側 — この script と繋がるのはこの file の形だけ)で、値はその係が新しいターンを断る床の上に、ターン
# 数本ぶんを足した物。この script は線の数を持たない: 床は係の判断の一部で、同じ量を 2 か所に宣言すると別々に動く(2026-11-05 に床 25.1 GiB と
# この script の 12 GiB が離れ、MemAvailable が 12〜25 GiB の間は、ターンが返されるのに中間物は memory を返さなかった)。file が無い・数でない間
# (係がまだ起きていない Pod の起動の直後)は、機体の memory を理由には消さない。名が `<2 字>/<16 進>` の形でないので中間物とは読まない。
#
# 値(sidecar の宣言と同じ — 検は tests/test_cargo_build_keeper.py が偽の df と meminfo で回す時だけ替える)。
set -u

root=${CARGO_BUILD_KEEPER_ROOT:-/cargo-build}
high=${CARGO_BUILD_KEEPER_HIGH_PERCENT:-75}
low=${CARGO_BUILD_KEEPER_LOW_PERCENT:-50}
full=${CARGO_BUILD_KEEPER_FULL_PERCENT:-90}
line_file=${CARGO_BUILD_KEEPER_LINE_FILE:-$root/.keep-available-mib}
idle=${CARGO_BUILD_KEEPER_IDLE_SECONDS:-14400}
grace=${CARGO_BUILD_KEEPER_GRACE_SECONDS:-900}
full_grace=${CARGO_BUILD_KEEPER_FULL_GRACE_SECONDS:-300}
short_said=${CARGO_BUILD_KEEPER_SHORT_SAID_SECONDS:-300}
meminfo=${CARGO_BUILD_KEEPER_MEMINFO:-/proc/meminfo}

log() {
  echo "cargo-build-keeper: $*" >&2
}

used_percent() {
  df -P "$root" | awk 'NR == 2 { sub(/%/, "", $5); print $5 }'
}

available_mib() {
  awk '/^MemAvailable:/ { print int($2 / 1024) }' "$meminfo"
}

# 機体の MemAvailable の線(MiB)。読めない・数でなければ 0(機体の memory を理由には消さない)。
line_mib() {
  line=$(cat "$line_file" 2>/dev/null)
  case "$line" in
    '' | *[!0-9]*) echo 0 ;;
    *) echo "$line" ;;
  esac
}

# 作業木 1 つの中間物の dir を 1 行ずつ。symlink と形の違う物は出さない。
entries() {
  for entry in "$root"/*/*; do
    [ -d "$entry" ] && [ ! -L "$entry" ] || continue
    prefix=${entry%/*}
    case "${prefix##*/}" in
      [0-9a-f][0-9a-f]) ;;
      *) continue ;;
    esac
    case "${entry##*/}" in
      *[!0-9a-f]*) continue ;;
    esac
    printf '%s\n' "$entry"
  done
}

# $1 の中の dir の最後の変更の時刻(epoch 秒)。<dir>/<profile>/deps と <dir>/<triple>/<profile> まで見る。
newest() {
  find "$1" -maxdepth 3 -type d -printf '%T@\n' | sort -n | tail -n 1 | cut -d. -f1
}

# $1 を cargo が持っていれば真。
locked() {
  for lock in $(find "$1" -maxdepth 3 -type f -name .cargo-build-lock); do
    flock -n "$lock" true || return 0
  done
  return 1
}

# 消してよい物を「最後の変更の時刻 dir」で古い順に。
candidates() {
  now=$(date +%s)
  entries | while IFS= read -r entry; do
    changed=$(newest "$entry")
    [ $((now - changed)) -ge "$grace" ] || continue
    locked "$entry" && continue
    printf '%s %s\n' "$changed" "$entry"
  done | sort -n
}

# 名を替えてから消す(消す途中の dir を、次に来た cargo が半端な中間物として読まない)。
evict() {
  doomed="$root/.evicting-$$-${1##*/}"
  mv "$1" "$doomed" || return 1
  rm -rf -- "$doomed"
  rmdir "${1%/*}" 2>/dev/null
  log "$1 を消した($2)"
}

pressed() {
  [ "$(used_percent)" -ge "$1" ] || [ "$(available_mib)" -lt "$(line_mib)" ]
}

# 前の回が消す途中で止められた物を片づける。
for doomed in "$root"/.evicting-*; do
  [ -e "$doomed" ] && rm -rf -- "$doomed"
done

now=$(date +%s)
candidates | while read -r changed entry; do
  [ $((now - changed)) -ge "$idle" ] && evict "$entry" "$idle 秒使われていない"
done

if pressed "$high"; then
  # パイプの右は subshell なので、止める判定は毎回 df と meminfo を読み直す。
  candidates | while read -r _ entry; do
    pressed "$low" || break
    evict "$entry" "使い $(used_percent)%・MemAvailable $(available_mib) MiB・線 $(line_mib) MiB"
  done
fi

if [ "$(used_percent)" -ge "$full" ]; then
  grace=$full_grace
  candidates | while read -r _ entry; do
    [ "$(used_percent)" -ge "$high" ] || break
    evict "$entry" "満杯の手前・使い $(used_percent)%"
  done
fi

percent=$(used_percent)
if [ "$percent" -ge "$full" ]; then
  log "使い ${percent}% で消せる物が無い(どれも使っている)— この先の書きは ENOSPC で失敗する"
fi

# 線を割ったまま消せる物が無い(同じ行を 10 秒ごとに出さない — 印の file の刻で間を空ける)。
available=$(available_mib)
line=$(line_mib)
said="$root/.short-said"
if [ "$available" -lt "$line" ]; then
  if [ ! -e "$said" ] || [ $(($(date +%s) - $(date -r "$said" +%s))) -ge "$short_said" ]; then
    touch "$said"
    log "MemAvailable ${available} MiB が線 ${line} MiB を割っていて消せる物が無い(使い ${percent}%・どれも使っている)— 新しいターンは返される"
  fi
fi
exit 0
