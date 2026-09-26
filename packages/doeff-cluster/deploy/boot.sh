#!/bin/sh
# doeff-cluster の起動(k8s の Pod と手元の機体の両方で使う)。ROLE に応じて起動する。
# クラスタの仕組み(doeff_cluster)は PATH の hy の venv の物を使う。WORKER_DOEFF_COMMIT が在れば、その venv を宣言した doeff の commit
# から自分で用意する(自己起動・下)。業務のコードは worker が job ごとに用意する(CODE_REPO_URL の版の木か、実行環境の root)。
#   ROLE=coordinator … 割り当て係(LISTEN_PORT・状態は $WORK_DIR/coord/state.json・CLUSTER_NAMING = 外の系と取り交わす名の JSON)
#   ROLE=records     … effect の記録の置き場(LISTEN_PORT・RECORDS_ROOT・RECORDS_RETENTION_DAYS — record_store.hy)
#   ROLE=worker      … worker(COORDINATOR_URL = URL を `,` で並べると前から順に試す・WORKER_NAME・WORKER_LABELS・WORKER_CAPACITY・
#                      CODE_REPO_URL = 業務のコードの git の clone 元(空 = 版の木の job を受けない)・CODE_IMPORT_ROOTS = 木の中の
#                      import の根(`,` で並べる・既定 .)・CODE_OVERLAY_PATH = overlay の口で重ねる dir(既定 空 = 重ねない)・
#                      WORKER_TOOLS = 名乗る道具に足す物(名=版,…)・WORKER_PASS_ENV = job の子へ渡す worker の環境変数の名
#                      (`,` で並べる — 実行環境の job の子は worker の環境を許可表でしか継がないので、機体の設定の path や URL を名で渡す))
#   ROLE=drain       … worker の Pod の preStop: coordinator に drain を頼み、この worker の上の job が他へ移るまで
#                      (上限 DRAIN_DEADLINE 秒・既定 90)待つ。結末は container の log(PID 1 の stderr)へ 1 行
#   ROLE=access      … 読み取りの許可表(WORKER_REPOS)の git / ssh の設定と許可表の JSON だけを書き、JSON の path を出す
#   ROLE=ready       … worker の Pod の readinessProbe: coordinator の見る worker がこの Pod の worker(世代が一致)で、生きていて
#                      drain 中でなければ 0
# 環境: WORK_DIR(既定 /work)。
#
# 自己起動(WORKER_DOEFF_COMMIT が在る時 — 土台だけの image・deploy/base/Dockerfile。設計 D13・E13):
#   WORKER_DOEFF_URL(既定 https://github.com/proboscis/doeff.git)の WORKER_DOEFF_COMMIT を $WORK_DIR/boot/roots/<sha> に展開し、
#   `uv sync --locked --package doeff-cluster` した venv の hy で起こす。同じ commit の root は完成の印で使い回す(2 回目の起動は秒)。
#   uv の cache と Python は実行環境の root と同じ $WORK_DIR/state の下(uv-cache・python)。crate の取得先は $WORK_DIR/state/cargo。
#   worker の code を変える時は WORKER_DOEFF_COMMIT を変えて Pod を入れ替える(image は作り直さない)。無ければ今までどおり PATH の hy。
#
# 読み取りの許可表(WORKER_REPOS が在る時 — 設計 U5・U6):
#   WORKER_REPOS = 空白で並べた「<url>=<鍵の名>」(鍵の名が空 = 鍵なしで読む公開の repo)。鍵の file = $WORKER_REPO_KEYS_DIR/<鍵の名>
#   (既定 /etc/worker-repos・known_hosts も同じ dir)。worker の許可表の JSON(--repo-keys)と、git / ssh の設定(url ごとに別の
#   Host の別名へ書き換え、その Host に鍵を結ぶ)を $WORKER_ACCESS_DIR(既定 $HOME/.doeff-worker-repos)に書き、GIT_CONFIG_GLOBAL と
#   GIT_SSH_COMMAND(ssh -F)でそこへ向ける(機体の ~/.gitconfig と ~/.ssh/config は読まない・書かない)。GitHub の deploy key は repo ごとなので、鍵を 1 本しか渡さない
#   GIT_SSH_COMMAND では uv の git の依存(別の非公開 repo)を読めない。表に無い url は worker が repo-denied で断る。
#   WORKER_REPOS が無く /etc/worker-git/id が在れば、今までどおりそれを唯一の deploy key として使う。
set -eu
WORK_DIR=${WORK_DIR:-/work}
role=${ROLE:-worker}

