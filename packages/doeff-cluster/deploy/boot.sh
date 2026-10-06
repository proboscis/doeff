#!/bin/sh
# doeff-cluster の起動(k8s の Pod と手元の機体の両方で使う)。ROLE に応じて起動する。
# クラスタの仕組み(doeff_cluster)は PATH の hy の venv の物を使う。WORKER_DOEFF_COMMIT が在れば、その venv を宣言した doeff の commit
# から自分で用意する(自己起動・下)。業務のコードは worker が job ごとに用意する(CODE_REPO_URL の版の木か、実行環境の root)。
#   ROLE=coordinator … 割り当て係(LISTEN_PORT・状態は $WORK_DIR/coord/state.json・CLUSTER_NAMING = 外の系と取り交わす名の JSON)
#   ROLE=records     … effect の記録の置き場(LISTEN_PORT・RECORDS_ROOT・RECORDS_RETENTION_DAYS — record_store/core/program.hy)
#   ROLE=worker      … worker(COORDINATOR_URL = URL を `,` で並べると前から順に試す・WORKER_NAME・WORKER_PROVIDES(提供する能力の名 a,b)・
#                      WORKER_EXCLUSIVE(専用の能力 — provides の一部)・NODE_NAME(k8s の downward API の spec.nodeName —
#                      coordinator が node の label から company-machine などの能力を導く)・WORKER_CAPACITY・
#                      WORKER_TASK_RESERVE = capacity のうち task のために空けておく数(必ず渡す — 無ければ起動しない)・
#                      CODE_REPO_URL = 業務のコードの git の clone 元(空 = 版の木の job を受けない)・CODE_IMPORT_ROOTS = 木の中の
#                      import の根(`,` で並べる・既定 .)・
#                      WORKER_TOOLS = 名乗る道具に足す物(名=版,…)・WORKER_PASS_ENV = job の子へ渡す worker の環境変数の名
#                      (`,` で並べる — 実行環境の job の子は worker の環境を許可表でしか継がないので、機体の設定の path や URL を名で渡す)・
#                      WORKER_ENV_ROOTS_GIB / WORKER_ENV_MIN_FREE_GIB = 実行環境の root の置き場の 2 つの量(GiB の整数・下の既定の註))
#   ROLE=drain       … worker の Pod の preStop: coordinator に drain を頼み、この worker の上の job が他へ移るまで
#                      (上限 DRAIN_DEADLINE 秒・既定 90)待つ。結末は container の log(PID 1 の stderr)へ 1 行
#   ROLE=prepare     … WORKER_DOEFF_COMMIT の自己起動の root を展開して準備する(.pyc の焼きまで)だけで、何も起こさずに root の path を
#                      出して終わる — 版上げの前に、今の worker の Pod の中で上げ先の版の root を先に組むため(同じ $WORK_DIR)。
#                      準備済みなら秒で終わる。上げ先の commit の script がこの役を知らないと断られるので、撃つ前に上げ先を読む
#   ROLE=access      … 読み取りの鍵の表(WORKER_REPOS)の git / ssh の設定と鍵の表の JSON だけを書き、JSON の path を出す
#   ROLE=ready       … worker の Pod の readinessProbe: coordinator の見る worker がこの Pod の worker(世代が一致)で、生きていて
#                      drain 中でなければ 0
# 環境: WORK_DIR(既定 /work)。
#
# 自己起動(WORKER_DOEFF_COMMIT が在る時 — 土台だけの image・deploy/base/Dockerfile。設計 D13・E13):
#   image に焼いた script の受け持ちは展開と引き継ぎだけ: WORKER_DOEFF_URL(既定 https://github.com/proboscis/doeff.git)の
#   WORKER_DOEFF_COMMIT を $WORK_DIR/boot/roots/<sha> に展開し(展開の済んだ印 .doeff-boot-extracted)、root の中の同じ commit の
#   packages/doeff-cluster/deploy/boot.sh へ exec で引き継ぐ(DOEFF_BOOT_FROM_ROOT=1 — 中身が同じでも引き継ぐ)。root の準備(venv と
#   doeff-vm の wheel・完成の印 .doeff-boot-ready)と役の起動は引き継いだ先 — 宣言した commit の script — がする。だから準備の手順を
#   直しても、WORKER_DOEFF_COMMIT を変えて入れ替えれば新しい手順で準備され、image を作り直さない。
#   準備: `uv sync --locked --compile-bytecode --package doeff-cluster --no-install-package doeff-vm` した venv に doeff-vm の wheel を
#   入れて、その venv の hy で起こす。wheel は実行環境の準備(worker)と同じく `uv build --wheel` で build の口(tools/doeff_cargo_backend.py
#   — Rust の部品を組む・引く入口の 1 つ・ADR-DOE-BUILD-001)を通し、口の保存先($WORK_DIR/state/wheels — source の中身の鍵)の物を
#   使い、無ければ口が組んで置く(python -m doeff_cluster.worker.entry.boot_wheel — 呼びの約束の定義点は doeff_cluster/shared/core/native_wheel.py
#   の 1 つ)。doeff-vm の source が同じなら、起動も実行環境の準備も Rust を組み直さない。続けて root の中の source(venv に editable で入る
#   dir)の bytecode を、実行環境の準備と同じ焼く道具(root の worker/entry/code_prepare.hy)で BOOT_ENTRIES の閉包だけ用意する(doeff_bake —
#   source の中身で引く保存先 DOEFF_HY_CODE_STORE から書き、中身の変わった file だけを焼く)。焼けなくても起動は続ける(import の時に作られる)。
#   同じ commit の root は完成の印で使い回す(2 回目の起動は秒)。
#   uv の cache は DOEFF_UV_CACHE_DIR(既定 $WORK_DIR/state/uv-cache — 実行環境の root と同じ dir)、Python は $WORK_DIR/state/python。crate の取得先は $WORK_DIR/state/cargo。
#   2026-10-06 より前の image の script は準備まで自分でしてから引き継ぐ — 引き継いだ先は完成の印を見て準備済みとして続ける。
#   worker の code を変える時は WORKER_DOEFF_COMMIT を変えて Pod を入れ替える(image は作り直さない)。無ければ今までどおり PATH の hy。
#
# 読み取りの鍵の表(WORKER_REPOS が在る時 — 設計 U5・U6):
#   WORKER_REPOS = 空白で並べた「<url>=<鍵の名>」(鍵の名が空 = 鍵なしで読む公開の repo)。鍵の file = $WORKER_REPO_KEYS_DIR/<鍵の名>
#   (既定 /etc/worker-repos・known_hosts も同じ dir)。worker の鍵の表の JSON(--repo-keys)と、git / ssh の設定(url ごとに別の
#   Host の別名へ書き換え、その Host に鍵を結ぶ)を $WORKER_ACCESS_DIR(既定 $HOME/.doeff-worker-repos)に書き、GIT_CONFIG_GLOBAL と
#   GIT_SSH_COMMAND(ssh -F)でそこへ向ける(機体の ~/.gitconfig と ~/.ssh/config は読まない・書かない)。GitHub の deploy key は repo ごとなので、鍵を 1 本しか渡さない
#   GIT_SSH_COMMAND では uv の git の依存(別の非公開 repo)を読めない。表は url に鍵を結ぶだけで url を断らない — 表に無い url は
#   worker が鍵なしで clone する(公開の repo は通り、読めない非公開の repo は clone の失敗 repo-unreachable で返る)。
#   WORKER_REPOS が無く /etc/worker-git/id が在れば、今までどおりそれを唯一の deploy key として使う。
set -eu
WORK_DIR=${WORK_DIR:-/work}
role=${ROLE:-worker}
# source の中身で引く bytecode の保存先(doeff-hy の code_store — 入口は 1 つ・版と root をまたいで同じ中身の source の code と import の
# 名を引く)。worker の永続の dir の下に置き、自分の子(実行環境の準備・焼く道具・job)へ継ぐ。呼び手が値を置けばそれを使う(日次の全体
# 検証の task と、手元の 1 台の cluster を起こすテストは件をまたいで同じ dir を渡す)。7 日使われない entry は worker の掃除が消す。
export DOEFF_HY_CODE_STORE="${DOEFF_HY_CODE_STORE:-$WORK_DIR/state/doeff-hy-code-store}"
# uv の cache の dir(root の準備の uv と、worker の実行環境の準備の uv の子の UV_CACHE_DIR — worker へは --uv-cache で渡す)。既定は
# worker の永続の dir の下。呼び手が値を置けばそれを使う(手元の 1 台の cluster を件ごとに起こすテストは、件をまたいで同じ dir を渡して
# 依存を件ごとに取り直さない・#3858)。
export DOEFF_UV_CACHE_DIR="${DOEFF_UV_CACHE_DIR:-$WORK_DIR/state/uv-cache}"
# 知らない役は、展開も準備もせず名指しで断る(worker の起動へ落とさない — 走っている worker の Pod の中で役 prepare を、その役を
# 知らない版へ向けて撃っても、2 つ目の worker を起こさない)。
case "$role" in
  coordinator|records|worker|drain|access|ready|prepare) ;;
  *)
    echo "boot: 知らない役 ROLE=$role(coordinator・records・worker・drain・access・ready・prepare のどれか)" >&2
    exit 2 ;;
