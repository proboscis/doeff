#!/bin/sh
# coordinator の記録(WAL と snapshot)の控え(直近 5 つを残す)。この dir の kustomization.yaml が ConfigMap coord-wal-backup
# (キー backup.sh)に組み、coordinator.yaml の init container wal-backup が、coordinator の起動の前に 1 回だけ実行する。
# Deployment は Recreate なので、古い Pod が消えてから写す = WAL は書かれていない。控えの置き場は WAL と同じ volume
# (coordinator.yaml の coord-wal)の中。
#
# 起動を止めない: 控えが無くても coordinator は動く(戻しの元が 1 つ古くなるだけ)が、init container が落ちると cluster 全体が止まる。
# だから控えを取れない時は、訳を名指しの 1 行(先頭 "coord-wal-backup: 控えを飛ばす")で出して 0 で終わる。呼び手(Deployment)も
# この script の失敗を 0 にする。
#
# 消すのは、この script が作った控えの dir だけ: BACKUP_DIR の直下で、名が UTC の YYYYMMDDTHHMMSSZ の形に合い、本物の dir(symlink で
# ない)の物。新しい順に BACKUP_KEEP を残す。glob は末尾の / を付けず、消す直前にもう 1 度 dir であり symlink でない事を確かめ、
# rm にも末尾の / を付けない(symlink の指す先の中身を消さないため)。
#
# 値(Deployment の env): WAL_DIR = WAL の dir・BACKUP_DIR = 控えの置き場・BACKUP_KEEP = 残す数・BACKUP_MIN_FREE_BYTES = 写した後に
# 残す空きの下限(coordinator の Node の kubelet の soft の追い出しの線より上に置く)。
# 戻し方 = coordinator の Deployment からこの init container と ConfigMap の mount を外す(控えの dir は残る)。
# 検 = packages/doeff-cluster/tests/test_coord_wal_backup.py(この script を sh で実際に走らせる)。
set -u

wal_dir="${WAL_DIR:?WAL_DIR が無い}"
backups="${BACKUP_DIR:?BACKUP_DIR が無い}"
keep="${BACKUP_KEEP:?BACKUP_KEEP が無い}"
min_free="${BACKUP_MIN_FREE_BYTES:?BACKUP_MIN_FREE_BYTES が無い}"
partial="$backups/.partial"

skip() {
  echo "coord-wal-backup: 控えを飛ばす — $1(coordinator の起動は通す)"
  exit 0
}

# 写す物 = WAL の dir の中で在る物だけ(初めての起動では無い)。
files=""
need=0
for name in wal.jsonl snapshot.json; do
  if [ -f "$wal_dir/$name" ]; then
    files="$files $name"
    need=$((need + $(stat -c %s "$wal_dir/$name")))
  fi
done
[ -n "$files" ] || skip "$wal_dir に wal.jsonl も snapshot.json も無い"

mkdir -p "$backups" || skip "控えの置き場 $backups を作れない"
free=$(df -B1 --output=avail "$backups" | tail -n 1 | tr -d ' ')
[ "$((free - need))" -ge "$min_free" ] \
  || skip "空き $free byte から控え $need byte を引くと下限 $min_free byte を切る"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
[ ! -e "$backups/$stamp" ] || skip "同じ名の控え $stamp が既に在る"

# 写す途中の dir。前の回が途中で止まった残りは消してから使う(symlink なら触らない)。
if [ -L "$partial" ]; then
  skip "$partial が symlink"
fi
if [ -d "$partial" ]; then
  rm -rf -- "$partial" || skip "前の回の途中の写し $partial を消せない"
fi
mkdir "$partial" || skip "$partial を作れない"
for name in $files; do
  cp -p -- "$wal_dir/$name" "$partial/$name" || skip "$name を写せない"
done
(cd "$partial" && sha256sum $files > SHA256SUMS) || skip "sha256 を書けない"
mv -- "$partial" "$backups/$stamp" || skip "$partial を $stamp へ付け替えられない"
echo "coord-wal-backup: 控えた $backups/$stamp ($need byte:$files)"

# この script が作った控えの dir か(消してよい物の判定の 1 か所): symlink でなく・本物の dir で・名が UTC の YYYYMMDDTHHMMSSZ の形。
# 選ぶ時と消す直前の両方がこの判定を呼ぶ(選んだ後に symlink へ差し替わっても消さない)。
owned_backup() {
  [ ! -L "$1" ] || return 1
  [ -d "$1" ] || return 1
  case "${1##*/}" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) return 0 ;;
  esac
  return 1
}

# 古い控えを消す(新しい順に keep を残す)。消せなくても起動は通す。
names=""
for path in "$backups"/*; do
  owned_backup "$path" && names="$names ${path##*/}"
done
for name in $(printf '%s\n' $names | sort -r | tail -n +"$((keep + 1))"); do
  path="$backups/$name"
  owned_backup "$path" || continue
  if rm -rf -- "$path"; then
    echo "coord-wal-backup: 古い控え $name を消した"
  else
    echo "coord-wal-backup: 古い控え $name を消せない(起動は通す)"
  fi
done
exit 0
