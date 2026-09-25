#!/bin/sh
# doeff-cluster の起動(k8s の Pod と手元の機体の両方で使う)。ROLE に応じて起動する。
# クラスタの仕組み(doeff_cluster)は実行環境(PATH の hy の venv)の物を使う。業務のコードは worker が CODE_REPO_URL の版ごとに展開する。
#   ROLE=coordinator … 割り当て係(LISTEN_PORT・状態は $WORK_DIR/coord/state.json・CLUSTER_NAMING = 外の系と取り交わす名の JSON)
#   ROLE=records     … effect の記録の置き場(LISTEN_PORT・RECORDS_ROOT・RECORDS_RETENTION_DAYS — record_store.hy)
#   ROLE=worker      … worker(COORDINATOR_URL = URL を `,` で並べると前から順に試す・WORKER_NAME・WORKER_LABELS・WORKER_CAPACITY・
#                      CODE_REPO_URL = 業務のコードの git の clone 元・CODE_IMPORT_ROOTS = 木の中の import の根(`,` で並べる・既定 .)・
#                      CODE_OVERLAY_PATH = overlay の口で重ねる dir(既定 空 = 重ねない))
#   ROLE=drain       … worker の Pod の preStop: coordinator に drain を頼み、この worker の上の job が他へ移るまで
#                      (上限 DRAIN_DEADLINE 秒・既定 90)待つ。結末は container の log(PID 1 の stderr)へ 1 行
#   ROLE=ready       … worker の Pod の readinessProbe: coordinator の見る worker がこの Pod の worker(世代が一致)で、生きていて
#                      drain 中でなければ 0
# 環境: WORK_DIR(既定 /work)・PATH に doeff の venv の python と hy(doeff-cluster を含む)。
# /etc/worker-git/id が在ればそれを deploy key として使う(手元の機体は自分の ssh の設定を使う)。
set -eu
WORK_DIR=${WORK_DIR:-/work}
case "${ROLE:-worker}" in
  ready)
    # hy を起こさず、この Pod の worker が heartbeat の返事ごとに書く file を読むだけ。Ready = file が在り、中身が ready
    # (coordinator の返事で drain 中でない)で、READY_MAX_AGE 秒(既定 30)以内に書かれた。file は container の /tmp なので
    # 同じ node の前の Pod の物とは混ざらない。
    f=${DOEFF_WORKER_READY_FILE:-/tmp/doeff-worker-ready}
    [ -f "$f" ] || exit 1
    [ "$(cat "$f")" = ready ] || exit 1
    # stat は GNU(Pod)と BSD(macOS の worker)で綴りが違う。
    mtime=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f")
    age=$(( $(date +%s) - mtime ))
    [ "$age" -le "${READY_MAX_AGE:-30}" ] || exit 1
    exit 0 ;;
  drain)
    # preStop の出力は kubelet が捨てるので、container の log(PID 1 = worker の stderr)へ出す。
    exec hy -m doeff_cluster.drain_main drain --coordinator "$COORDINATOR_URL" --name "$WORKER_NAME" \
      --deadline "${DRAIN_DEADLINE:-90}" 2>>/proc/1/fd/2 ;;
  coordinator)
    mkdir -p "$WORK_DIR/coord"
    naming=${CLUSTER_NAMING:-}
    [ -n "$naming" ] || naming='{}'
    exec hy -m doeff_cluster.coordinator --state-file "$WORK_DIR/coord/state.json" --port "${LISTEN_PORT:-8080}" \
      --naming "$naming" ;;
  records)
    exec hy -m doeff_cluster.record_store_main --root "${RECORDS_ROOT:-/records}" --port "${LISTEN_PORT:-8080}" \
      --retention-days "${RECORDS_RETENTION_DAYS:-30}" ;;
esac
# ROLE=worker
# WORKER_DIR_LOCK=1(k8s の DaemonSet)では、node の dir(mirror・状態)を使う worker を 1 つに限る。DaemonSet は消した Pod の終了を
# 待たずに同じ node へ次の Pod を作るので、旧 worker が job を止め終えて終わるまで、新しい Pod は git にも状態にも触らずに待つ。
# lock は fd 9 に持ち、exec した worker が終わるまで離さない(job の子 process へは渡らない — subprocess の close_fds)。
if [ "${WORKER_DIR_LOCK:-}" = 1 ]; then
  exec 9>"$WORK_DIR/worker.lock"
  echo "boot: $WORK_DIR/worker.lock を待つ $(date +%T)" >&2
  flock 9
  echo "boot: $WORK_DIR/worker.lock を取った $(date +%T)" >&2
fi
if [ -f /etc/worker-git/id ]; then
  export GIT_SSH_COMMAND="ssh -i /etc/worker-git/id -o IdentitiesOnly=yes -o UserKnownHostsFile=/etc/worker-git/known_hosts -o StrictHostKeyChecking=yes"
fi
repo=$WORK_DIR/mirror.git
if [ ! -d "$repo" ]; then
  git clone -q --bare "$CODE_REPO_URL" "$repo"
  git -C "$repo" config remote.origin.fetch '+refs/heads/*:refs/heads/*'
fi
# この Pod の worker の世代を Pod の中(container の /tmp — node の dir ではない)へ書かせる。readinessProbe が比べる。
export DOEFF_WORKER_BOOT_FILE="${DOEFF_WORKER_BOOT_FILE:-/tmp/doeff-worker-boot}"
export DOEFF_WORKER_READY_FILE="${DOEFF_WORKER_READY_FILE:-/tmp/doeff-worker-ready}"
exec hy -m doeff_cluster.main --coordinator "$COORDINATOR_URL" --name "$WORKER_NAME" \
  --labels "${WORKER_LABELS:-}" --capacity "${WORKER_CAPACITY:-10}" \
  --repo "$repo" --state-dir "$WORK_DIR/state" --stop-grace 10 \
  --import-roots "${CODE_IMPORT_ROOTS:-.}" --overlay-path "${CODE_OVERLAY_PATH:-}"