esac
if [ "$role" = prepare ] && [ -z "${WORKER_DOEFF_COMMIT:-}" ]; then
  echo "boot: ROLE=prepare は WORKER_DOEFF_COMMIT(準備する doeff の commit)が要る" >&2
  exit 2
fi
# 自己起動の root に bytecode を焼く範囲の入口(`,` で並べる・役で分けない): 下の役が root の venv の hy で起こす module の全部と、worker が
# 同じ venv で起こす準備の process(env_tool)・shim・job の子の入口(job_entry)と、venv の .pth が interpreter の起動ごとに import する
# doeff-hy の doeff_hy_bytecode_guard(どこからも import の文で辿れない)。焼くのはこの入口から import を辿った閉包だけ。
BOOT_ENTRIES=doeff_cluster.worker.entry.main,doeff_cluster.worker.entry.drain_main,doeff_cluster.coordinator.entry.main,doeff_cluster.record_store.entry.main,doeff_cluster.worker.entry.env_tool,doeff_cluster.worker.entry.shim,doeff_cluster.worker.entry.job_entry,doeff_hy_bytecode_guard
# 焼く道具が焼き終えた木の根に置く完成の印(doeff_cluster/worker/core/code_plan.hy の MARKER と同じ名)。
CODE_MARKER=.doeff-code-ready.json

