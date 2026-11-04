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
#   溢れそう: tmpfs の使いが HIGH% 以上か、機体の MemAvailable が FLOOR MiB 未満なら、古い順に消す。使いが LOW% 未満かつ FLOOR 以上で止める。
#   消せる物が無い: 何も消さず、使いが FULL% 以上なら 1 行出す。その先の書きは ENOSPC で build が失敗する。SSD へは自動で戻さない(途中で置き場を
#     替えると全部を組み直す書きになり、減らす物を増やす)。`env -u CARGO_BUILD_BUILD_DIR cargo …` でその 1 回を作業木の target/ に組める。
#
# 値(sidecar の宣言と同じ — 検は tests/test_cargo_build_keeper.py が偽の df と meminfo で回す時だけ替える)。
set -u

root=${CARGO_BUILD_KEEPER_ROOT:-/cargo-build}
high=${CARGO_BUILD_KEEPER_HIGH_PERCENT:-75}
low=${CARGO_BUILD_KEEPER_LOW_PERCENT:-50}
full=${CARGO_BUILD_KEEPER_FULL_PERCENT:-90}
floor_mib=${CARGO_BUILD_KEEPER_FLOOR_MIB:-12288}
idle=${CARGO_BUILD_KEEPER_IDLE_SECONDS:-14400}
grace=${CARGO_BUILD_KEEPER_GRACE_SECONDS:-900}
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
  [ "$(used_percent)" -ge "$1" ] || [ "$(available_mib)" -lt "$floor_mib" ]
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
    evict "$entry" "使い $(used_percent)%・MemAvailable $(available_mib) MiB"
  done
fi

percent=$(used_percent)
if [ "$percent" -ge "$full" ]; then
  log "使い ${percent}% で消せる物が無い(どれも使っている)— この先の書きは ENOSPC で失敗する"
fi
exit 0
