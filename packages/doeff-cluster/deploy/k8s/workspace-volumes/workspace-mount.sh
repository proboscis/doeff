#!/bin/sh
# 作業場所の volume を作業の root の下の dir に付け外す、頼み 1 つの係(README.md の節「mount の係」)。
#
# 走らせる者: zeus の worker の Pod の sidecar workspace-mount(root・privileged — ../nodes/zeus/node.yaml)。sidecar は頼みの dir
# (worker と分け合う emptyDir)に `<鍵>.req` が置かれるたびに、この file を読み直して `sh workspace-mount.sh <頼みの file>` で
# 走らせ、終わったら頼みの file を消す。この file は名の固定した ConfigMap workspace-mount から置くので、中身を替えても Pod は
# 作り直されない(kubelet が file を入れ替えた後の頼みから効く)。
#
# 頼み(1 行・空白で 3 つ): `<命令> <volume の名> <付ける先>`
#   mount   付ける先に volume を付ける。volume に file system が無ければ ext4 を作ってから付ける(根の dir は worker の uid 1000・
#           root の予約 0)。
#           既に同じ volume が付いていれば何もしない。
#   umount  付ける先から外す。何も付いていなければ何もしない。使っている process が在って外れなければ断る(lazy に外さない)。
#   fstrim  付いている volume の使っていない block を Longhorn へ返す(冷やす前 — README.md の節「寿命」)。
#   grow    付いている volume の file system を、device の今の大きさまで付けたまま広げる(resize2fs — 頼み手が Longhorn の volume を
#           広げた後。device が広がっていなければ何もしない)。
# overlayfs の頼み(1 行・空白で 3 つ): `<命令> <下の dir> <付ける先>`(仕事ごとに repo の checkout を丸ごと
# 書き出さず、共有の読むだけの checkout の上に変えた file だけを書く):
#   overlay   付ける先に、下の dir を下の層・`<付ける先>.up` を上の層・`<付ける先>.wk` を overlayfs の作業の dir にした overlay を付ける。
#             下の dir は作業の root の下の base/ の下(共有の checkout の置き場)。上の層と作業の dir は頼み手が作る(同じ file system)。
#             既に同じ下の dir の overlay が付いていれば何もしない。
#   unoverlay 付ける先の overlay を外す(下の dir を照らしてから)。何も付いていなければ何もしない。上の層は消さない(頼み手が消す)。
# 答え(`<鍵>.out` — 書き切ってから名を替えて置く): 1 行目が結末の code(0 = した・既にそうだった / 1 = 断った・失敗した)、
# 2 行目からが訳と命令の出力。頼み手は答えを読んだら消す。
#
# 断る物(何もせずに 1): 6 つの命令の外・volume の名が英小文字・数字・- の外・付ける先が作業の root の下でない・. か .. か // を
# 含む・在る dir でない・symlink を通る・volume が node に attach されていない(device が無い)・付ける先に別の物が付いている・
# volume が別の所に付いている・file system が ext4 でない(作り直さない — 中身を消さない)。overlay と unoverlay では、ほかに: path が
# 英数字・. _ / - の外の字を持つ(overlayfs の option を区切る , と : を通さない)・下の dir が base/ の下でない・付ける先が base/ の下・
# 上の層か作業の dir が在る dir でないか symlink を通る・付ける先が空でない(付けると中身が隠れる)・付ける先に別の下の dir の overlay
# か overlay でない物が付いている。
#
# 値(sidecar の宣言と同じ — 検は tests/test_workspace_mount.py が偽の命令で回す時だけ替える):
#   WORKSPACE_MOUNT_ROOT     作業の root(sidecar の /work — worker の /work と同じ volume work)
#   WORKSPACE_MOUNT_DEVICES  attach した Longhorn の volume の device の dir(host の /dev を sidecar の /host/dev に置いた下)
set -u

request=$1
root=${WORKSPACE_MOUNT_ROOT:-/work}
devices=${WORKSPACE_MOUNT_DEVICES:-/host/dev/longhorn}
# blkid・mkfs.ext4・fstrim・resize2fs・blockdev は /usr/sbin に在る。後ろに足す(前の道の命令を替えない)。
PATH="$PATH:/usr/sbin:/sbin"