# 今の刻(epoch ミリ秒)。GNU の date は %3N でミリ秒を出す・BSD(macOS の worker)は出さないので秒の 1000 倍。
boot_ms() {
  ms=$(date +%s%3N 2>/dev/null || true)
  case "$ms" in
    ''|*[!0-9]*) echo $(( $(date +%s) * 1000 )) ;;
    *) echo "$ms" ;;
  esac
}
# 起動の内訳の刻(#3676 — worker が最初の heartbeat の答えの後に 1 行で出す): この script の始まり。起動の script の引き継ぎ(下の exec)を
# またいで最初の値を保つ(引き継いだ先では置き直さない)。
if [ -z "${DOEFF_BOOT_STARTED_MS:-}" ]; then
  DOEFF_BOOT_STARTED_MS=$(boot_ms)
fi
export DOEFF_BOOT_STARTED_MS

# 自己起動の root を展開する(image に焼いた script の受け持ち — 準備はしない)。引き継いだ先も sha と root を求めるために呼ぶ(展開の済んだ
# root には何もしない)。drain は展開しない(preStop で clone も展開もしない — 準備の済んだ root を使うだけ)。
doeff_extract() {
  commit=$WORKER_DOEFF_COMMIT
  url=${WORKER_DOEFF_URL:-https://github.com/proboscis/doeff.git}
  boot=$WORK_DIR/boot
  mkdir -p "$boot/roots" "$WORK_DIR/state"
  # 同じ node の dir を使う次の Pod と重ならないよう、展開と準備は排他(fd 8・済めば離す)。
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
  if [ -f "$root/.doeff-boot-ready" ] || [ -f "$root/.doeff-boot-extracted" ]; then
    :
  elif [ "$role" = drain ]; then
    echo "boot: doeff $sha の root が無い(drain は準備しない)" >&2
    exit 1
  else
    started=$(date +%s)
    # 印の無い root はこの script が途中で止まった残り — 作り直す。
    rm -rf "$root"
    mkdir -p "$root"
    git -C "$boot/doeff.git" archive --format=tar "$sha" | tar -x -C "$root"
    touch "$root/.doeff-boot-extracted"
    echo "boot: doeff $sha を展開した($(( $(date +%s) - started )) 秒)" >&2
  fi
  exec 8>&-
}

# 展開した root を準備して PATH の頭に置く(引き継いだ先 — 宣言した commit の script — の受け持ち)。drain は準備せず、完成した root を
# 使うだけ(preStop で build しない)。
doeff_prepare() {
  export UV_CACHE_DIR="$DOEFF_UV_CACHE_DIR" UV_PYTHON_INSTALL_DIR="$WORK_DIR/state/python" CARGO_HOME="$WORK_DIR/state/cargo"
  export UV_NO_PROGRESS=1
  exec 8>"$boot/boot.lock"
  flock 8
  if [ -f "$root/.doeff-boot-ready" ]; then
    echo "boot: doeff $sha の root を使う(準備済み)" >&2
  elif [ "$role" = drain ]; then
    echo "boot: doeff $sha の root が準備されていない(drain は準備しない)" >&2
    exit 1
  else
    started=$(date +%s)
    # 完成の印の無い venv は準備が途中で止まった残り — 作り直す。
    rm -rf "$root/.venv"
    # doeff-vm(Rust)は uv sync で source から組まず、実行環境の準備と同じ鍵の組み済みの wheel を入れる(下)。第三者の package の
    # .pyc は uv が焼く(--compile-bytecode — 実行環境の準備の uv sync と同じ)。
    # 準備の間の Python は、root の中の source に timestamp の方式の .pyc を書かない(PYTHONDONTWRITEBYTECODE — 書くと焼く道具がその .py を
    # 焼く物から外す・下の doeff_bake)。uv の焼きの子も venv の .pth を読んで doeff_hy_bytecode_guard を import する(立てないと、その
    # 3 file が焼く前に timestamp の方式で書かれていた — 2026-10-06 の docker の空の /work の回で測った)。uv 自身の焼き(site-packages)は
    # 明示の compile なので、この変数で止まらない。
    (cd "$root" && PYTHONDONTWRITEBYTECODE=1 uv sync --locked --compile-bytecode --package doeff-cluster --no-dev \
      --no-install-package doeff-vm >&2)
    synced=$(date +%s)
    # 答え = 1 行「<組んだ|使った|組んだかは不明で用意した> <wheel の path>」(path は uv build が root の下の --out-dir に出した wheel・
    # 組めなければ理由を stderr に出して非 0 — set -e で止まり、印を置かない)。
    answer=$(PYTHONDONTWRITEBYTECODE=1 "$root/.venv/bin/python" -m doeff_cluster.worker.entry.boot_wheel --root "$root" \
      --state "$WORK_DIR/state" --uv-cache "$DOEFF_UV_CACHE_DIR")
    how=${answer%% *}
    wheel=${answer#* }
    wheeled=$(date +%s)
    PYTHONDONTWRITEBYTECODE=1 uv pip install --no-deps --python "$root/.venv/bin/python" "$wheel" >&2
    installed=$(date +%s)
    doeff_bake
    touch "$root/.doeff-boot-ready"
    echo "boot: doeff $sha の root を準備した(uv sync $((synced - started)) 秒・doeff-vm の wheel を${how} $((wheeled - synced)) 秒・wheel の install $((installed - wheeled)) 秒・${baked})" >&2
  fi
  exec 8>&-
  export PATH="$root/.venv/bin:$PATH"
}

# 準備する root の中の source(venv の .pth が書く root の中の dir — editable で入る package)の bytecode を用意する(doeff_prepare が
# boot.lock を持ったまま呼ぶ・#3725)。道具は実行環境の準備(worker/protocol/env_translation の CompileTrees)と同じ — root の
# 版の worker/entry/code_prepare.hy を root の venv の hy で 1 回起こし、BOOT_ENTRIES の閉包だけを、import の時に source の hash を検める
# 方式で用意する(引数の意味と揃え方は道具の頭の註)。.pyc は source の中身で引く保存先(DOEFF_HY_CODE_STORE)から書き、中身の変わった
# file だけを焼く(#3858 — 前の root からの引き継ぎは持たない)。焼けなくても起動は続ける — 焼かれなかった module は import の時に
# 作られる(遅くなるだけ)。結果は準備の行に載せる 1 句(baked)。
doeff_bake() {
  tool=$root/packages/doeff-cluster/src/doeff_cluster/worker/entry/code_prepare.hy
  if [ ! -f "$tool" ] || [ ! -x "$root/.venv/bin/hy" ]; then
    baked="bytecode を焼かない(焼く道具か venv の hy が root に無い — import の時に作られる)"
    return 0
  fi
  real=$(cd "$root" && pwd -P)
  # 焼く根: site-packages の .pth の行のうち root の中の dir(root からの相対 path・root そのものは `.`)。site の規則どおり、空行と #
  # の行は読まず、import で始まる行は実行される code なので根にしない。相対の行は site-packages からの path。uv は末尾に改行を書かない。
  roots=""
  for pth in "$root"/.venv/lib/python*/site-packages/*.pth; do
    [ -f "$pth" ] || continue
    site=${pth%/*}
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in
        ''|'#'*|'import '*|'import	'*) continue ;;
        /*) dir=$line ;;
        *) dir=$site/$line ;;
      esac
      [ -d "$dir" ] || continue
      dir=$(cd "$dir" && pwd -P) || continue
      case "$dir" in
        "$real") rel=. ;;
        "$real"/*) rel=${dir#"$real"/} ;;
        *) continue ;;
      esac
      case ",$roots," in
        *",$rel,"*) ;;
        *) roots=${roots:+$roots,}$rel ;;
      esac
    done <"$pth"
  done
  if [ -z "$roots" ]; then
    baked="bytecode を焼かない(venv の .pth に root の中の dir が無い)"
    return 0
  fi
  set -- --revision "$sha" --entries "$BOOT_ENTRIES" --tree "$root" --roots "$roots"
  bake_started=$(date +%s)
  # 焼く道具そのもの(Hy)の import が timestamp の方式の .pyc を root へ書かないよう PYTHONDONTWRITEBYTECODE を立てる(道具の頭の註)。
  # 道具は stderr に報告の行を書く — 全体の行(stored=… rebuilt=… reused=… failed=… compile_s=…)から数を読む。
  if report=$(cd "$root" && PYTHONDONTWRITEBYTECODE=1 "$root/.venv/bin/hy" "$tool" "$@" 2>&1); then
    counts=$(printf '%s\n' "$report" |
      sed -n 's/.*\(stored=[0-9]* rebuilt=[0-9]* reused=[0-9]* failed=[0-9]*\) compile_s=.*/\1/p' | tail -n 1)
  else
    counts=""
  fi
  if [ -n "$counts" ]; then
    baked="bytecode $(( $(date +%s) - bake_started )) 秒(${counts})"
  else
    baked="bytecode を焼けない(import の時に作られる): $(printf '%s\n' "$report" | tail -n 3 | tr '\n' ' ')"
  fi
}

# 読み取りの鍵の表から、worker の鍵の表の JSON と、url ごとに鍵を選ぶ git / ssh の設定を書き、git と ssh をそこへ向ける
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
      [ -f "$key" ] || { echo "boot: 鍵の表の鍵 $key が無い" >&2; exit 1; }
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
  doeff_extract
  # image に焼いた script は展開までを受け持ち、準備と役の起動は宣言した commit の script へ引き継ぐ — 中身が同じでも引き継ぐ(準備の手順の
  # 持ち主は commit の側)。だから起動の script(準備の手順を含む)を直しても、WORKER_DOEFF_COMMIT を変えて入れ替えれば新しい script で
  # 準備して起き、image を作り直さない。引き継いだ先(DOEFF_BOOT_FROM_ROOT)ではもう引き継がない。
  if [ -z "${DOEFF_BOOT_FROM_ROOT:-}" ]; then
    next=$root/packages/doeff-cluster/deploy/boot.sh
    if [ ! -f "$next" ]; then
      echo "boot: doeff $sha の root に起動の script(packages/doeff-cluster/deploy/boot.sh)が無い" >&2
      exit 1
    fi
    echo "boot: 起動の script を doeff $sha の物へ引き継ぐ" >&2
    export DOEFF_BOOT_FROM_ROOT=1
    exec sh "$next"
  fi
  doeff_prepare
fi

case "$role" in
  prepare)
    # 上の展開と準備(doeff_extract・doeff_prepare — どちらも boot.lock の下・.pyc の焼きを含む)だけで終わる。役は何も起こさず、
    # 準備した root の path を 1 行出す。準備済みの root なら完成の印を読んで秒で抜ける。
    echo "$root"
    exit 0 ;;
  access)
    # 鍵の表の設定(git / ssh の設定と worker の鍵の表の JSON)だけを書き、JSON の path を出す — 手元の機体と検で確かめるため。
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
    exec hy -m doeff_cluster.worker.entry.drain_main drain --coordinator "$COORDINATOR_URL" --name "$WORKER_NAME" \
      --deadline "${DRAIN_DEADLINE:-90}" --boot-file "${DOEFF_WORKER_BOOT_FILE:-/tmp/doeff-worker-boot}" 2>>/proc/1/fd/2 ;;
  coordinator)
    mkdir -p "$WORK_DIR/coord"
    naming=${CLUSTER_NAMING:-}
    [ -n "$naming" ] || naming='{}'
    # worker の生死の出来事(#3864)を出す知らせの broker(redis://… か memory)は既定なし — manifest が NOTICE_BROKER を名指す。
    # Redis の時は、繋ぐ・送るの答えを待つ上限 NOTICE_TIMEOUT_SECONDS と、戻りを待つ間だけ繋がるかを試す間隔 NOTICE_RETRY_SECONDS も
    # 名指す(無ければ coordinator の起動の引数が断る)。
    [ -n "${NOTICE_BROKER:-}" ] || { echo "boot.sh: ROLE=coordinator には NOTICE_BROKER(redis://… か memory)が要る" >&2; exit 2; }
    set -- --notice-broker "$NOTICE_BROKER"
    [ -z "${NOTICE_TIMEOUT_SECONDS:-}" ] || set -- "$@" --notice-timeout-seconds "$NOTICE_TIMEOUT_SECONDS"
    [ -z "${NOTICE_RETRY_SECONDS:-}" ] || set -- "$@" --notice-retry-seconds "$NOTICE_RETRY_SECONDS"
    exec hy -m doeff_cluster.coordinator.entry.main --state-file "$WORK_DIR/coord/state.json" --port "${LISTEN_PORT:-8080}" \
      --naming "$naming" "$@" ;;
  records)
    exec hy -m doeff_cluster.record_store.entry.main --root "${RECORDS_ROOT:-/records}" --port "${LISTEN_PORT:-8080}" \
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
# 旧い WORKER_LABELS(置き場所の label)は受け付けない — 能力を WORKER_PROVIDES / WORKER_EXCLUSIVE で名乗る(ADR-DOE-CLUSTER-001 R4b)。
if [ -n "${WORKER_LABELS:-}" ]; then
  echo "boot: WORKER_LABELS は受け付けない — WORKER_PROVIDES(と WORKER_EXCLUSIVE)で提供する能力を名乗る" >&2
  exit 2