# 自己起動の root を用意して PATH の頭に置く。drain は準備せず、完成した root を使うだけ(preStop で build しない)。
doeff_root() {
  commit=$WORKER_DOEFF_COMMIT
  url=${WORKER_DOEFF_URL:-https://github.com/proboscis/doeff.git}
  boot=$WORK_DIR/boot
  export UV_CACHE_DIR="$WORK_DIR/state/uv-cache" UV_PYTHON_INSTALL_DIR="$WORK_DIR/state/python" CARGO_HOME="$WORK_DIR/state/cargo"
  export UV_NO_PROGRESS=1
  mkdir -p "$boot/roots" "$WORK_DIR/state"
  # 同じ node の dir を使う次の Pod と重ならないよう、root の準備は排他(fd 8・準備が済めば離す)。
  exec 8>"$boot/boot.lock"
  flock 8
  if [ ! -d "$boot/doeff.git" ]; then
    git clone -q --bare "$url" "$boot/doeff.git.tmp"
    mv "$boot/doeff.git.tmp" "$boot/doeff.git"
  fi
  if ! git -C "$boot/doeff.git" cat-file -e "$commit^{commit}" 2>/dev/null; then
    git -C "$boot/doeff.git" fetch -q origin "+refs/heads/*:refs/heads/*" || true
    git -C "$boot/doeff.git" fetch -q origin "$commit" 2>/dev/null || true
  fi
  sha=$(git -C "$boot/doeff.git" rev-parse --verify "$commit^{commit}")
  root=$boot/roots/$sha
  if [ -f "$root/.doeff-boot-ready" ]; then
    echo "boot: doeff $sha の root を使う(準備済み)" >&2
  elif [ "$role" = drain ]; then
    echo "boot: doeff $sha の root が無い(drain は準備しない)" >&2
    exit 1
  else
    started=$(date +%s)
    # 印の無い root はこの script が途中で止まった残り — 作り直す。
    rm -rf "$root"
    mkdir -p "$root"
    git -C "$boot/doeff.git" archive --format=tar "$sha" | tar -x -C "$root"
    extracted=$(date +%s)
    (cd "$root" && uv sync --locked --package doeff-cluster --no-dev >&2)
    touch "$root/.doeff-boot-ready"
    echo "boot: doeff $sha の root を準備した(展開 $((extracted - started)) 秒・uv sync $(( $(date +%s) - extracted )) 秒)" >&2
  fi
  exec 8>&-
  export PATH="$root/.venv/bin:$PATH"
}

# 読み取りの許可表から、worker の許可表の JSON と、url ごとに鍵を選ぶ git / ssh の設定を書き、git と ssh をそこへ向ける
# (export するので subshell で呼ばない)。JSON の path は repo_keys に置く。
repo_access() {
  keys=${WORKER_REPO_KEYS_DIR:-/etc/worker-repos}
  dir=${WORKER_ACCESS_DIR:-$HOME/.doeff-worker-repos}
  mkdir -p "$dir"
  chmod 700 "$dir"
  : >"$dir/ssh_config"
  : >"$dir/gitconfig"
  json="{"
  n=0
  for entry in $WORKER_REPOS; do
    url=${entry%=*}
    name=${entry##*=}
    key=""
    if [ -n "$name" ]; then
      key=$keys/$name
      [ -f "$key" ] || { echo "boot: 許可表の鍵 $key が無い" >&2; exit 1; }
      n=$((n + 1))
      alias=doeff-repo-$n
      # url の形: ssh://git@<host>/<path> か git@<host>:<path>
      case "$url" in
        ssh://*) rest=${url#ssh://}; userhost=${rest%%/*}; path=${rest#*/} ;;
        *@*:*) userhost=${url%%:*}; path=${url#*:} ;;
        *) echo "boot: 鍵つきの url は ssh の形にする: $url" >&2; exit 1 ;;
      esac
      user=${userhost%@*}
      host=${userhost#*@}
      printf 'Host %s\n  HostName %s\n  User %s\n  IdentityFile %s\n  IdentitiesOnly yes\n  UserKnownHostsFile %s/known_hosts\n  StrictHostKeyChecking yes\n' \
        "$alias" "$host" "$user" "$key" "$keys" >>"$dir/ssh_config"
      printf '[url "ssh://%s@%s/%s"]\n  insteadOf = %s\n' "$user" "$alias" "$path" "$url" >>"$dir/gitconfig"
    fi
    [ "$json" = "{" ] || json="$json,"
    json="$json\"$url\":\"$key\""
  done
  chmod 600 "$dir/ssh_config"
  printf '%s}\n' "$json" >"$dir/repo-keys.json"
  export GIT_CONFIG_GLOBAL="$dir/gitconfig" GIT_SSH_COMMAND="ssh -F $dir/ssh_config"
  repo_keys=$dir/repo-keys.json
}

# ready は hy を起こさないので root を要らない。
if [ -n "${WORKER_DOEFF_COMMIT:-}" ] && [ "$role" != ready ] && [ "$role" != access ]; then
  doeff_root
  # 起動の script も宣言した commit の物で続ける: image に焼いた script は最初の自己起動(root を用意するまで)だけを受け持つ。
  # だから起動の script を直しても、WORKER_DOEFF_COMMIT を変えて入れ替えれば新しい script で起き、image を作り直さない。
  # 引き継いだ先(DOEFF_BOOT_FROM_ROOT)ではもう引き継がない。
  next=$root/packages/doeff-cluster/deploy/boot.sh
  if [ -z "${DOEFF_BOOT_FROM_ROOT:-}" ] && [ -f "$next" ] && ! cmp -s "$next" "$0"; then
    echo "boot: 起動の script を doeff $sha の物へ引き継ぐ" >&2
    export DOEFF_BOOT_FROM_ROOT=1
    exec sh "$next"
  fi
fi

case "$role" in
  access)
    # 許可表の設定(git / ssh の設定と worker の許可表の JSON)だけを書き、JSON の path を出す — 手元の機体と検で確かめるため。
    repo_access
    echo "$repo_keys"
    exit 0 ;;
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
    # 世代の file(worker が起動の時に書く — 下の DOEFF_WORKER_BOOT_FILE と同じ path)を渡し、頼みに世代を載せる。
    exec hy -m doeff_cluster.drain_main drain --coordinator "$COORDINATOR_URL" --name "$WORKER_NAME" \
      --deadline "${DRAIN_DEADLINE:-90}" --boot-file "${DOEFF_WORKER_BOOT_FILE:-/tmp/doeff-worker-boot}" 2>>/proc/1/fd/2 ;;
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
repo_keys=""
if [ -n "${WORKER_REPOS:-}" ]; then
  repo_access
elif [ -f /etc/worker-git/id ]; then
  export GIT_SSH_COMMAND="ssh -i /etc/worker-git/id -o IdentitiesOnly=yes -o UserKnownHostsFile=/etc/worker-git/known_hosts -o StrictHostKeyChecking=yes"
fi
repo=$WORK_DIR/mirror.git
if [ ! -d "$repo" ]; then
  if [ -n "${CODE_REPO_URL:-}" ]; then
    git clone -q --bare "$CODE_REPO_URL" "$repo"
    git -C "$repo" config remote.origin.fetch '+refs/heads/*:refs/heads/*'
  else
    # 版の木の job を受けない worker(実行環境の task だけ)— 空の repo を渡す(版の木の job は commit が無く失敗する)。
    git init -q --bare "$repo"
  fi
fi
# 名乗る道具: 土台の道具の版(在る物だけ)と WORKER_TOOLS。実行環境の宣言の tools と照らされる。
tools=git=$(git --version | cut -d" " -f3)
if command -v uv >/dev/null 2>&1; then tools=$tools,uv=$(uv --version | cut -d" " -f2); fi
if command -v rustc >/dev/null 2>&1; then tools=$tools,rustc=$(rustc --version | cut -d' ' -f2); fi
if command -v claude >/dev/null 2>&1; then tools=$tools,claude=$(claude --version 2>/dev/null | cut -d' ' -f1); fi
[ -z "${WORKER_TOOLS:-}" ] || tools=$tools,$WORKER_TOOLS
echo "boot: 名乗る道具 $tools" >&2
# この Pod の worker の世代を Pod の中(container の /tmp — node の dir ではない)へ書かせる。readinessProbe が比べる。
export DOEFF_WORKER_BOOT_FILE="${DOEFF_WORKER_BOOT_FILE:-/tmp/doeff-worker-boot}"
export DOEFF_WORKER_READY_FILE="${DOEFF_WORKER_READY_FILE:-/tmp/doeff-worker-ready}"
exec hy -m doeff_cluster.main --coordinator "$COORDINATOR_URL" --name "$WORKER_NAME" \
  --labels "${WORKER_LABELS:-}" --capacity "${WORKER_CAPACITY:-10}" \
  --repo "$repo" --state-dir "$WORK_DIR/state" --stop-grace 10 \
  --import-roots "${CODE_IMPORT_ROOTS:-.}" --overlay-path "${CODE_OVERLAY_PATH:-}" \
  --repo-keys "$repo_keys" --tools "$tools" --pass-env "${WORKER_PASS_ENV:-}"