answer=${request%.req}.out
notes=$(mktemp)

finish() {
  { echo "$1"; cat "$notes"; } >"$answer.tmp" && mv -f "$answer.tmp" "$answer"
  rm -f "$notes"
  exit "$1"
}

say() { echo "workspace-mount: $*" >>"$notes"; }

refuse() {
  say "$*"
  finish 1
}

# 付ける先に今付いている物の major:minor(付いていなければ空)。重ねて付いていれば一番上。
mounted_at() { findmnt -rn -M "$1" -o MAJ:MIN | tail -n 1; }

read -r verb volume target rest <"$request" || [ -n "${verb:-}" ] || refuse "頼み $request が読めない(空)"
[ -z "${rest:-}" ] || refuse "頼みの欄が 3 つでない: '$verb $volume $target $rest'"
case ${verb:-} in mount | umount | fstrim | grow | overlay | unoverlay) ;;
  *) refuse "知らない命令 '${verb:-}'(mount・umount・fstrim・grow・overlay・unoverlay だけ)" ;;
esac

# 作業の root の下の、在る・symlink を通らない dir である事(何の dir かを $1 で言う)。
plain_dir() {
  case ${2:-} in "$root"/?*) ;; *) refuse "$1 '${2:-}' が $root の下でない" ;; esac
  case $2/ in */../* | */./* | *//*) refuse "$1 '$2' が . か .. か // を含むか / で終わる" ;; esac
  [ -d "$2" ] || refuse "$1 $2 が在る dir でない(dir は頼み手が作る)"
  [ "$(realpath -e "$2" 2>/dev/null)" = "$2" ] || refuse "$1 $2 が symlink を通る"
}

case $verb in overlay | unoverlay)
  # overlayfs の頼み: 2 つ目の欄は volume の名でなく下の dir(共有の checkout)。
  lower=$volume
  for path in "$lower" "$target"; do
    case $path in *[!A-Za-z0-9._/-]*) refuse "path '$path' が英数字・. _ / - の外の字を持つ(overlayfs の option を区切る , と : を通さない)" ;; esac
  done
  case $lower in "$root"/base/?*) ;; *) refuse "下の dir '$lower' が $root/base の下でない(共有の checkout の置き場の外)" ;; esac
  case $target in "$root"/base/*) refuse "付ける先 '$target' が $root/base の下(共有の checkout の置き場に付けない)" ;; esac
  plain_dir "下の dir" "$lower"
  plain_dir "付ける先" "$target"
  # 付ける先に今付いている overlay の下の dir(付いていなければ空・overlay でない物なら '-')。
  have_type=$(findmnt -rn -M "$target" -o FSTYPE | tail -n 1)
  have_options=$(findmnt -rn -M "$target" -o OPTIONS | tail -n 1)
  case $have_type in
    '') have_lower= ;;
    overlay) have_lower=$(echo ",$have_options," | sed -n 's/.*,lowerdir=\([^,]*\),.*/\1/p') ;;
    *) have_lower=- ;;
  esac
  case $verb in
    overlay)
      if [ -n "$have_lower" ]; then
        [ "$have_lower" = "$lower" ] || refuse "$target には別の物(${have_type} ${have_lower})が付いている"
        say "$target には既に $lower の overlay が付いている"
        finish 0
      fi
      upper=$target.up
      work=$target.wk
      plain_dir "上の層" "$upper"
      plain_dir "overlayfs の作業の dir" "$work"
      [ -z "$(ls -A "$target")" ] || refuse "付ける先 $target が空でない(付けると中身が隠れる)"
      mount -t overlay overlay -o "lowerdir=$lower,upperdir=$upper,workdir=$work" "$target" >>"$notes" 2>&1 ||
        refuse "$lower の overlay を $target に付けられなかった"
      say "$lower を下の層・$upper を上の層にした overlay を $target に付けた"
      finish 0
      ;;
    unoverlay)
      if [ -z "$have_lower" ]; then
        say "$target には何も付いていない"
        finish 0
      fi
      [ "$have_lower" = "$lower" ] || refuse "$target に付いているのは $lower の overlay でない(${have_type} ${have_lower})"
      umount "$target" >>"$notes" 2>&1 || refuse "$target を外せなかった(使っている process が在るかもしれない)"
      say "$lower の overlay を $target から外した"
      finish 0
      ;;
  esac
  ;;