fi
# task のために空けておく数(WORKER_TASK_RESERVE)は必ず渡す — 既定の値で埋めない(範囲 0 以上 capacity 以下は worker の入口が検める)。
if [ -z "${WORKER_TASK_RESERVE:-}" ]; then
  echo "boot: WORKER_TASK_RESERVE が無い — capacity のうち task のために空けておく数(0 以上 WORKER_CAPACITY 以下)を渡す" >&2
  exit 2
fi
# 実行環境の root の置き場の 2 つの量(#3732 — 掃除の下限を disk 全体の割合から絶対の量へ。既定の値はここの 1 か所で、worker の
# 入口は必ずの引数として受ける)。台ごとに変える時は Deployment の env に GiB の整数で書く:
#   WORKER_ENV_ROOTS_GIB    roots の合計の上限(既定 20)— 越えた時だけ、固定(走っている job・宣言の job・準備中・温める表)でも
#                           project ごとの新しい 2 つ(今の版と戻し先の版)でもない root を、最後に使った古い順に消す。合計は root ごとの
#                           大きさの和で、root どうしが hardlink で共有する木と .pyc を重ねて数える(実の使用量より大きく出る)。
#                           2026-10-06 の実測で root 1 つは重ねて数えて 0.19〜0.25 GB(zeus の service worker の業務の root)— 専用の
#                           service worker(/work が共有の USB の SSD の hostPath)は 1(root 4 本分)で足りる。
#   WORKER_ENV_MIN_FREE_GIB 共有の disk の空きの最低(既定 25)— 割った時は root を消さずに準備を disk-full で断り、heartbeat で
#                           exhausted を名乗る(coordinator は準備済みでない env の task を置かない)。
env_roots_gib=${WORKER_ENV_ROOTS_GIB:-20}
env_min_free_gib=${WORKER_ENV_MIN_FREE_GIB:-25}
for amount in "WORKER_ENV_ROOTS_GIB=$env_roots_gib" "WORKER_ENV_MIN_FREE_GIB=$env_min_free_gib"; do
  case "${amount#*=}" in
    ''|*[!0-9]*)
      echo "boot: ${amount%%=*} は GiB の 0 以上の整数で渡す(受けた値: ${amount#*=})" >&2
      exit 2 ;;
  esac
done
# worker を exec する刻(起動の内訳の 3 番目の刻・#3676)。
DOEFF_BOOT_EXEC_MS=$(boot_ms)
export DOEFF_BOOT_EXEC_MS
exec hy -m doeff_cluster.worker.entry.main --coordinator "$COORDINATOR_URL" --name "$WORKER_NAME" \
  --provides "${WORKER_PROVIDES:-}" --exclusive "${WORKER_EXCLUSIVE:-}" --node "${NODE_NAME:-}" --capacity "${WORKER_CAPACITY:-10}" \
  --task-reserve "${WORKER_TASK_RESERVE}" \
  --repo "$repo" --state-dir "$WORK_DIR/state" --stop-grace 10 \
  --import-roots "${CODE_IMPORT_ROOTS:-.}" \
  --repo-keys "$repo_keys" --tools "$tools" --pass-env "${WORKER_PASS_ENV:-}" \
  --env-roots-cap "$((env_roots_gib * 1073741824))" --env-min-free "$((env_min_free_gib * 1073741824))" \
  --code-store "$DOEFF_HY_CODE_STORE" --uv-cache "$DOEFF_UV_CACHE_DIR"