esac

case ${volume:-} in '' | -* | *[!a-z0-9-]*) refuse "volume の名 '${volume:-}' が英小文字・数字・- の外" ;; esac
plain_dir "付ける先" "$target"

device=$devices/$volume
# device の型と major:minor(Longhorn は attach した volume を host の /dev/longhorn/<名> の block device にする)。
info=$(stat -L -c '%F %Hr:%Lr' "$device" 2>/dev/null) || info=
case $info in
  "block special file "*) number=${info#block special file } ;;
  *) number= ;;
esac
have=$(mounted_at "$target")

case $verb in
  mount)
    [ -n "$number" ] || refuse "$device が無い — volume $volume がこの node に attach されていない"
    if [ -n "$have" ]; then
      [ "$have" = "$number" ] || refuse "$target には別の物($have)が付いている"
      say "$target には既に $volume が付いている"
      finish 0
    fi
    elsewhere=$(findmnt -rn -o MAJ:MIN,TARGET | while read -r found path; do
      [ "$found" = "$number" ] && { echo "$path"; break; }
    done)
    [ -z "$elsewhere" ] || refuse "$volume は既に $elsewhere に付いている(1 つの volume は 1 つの dir にだけ付ける)"
    # blkid -p: 0 = 印が在る・2 = 何も無い(空の volume)・ほか = 読めない。印が在って TYPE が空なら file system でない印(区画の表など)。
    fstype=$(blkid -p -o value -s TYPE "$device" 2>>"$notes")
    probed=$?
    case $probed in
      2)
        # -m 0: 根の dir を使うのは worker の uid だけなので、root の予約(既定 5% — 10 GiB で 512 MiB)を置かない。
        # assume_storage_prezeroed=1(e2fsprogs 1.47 から): journal と inode の表を 0 で埋めない。ここへ来るのは blkid が何も
        # 見ない新しい volume だけで、Longhorn の新しい volume は書いていない所を 0 で読む(replica は sparse file)。埋めると
        # 10 GiB の volume ごとに journal 64 MiB と表 160 MiB を SSD へ書く(作っただけで使わない volume でも)。
        mkfs.ext4 -q -m 0 -E root_owner=1000:1000,assume_storage_prezeroed=1 "$device" </dev/null >>"$notes" 2>&1 ||
          refuse "$device に ext4 を作れなかった"
        say "空の $volume に ext4 を作った"
        ;;
      0)
        [ "$fstype" = ext4 ] || refuse "$device の中身が ext4 でない('$fstype')— 作り直さない"
        ;;
      *) refuse "$device を blkid が読めない(code $probed)" ;;
    esac
    mount -t ext4 "$device" "$target" >>"$notes" 2>&1 || refuse "$volume を $target に付けられなかった"
    say "$volume を $target に付けた"
    finish 0
    ;;
  umount)
    if [ -z "$have" ]; then
      say "$target には何も付いていない"
      finish 0
    fi
    [ -n "$number" ] || refuse "$target に付いている物($have)を $volume と照らせない — $device が無い"
    [ "$have" = "$number" ] || refuse "$target に付いているのは $volume でない($have)"
    umount "$target" >>"$notes" 2>&1 || refuse "$target を外せなかった(使っている process が在るかもしれない)"
    say "$volume を $target から外した"
    finish 0
    ;;
  fstrim)
    [ -n "$have" ] || refuse "$target に何も付いていない"
    [ -n "$number" ] && [ "$have" = "$number" ] || refuse "$target に付いているのは $volume でない($have)"
    fstrim -v "$target" >>"$notes" 2>&1 || refuse "$target の fstrim が失敗した"
    finish 0
    ;;
  grow)
    [ -n "$have" ] || refuse "$target に何も付いていない"
    [ -n "$number" ] && [ "$have" = "$number" ] || refuse "$target に付いているのは $volume でない($have)"
    resize2fs "$device" >>"$notes" 2>&1 || refuse "$target の file system を広げられなかった"
    say "$volume の file system を device の大きさ $(blockdev --getsize64 "$device" 2>/dev/null) byte まで広げた"
    finish 0
    ;;
esac
