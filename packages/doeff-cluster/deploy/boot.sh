#!/bin/sh
# doeff-cluster の起動(k8s の Pod と手元の機体の両方で使う)。ROLE に応じて起動する。
# クラスタの仕組み(doeff_cluster)は PATH の hy の venv の物を使う。WORKER_DOEFF_COMMIT が在れば、その venv を宣言した doeff の commit
# から自分で用意する(自己起動・下)。業務のコードは worker が job ごとに用意する(CODE_REPO_URL の版の木か、実行環境の root)。
#   ROLE=coordinator … 割り当て係(LISTEN_PORT・状態は $WORK_DIR/coord/state.json・CLUSTER_NAMING = 外の系と取り交わす名の JSON)
#   ROLE=records     … effect の記録の置き場(LISTEN_PORT・RECORDS_ROOT・RECORDS_RETENTION_DAYS — record_store/core/program.hy)
#   ROLE=worker      … worker(COORDINATOR_URL = URL を `,` で並べると前から順に試す・WORKER_NAME・WORKER_PROVIDES(提供する能力の名 a,b)・
#                      WORKER_EXCLUSIVE(専用の能力 — provides の一部)・NODE_NAME(k8s の downward API の spec.nodeName —
#                      coordinator が node の label から company-machine などの能力を導く)・WORKER_ROOM_FILE = 枠の file
#                      (名 → capacity・taskReserve の JSON — 在れば heartbeat の前に読み直す)・WORKER_CAPACITY・
#                      WORKER_TASK_RESERVE = capacity のうち task のために空けておく数(枠の file が無ければ必ず渡す — 無ければ起動しない)・
#                      CODE_REPO_URL = 業務のコードの git の clone 元(空 = 版の木の job を受けない)・CODE_IMPORT_ROOTS = 木の中の
#                      import の根(`,` で並べる・既定 .)・
#                      WORKER_TOOLS = 名乗る道具に足す物(名=版,…)・WORKER_PASS_ENV = job の子へ渡す worker の環境変数の名
#                      (`,` で並べる — 実行環境の job の子は worker の環境を許可表でしか継がないので、機体の設定の path や URL を名で渡す)・
#                      WORKER_ENV_ROOTS_GIB / WORKER_ENV_MIN_FREE_GIB = 実行環境の root の置き場の 2 つの量(GiB の整数・下の既定の註))
#   ROLE=drain       … worker の Pod の preStop: coordinator に drain を頼み、この worker の上の job が他へ移るまで
#                      (上限 DRAIN_DEADLINE 秒・既定 90)待つ。結末は container の log(PID 1 の stderr)へ 1 行
#   ROLE=prepare     … WORKER_DOEFF_COMMIT の自己起動の root を展開して準備する(.pyc の焼きまで)だけで、何も起こさずに root の path を
#                      出して終わる — 版上げの前に、今の worker の Pod の中で上げ先の版の root を先に組むため(同じ $WORK_DIR)。
#                      準備済みなら秒で終わる。上げ先の commit の script がこの役を知らないと断られるので、実行する前に上げ先を読む。
#                      bytecode の焼きが失敗した時は完成の印を置かず、path を出さずに非 0 で終わる(下の「完成の印」)
#   ROLE=access      … 読み取りの鍵の表(WORKER_REPOS)の git / ssh の設定と鍵の表の JSON だけを書き、JSON の path を出す
#   ROLE=ready       … worker の Pod の readinessProbe: この Pod の worker が coordinator へ heartbeat を届けていれば 0(drain 中かどうかは
#                      見ない — drain は coordinator の配りの状態で Pod の健康ではなく、Pod の作り直しをまたいで残る・#4177)
#   ROLE=cgroup      … WORKER_CGROUP_LAYOUT の cgroup の層(下の「cgroup の層」)だけを作り、書ける cgroup の dir を出して終わる — 検と、
#                      手元で層を確かめるため。役 worker は起動の頭で同じ層を作る
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
#   source の中身で引く保存先 DOEFF_HY_CODE_STORE から書き、中身の変わった file だけを焼く)。
#   同じ commit の root は完成の印で使い回す(2 回目の起動は秒)。
#   完成の印 .doeff-boot-ready(#4277 — 役ごとの扱いは戻せる決定): 印は焼きが通った時だけ置く。印の無い root は準備が途中で止まった
#   残りとして、次の準備(役 prepare か、起動する役の起動)が .venv を消して組み直す(root の dir は消さない・道具の印
#   .doeff-code-ready.json は道具の受け持ち)。
#     - 焼きの失敗(焼く道具が非 0 で終わる・報告に stored/rebuilt の数の行が無い)は準備の失敗: 印を置かず、stderr に理由を 1 行出す。
#       前は焼けなくても印を置いたので、焼けなかった版の root が準備済みのまま残り、次の準備は印を見て飛ばし続けた(版上げの Program は
#       その版を READY-MARK-INCOMPLETE で断り続け、直すには root の dir を手で消すしかなかった)。
#     - 役 prepare は焼きの失敗で非 0 で終わり、root の path を出さない(版上げの Program は PREPARE-FAILED を受け、次の実行で組み直す)。
#     - 起動を続ける役(worker・coordinator・records)は焼きの失敗でも同じ venv のまま起動する — 焼かれなかった module は import の時に
#       作られる(遅くなるだけ)。止めると Pod が起動しない状態になる。印が無いので次の起動が .venv を組み直す。
#     - 焼く物が無い時(焼く道具か venv の hy が root に無い・venv の .pth に root の中の dir が無い)は失敗ではないので印を置く — 焼く道具を
#       持たない古い版の root と、焼く根の無い venv は、何度組み直しても焼く物が無い。
#     - drain は組まない: 印が無くても venv の hy が在れば(焼きの失敗のまま起動した worker の preStop)それを使い、無ければ断る。
#   uv の cache は DOEFF_UV_CACHE_DIR(既定 $WORK_DIR/state/uv-cache — 実行環境の root と同じ dir)、Python は $WORK_DIR/state/python。crate の取得先は $WORK_DIR/state/cargo。
#   2026-10-06 より前の image の script は準備まで自分でしてから引き継ぐ — 引き継いだ先は完成の印を見て準備済みとして続ける。
#   worker の code を変える時は WORKER_DOEFF_COMMIT を変えて Pod を入れ替える(image は作り直さない)。無ければ今までどおり PATH の hy。
#   版の file(WORKER_VERSION_FILE)を渡す worker は Pod を入れ替えず、下の keeper が同じ container の中で worker の process を入れ替える。
#
# 版の file と keeper(WORKER_VERSION_FILE が在る役 worker — card ki-6e83e03d146d):
#   WORKER_VERSION_FILE = worker の doeff の版(40 字の sha・1 行)を持つ file。k8s では名の固定した ConfigMap worker-version を volume で
#   置いた file(subPath でない — 中身を変えても Pod は作り直されず、kubelet が file を入れ替える)。起動の版は呼び手がこの file から
#   WORKER_DOEFF_COMMIT に入れて渡す(image の script は file を読まない)。その版の script は役 worker を keeper として回す:
#     1. worker の世代(process 1 つ)を自分の子から外して起こす(回収は PID 1 の tini)。世代ごとの状態の dir は $WORK_DIR/gen/<世代>
#        (下の WORKER_GENERATION_DIRS — 世代の名は <POD_UID>.<起こした刻>)・boot と ready と pid の file は $DOEFF_KEEPER_DIR/gens/<世代>/。
#     2. KEEPER_TICK_SECONDS 秒(既定 2)ごとに版の file を読み、版が替わると、その版の root を役 prepare で背景で準備し(走っている世代は
#        止めない)、準備できたらその版の世代を同じ container に起こす(前の世代の錠が持たれているので --overlaps-elder — 同じ機体に同じ名の
#        2 つの世代を並べる RollingUpdate と同じ形・AL-D2)。
#     3. いちばん新しい世代が coordinator に名乗れて自分の ready の file を書いた後(受け渡しの待ち handover-pending が解けた後)に、前の
#        世代を世代つきで drain し(旧い Pod の preStop と同じ drain_main・上限 DRAIN_DEADLINE)、drained の後に SIGTERM を送る。前の世代は
#        自分の task と退いた handoff の process を終わりまで回してから退く(新しい世代は引き取らない)。新しい世代が名乗れない間(断られる・
#        古い coordinator)は前の世代を drain しない — 前の世代が仕事を続ける。
#     4. いちばん新しい世代が落ちたら、間を空けて(30 秒から倍にして 300 秒まで)同じ版で起こし直す。準備の失敗は 300 秒後に撃ち直す。
#        世代を知らない版(起動の script に DOEFF_WORKER_GENERATION の無い commit)へは替えず、log に 1 行出して今の版のまま。
#     5. Pod の ready: 生きているどれかの世代の ready の file が新しければ、readinessProbe の読む file(DOEFF_WORKER_READY_FILE・既定
#        /tmp/doeff-worker-ready)へ ready を写す。いちばん新しい ready の世代の boot は DOEFF_WORKER_BOOT_FILE へ写す。
#     6. SIGTERM・SIGINT: 生きている全部の世代へ SIGTERM を渡し、終わるのを待って抜ける。役 drain(preStop)は生きている全部の世代を
#        世代つきで並べて drain し、その間 keeper は版を替えず、落ちた世代を起こし直さない(印 $DOEFF_KEEPER_DIR/leaving)。
#   keeper の code は container の起動の版のまま(keeper そのものの直しは Pod の作り直しで効く)。世代の起動・準備・drain は、その世代の
#   版の script と venv を使う。keeper の状態の dir は DOEFF_KEEPER_DIR(既定 /tmp/doeff-worker-keeper — container の中・Pod の作り直しで消える)。
#
# 読み取りの鍵の表(WORKER_REPOS が在る時 — 設計 U5・U6):
#   WORKER_REPOS = 空白で並べた「<url>=<鍵の名>」(鍵の名が空 = 鍵なしで読む公開の repo)。鍵の file = $WORKER_REPO_KEYS_DIR/<鍵の名>
#   (既定 /etc/worker-repos・known_hosts も同じ dir)。worker の鍵の表の JSON(--repo-keys)と、git / ssh の設定(url ごとに別の
#   Host の別名へ書き換え、その Host に鍵を結ぶ)を $WORKER_ACCESS_DIR(既定 $HOME/.doeff-worker-repos)に書き、GIT_CONFIG_GLOBAL と
#   GIT_SSH_COMMAND(ssh -F)でそこへ向ける(機体の ~/.gitconfig と ~/.ssh/config は読まない・書かない)。GitHub の deploy key は repo ごとなので、鍵を 1 本しか渡さない
#   GIT_SSH_COMMAND では uv の git の依存(別の非公開 repo)を読めない。表は url に鍵を結ぶだけで url を断らない — 表に無い url は
#   worker が鍵なしで clone する(公開の repo は通り、読めない非公開の repo は clone の失敗 repo-unreachable で返る)。
#   WORKER_REPOS が無く /etc/worker-git/id が在れば、今までどおりそれを唯一の deploy key として使う。
#
# cgroup の層(WORKER_CGROUP_LAYOUT が在る時 — card ki-cd8cb84fdcd2):
#   1 本の task の memory を kernel が抑え、常駐の job と worker の本体の memory を kernel が守るため、worker の cgroup の中に子の cgroup を
#   作り、子ごとに memory の守り(memory.min・memory.low)か上限(memory.max・memory.high)を掛ける。worker はその子へ job を置き分ける
#   (DOEFF_WORKER_JOB_CGROUP・DOEFF_WORKER_SERVICE_CGROUP — worker/core/job_cgroup)。
#   CPU の重み(card ki-b40c5c399455): 子ごとに cpu.weight(1〜10000)と cpu.idle(0 か 1)も掛けられる。CPU が埋まった時に、遅れに
#   敏感な層(worker の本体・常駐の job)の thread を、一括の仕事の層(task・CLI・命令)より先に CPU へ載せるため — 子に cpu を掛けない
#   と、層の中の nice や SCHED_IDLE は層どうしの取り分を変えず、起きた常駐の thread が一括の仕事の後ろで実行待ちの列に並ぶ。
#   WORKER_CGROUP_LAYOUT = 空白で並べた「<名>:<file>=<値>,…」(名は英小文字・数字・-。file は memory.min・memory.low・memory.high・
#   memory.max・cpu.weight・cpu.idle のどれか。memory の値は kernel の書式のまま・cpu.weight は 1〜10000 の整数・cpu.idle は 0 か 1)。
#   最初の層に、今 worker の cgroup に居る process を全部移す(tini・この script・準備の残り)— cgroup v2 は process の居る cgroup の子に
#   controller を掛けられないため。子には memory をいつも、cpu は cpu の file を書く層が在る時だけ掛ける。例:
#     worker:memory.min=2G,cpu.weight=1000 services:memory.min=24G,cpu.weight=1000 jobs:memory.max=36G,cpu.weight=100
#   書ける見え: k8s の Pod の /sys/fs/cgroup は自分の container の cgroup を根とする読み取りだけの見えなので、機体の配備は host の cgroupfs を
#   別の所に書ける形で置き、その場所を WORKER_CGROUP_HOST で渡す。script はその中から、名前空間の根(WORKER_CGROUP_NS_ROOT・既定
#   /sys/fs/cgroup)と同じ inode の dir(= 自分の container の cgroup)を引く。WORKER_CGROUP_HOST が無ければ名前空間の根そのものに書く。
#   委ね: 書く dir は worker の uid に委ねられている事(dir・cgroup.procs・cgroup.subtree_control が書ける)。Pod では同じ Pod の root の
#   係が起動の直後に委ねるので、WORKER_CGROUP_WAIT 秒(既定 60)まで待つ。委ねられない・層を作れない・値を書けない時は起動しない
#   (守りの無いまま黙って起動しない — RollingUpdate なら旧い Pod が動き続ける)。
#   作った後、書ける dir を DOEFF_WORKER_CGROUP_DIR で worker へ渡す(worker は兄弟の cgroup.procs をその下に書き、job の子へも渡す)。
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
# Rust の wheel の保存先(build の口が source の中身の鍵で引く dir)。worker は --state-dir から同じ path を導いて自分の uv の子へ渡す
# (doeff_cluster/shared/core/native_wheel.py の wheels_root — state の下の wheels)。ここでも export して起こす役の process に置き、worker が
# job の子へ継ぐ(子の uv sync が doeff-vm を組み直さない・#3972)。worker の導く path と食い違わないように呼び手の値で上書きする。
export DOEFF_WHEEL_CACHE="$WORK_DIR/state/wheels"
# 知らない役は、展開も準備もせず名指しで断る(worker の起動へ落とさない — 走っている worker の Pod の中で役 prepare を、その役を
# 知らない版へ向けて撃っても、2 つ目の worker を起こさない)。
case "$role" in
  coordinator|records|worker|drain|access|ready|prepare|cgroup) ;;
  *)
    echo "boot: 知らない役 ROLE=$role(coordinator・records・worker・drain・access・ready・prepare・cgroup のどれか)" >&2
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

# 展開した root を準備して PATH の頭に置く(引き継いだ先 — 宣言した commit の script — の受け持ち)。drain は準備せず、準備した root を
# 使うだけ(preStop で build しない)。完成の印の置き方と役ごとの扱いは頭の註の「完成の印」。
doeff_prepare() {
  export UV_CACHE_DIR="$DOEFF_UV_CACHE_DIR" UV_PYTHON_INSTALL_DIR="$WORK_DIR/state/python" CARGO_HOME="$WORK_DIR/state/cargo"
  export UV_NO_PROGRESS=1
  exec 8>"$boot/boot.lock"
  flock 8
  if [ -f "$root/.doeff-boot-ready" ]; then
    echo "boot: doeff $sha の root を使う(準備済み)" >&2
  elif [ "$role" = drain ]; then
    # 印の無い root で起動している worker(焼きの失敗のまま起動を続けた)の preStop は、その venv の hy を使う — drain は組まない。
    if [ ! -x "$root/.venv/bin/hy" ]; then
      echo "boot: doeff $sha の root が準備されていない(drain は準備しない)" >&2
      exit 1
    fi
    echo "boot: doeff $sha の root に完成の印が無い — venv の hy をそのまま使う(drain は組み直さない)" >&2
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
    spent="uv sync $((synced - started)) 秒・doeff-vm の wheel を${how} $((wheeled - synced)) 秒・wheel の install $((installed - wheeled)) 秒"
    case "$bake_result" in
      baked|nothing)
        touch "$root/.doeff-boot-ready"
        echo "boot: doeff $sha の root を準備した(${spent}・${baked})" >&2 ;;
      failed)
        # 焼きの失敗は準備の失敗 — 印を置かない(次の準備が上の分岐で .venv を組み直す)。役 prepare は非 0 で終わり、root の path を
        # 出さない。起動を続ける役はこの venv のまま起動する(焼かれなかった module は import の時に作られる)。
        echo "boot: doeff $sha の root の bytecode を焼けない — 完成の印 .doeff-boot-ready を置かない(次の準備が .venv を組み直す・${spent}・${baked})" >&2
        if [ "$role" = prepare ]; then
          exit 1
        fi ;;
      *)
        echo "boot: 焼きの結果 '$bake_result' は baked・nothing・failed のどれでもない" >&2
        exit 1 ;;
    esac
  fi
  exec 8>&-
  export PATH="$root/.venv/bin:$PATH"
}

# 準備する root の中の source(venv の .pth が書く root の中の dir — editable で入る package)の bytecode を用意する(doeff_prepare が
# boot.lock を持ったまま呼ぶ・#3725)。道具は実行環境の準備(worker/protocol/env_translation の CompileTrees)と同じ — root の
# 版の worker/entry/code_prepare.hy を root の venv の hy で 1 回起こし、BOOT_ENTRIES の閉包だけを、import の時に source の hash を検める
# 方式で用意する(引数の意味と揃え方は道具の頭の註)。.pyc は source の中身で引く保存先(DOEFF_HY_CODE_STORE)から書き、中身の変わった
# file だけを焼く(#3858 — 前の root からの引き継ぎは持たない)。結果は 2 つ: 準備の行に載せる 1 句(baked)と、完成の印を置くかを
# 決める 3 値(bake_result)— baked = 焼けた・nothing = 焼く物が無い(失敗ではない — 印を置く)・failed = 焼く道具が非 0 で終わったか、
# 報告に stored/rebuilt の数の行が無い(印を置かない — 頭の註の「完成の印」)。どれでも 0 で返す(set -e の下で呼ぶ)。
doeff_bake() {
  tool=$root/packages/doeff-cluster/src/doeff_cluster/worker/entry/code_prepare.hy
  if [ ! -f "$tool" ] || [ ! -x "$root/.venv/bin/hy" ]; then
    # 焼く道具を持たない版の root か、hy の無い venv — 組み直しても焼く物は無い(import の時に作られる)。
    baked="bytecode を焼かない(焼く道具か venv の hy が root に無い — import の時に作られる)"
    bake_result=nothing
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
    # 焼く根の無い venv — 組み直しても焼く物は無い(import の時に作られる)。
    baked="bytecode を焼かない(venv の .pth に root の中の dir が無い)"
    bake_result=nothing
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
    bake_result=baked
  else
    baked="焼く道具の報告の末尾: $(printf '%s\n' "$report" | tail -n 3 | tr '\n' ' ')"
    bake_result=failed
  fi
}

# 読み取りの鍵の表の今の値を出す: WORKER_REPOS_FILE(ConfigMap の dir の mount の file — kubelet が更新を届ける)が読めればその中身、
# 無ければ env の WORKER_REPOS(起動の時の値)。
worker_repos_table() {
  if [ -n "${WORKER_REPOS_FILE:-}" ] && [ -r "$WORKER_REPOS_FILE" ]; then
    cat "$WORKER_REPOS_FILE"
  else
    printf '%s' "${WORKER_REPOS:-}"
  fi
}

# 鍵の表の組みを途中で断る(書きかけの別名の file を消し、置き場の 3 つの file は前の値のまま残す)。
access_refused() {
  rm -f "$dir/.ssh_config.$$" "$dir/.gitconfig.$$" "$dir/.repo-keys.json.$$"
  echo "boot: $1" >&2
  exit 1
}

# 読み取りの鍵の表から、worker の鍵の表の JSON と、url ごとに鍵を選ぶ git / ssh の設定を書き、git と ssh をそこへ向ける
# (export するので subshell で呼ばない)。JSON の path は repo_keys に置く。3 つの file は別名に書いてから置き換える — 走っている
# worker の隣で組み直しても(worker が準備の前に ROLE=access で撃つ・card ki-2de748a0eac5)、git・ssh・準備の process は書きかけを読まない。
repo_access() {
  keys=${WORKER_REPO_KEYS_DIR:-/etc/worker-repos}
  dir=${WORKER_ACCESS_DIR:-$HOME/.doeff-worker-repos}
  mkdir -p "$dir"
  chmod 700 "$dir"
  : >"$dir/.ssh_config.$$"
  : >"$dir/.gitconfig.$$"
  json="{"
  n=0
  table=$(worker_repos_table)
  for entry in $table; do
    url=${entry%=*}
    name=${entry##*=}
    key=""
    if [ -n "$name" ]; then
      key=$keys/$name
      [ -f "$key" ] || access_refused "鍵の表の鍵 $key が無い"
      n=$((n + 1))
      alias=doeff-repo-$n
      # url の形: ssh://git@<host>/<path> か git@<host>:<path>
      case "$url" in
        ssh://*) rest=${url#ssh://}; userhost=${rest%%/*}; path=${rest#*/} ;;
        *@*:*) userhost=${url%%:*}; path=${url#*:} ;;
        *) access_refused "鍵つきの url は ssh の形にする: $url" ;;
      esac
      user=${userhost%@*}
      host=${userhost#*@}
      printf 'Host %s\n  HostName %s\n  User %s\n  IdentityFile %s\n  IdentitiesOnly yes\n  UserKnownHostsFile %s/known_hosts\n  StrictHostKeyChecking yes\n' \
        "$alias" "$host" "$user" "$key" "$keys" >>"$dir/.ssh_config.$$"
      printf '[url "ssh://%s@%s/%s"]\n  insteadOf = %s\n' "$user" "$alias" "$path" "$url" >>"$dir/.gitconfig.$$"
    fi
    [ "$json" = "{" ] || json="$json,"
    json="$json\"$url\":\"$key\""
  done
  # 表に無い url(鍵なしで読む ssh の repo)は、鍵が無ければ問いや待ちで止まらず、すぐ断られて repo-unreachable の理由に出る
  # (対話の問いに答えられない・届かない相手を待ち続けると、準備が進みの無いまま停滞の窓で止まり、理由の行が残らなかった)。
  # 先に書いた Host が先に効くので、ここは鍵つきの項目の後ろに置く。
  printf 'Host *\n  BatchMode yes\n  ConnectTimeout 30\n' >>"$dir/.ssh_config.$$"
  chmod 600 "$dir/.ssh_config.$$"
  printf '%s}\n' "$json" >"$dir/.repo-keys.json.$$"
  mv -f "$dir/.ssh_config.$$" "$dir/ssh_config"
  mv -f "$dir/.gitconfig.$$" "$dir/gitconfig"
  mv -f "$dir/.repo-keys.json.$$" "$dir/repo-keys.json"
  export GIT_CONFIG_GLOBAL="$dir/gitconfig" GIT_SSH_COMMAND="ssh -F $dir/ssh_config"
  repo_keys=$dir/repo-keys.json
}

# cgroup の層を断る(頭の註の「cgroup の層」— 守りの無いまま起動しない)。
cgroup_refused() {
  echo "boot: cgroup の層: $1" >&2
  exit 2
}

# 頭の註の「cgroup の層」を作るため: 宣言を全部検めてから、書ける dir を引き、委ねを待ち、層を作り、process を最初の層へ移して子に memory
# (と、cpu の file を書く層が在れば cpu)を掛け、層ごとの値を書く。書ける dir を cgroup_dir に置き、DOEFF_WORKER_CGROUP_DIR で export する。
cgroup_layout() {
  set -f
  problems=""
  first=""
  controllers=+memory
  for entry in $WORKER_CGROUP_LAYOUT; do
    name=${entry%%:*}
    case "$name" in
      ''|*[!a-z0-9-]*) problems="$problems 層の名 '$name' は英小文字・数字・- で書く($entry)。" ;;
    esac
    [ -n "$first" ] || first=$name
    case "$entry" in *:*) settings=${entry#*:} ;; *) settings="" ;; esac
    old_ifs=$IFS
    IFS=,
    for setting in $settings; do
      key=${setting%%=*}
      value=${setting#*=}
      if [ "$value" = "$setting" ]; then
        problems="$problems 層 $name の $key に値が無い。"
        continue
      fi
      case "$key" in
        memory.min|memory.low|memory.high|memory.max)
          case "$value" in
            max|[0-9]*) ;;
            *) problems="$problems 層 $name の $key の値 '$value' は数(K・M・G を付けてよい)か max で書く。" ;;
          esac ;;
        cpu.weight)
          controllers="+memory +cpu"
          case "$value" in
            ''|*[!0-9]*|0*) problems="$problems 層 $name の cpu.weight の値 '$value' は 1〜10000 の整数で書く。" ;;
            *) [ "${#value}" -le 5 ] && [ "$value" -le 10000 ] ||
                 problems="$problems 層 $name の cpu.weight の値 '$value' は 1〜10000 の整数で書く。" ;;
          esac ;;
        cpu.idle)
          controllers="+memory +cpu"
          case "$value" in
            0|1) ;;
            *) problems="$problems 層 $name の cpu.idle の値 '$value' は 0 か 1 で書く。" ;;
          esac ;;
        *) problems="$problems 層 $name の $key は memory.min・memory.low・memory.high・memory.max・cpu.weight・cpu.idle のどれかで書く。" ;;
      esac
    done
    IFS=$old_ifs
  done
  set +f
  [ -n "$first" ] || problems="$problems WORKER_CGROUP_LAYOUT に層が無い。"
  [ -z "$problems" ] || cgroup_refused "宣言 WORKER_CGROUP_LAYOUT を読めない:$problems"
  ns_root=${WORKER_CGROUP_NS_ROOT:-/sys/fs/cgroup}
  if [ -n "${WORKER_CGROUP_HOST:-}" ]; then
    ino=$(stat -c %i "$ns_root") || cgroup_refused "名前空間の根 $ns_root を読めない"
    cgroup_dir=$(find "$WORKER_CGROUP_HOST" -maxdepth 6 -type d -inum "$ino" 2>/dev/null | head -n 1)
    [ -n "$cgroup_dir" ] || cgroup_refused "$WORKER_CGROUP_HOST の中に名前空間の根 $ns_root(inode $ino)と同じ dir が無い"
  else
    cgroup_dir=$ns_root
  fi
  # ポーリングの訳: 委ねる係(同じ Pod の別の container)は file の持ち主を替えるだけで、替えた事を知らせる道を持たない(cgroupfs の
  # 持ち主の変更は inotify にも出ない)。起動の 1 度だけ、上限つきで 1 秒ごとに書けるかを見る(試しの Pod では 1 秒で委ねられた)。
  waited=0
  until [ -w "$cgroup_dir" ] && [ -w "$cgroup_dir/cgroup.procs" ] && [ -w "$cgroup_dir/cgroup.subtree_control" ]; do
    [ "$waited" -lt "${WORKER_CGROUP_WAIT:-60}" ] ||
      cgroup_refused "$cgroup_dir が uid $(id -u) に委ねられていない(${WORKER_CGROUP_WAIT:-60} 秒待った — 機体の配備の委ねる係を見る)"
    sleep 1
    waited=$((waited + 1))
  done
  set -f
  for entry in $WORKER_CGROUP_LAYOUT; do
    name=${entry%%:*}
    [ -d "$cgroup_dir/$name" ] || mkdir "$cgroup_dir/$name" || cgroup_refused "層 $name を作れない"
  done
  set +f
  tries=0
  while :; do
    for pid in $(cat "$cgroup_dir/cgroup.procs"); do
      # 読んだ後に終わった process は移せない(No such process)が、根にも残らないので飛ばす。
      echo "$pid" >"$cgroup_dir/$first/cgroup.procs" 2>/dev/null || [ ! -d "/proc/$pid" ] ||
        cgroup_refused "process $pid を層 $first へ移せない"
    done
    if echo "$controllers" >"$cgroup_dir/cgroup.subtree_control" 2>/dev/null; then
      break
    fi
    # 移した後に根へ入った process(readinessProbe の exec など)が居ると、子に controller を掛けられない — 移し直す。
    tries=$((tries + 1))
    [ "$tries" -lt 5 ] || cgroup_refused "$cgroup_dir の子に $controllers を掛けられない(根に process が残るか、親が cpu を子へ渡さない)"
    sleep 1
  done
  set -f
  for entry in $WORKER_CGROUP_LAYOUT; do
    name=${entry%%:*}
    case "$entry" in *:*) settings=${entry#*:} ;; *) settings="" ;; esac
    old_ifs=$IFS
    IFS=,
    for setting in $settings; do
      printf '%s\n' "${setting#*=}" >"$cgroup_dir/$name/${setting%%=*}" 2>/dev/null ||
        cgroup_refused "層 $name の ${setting%%=*} に ${setting#*=} を書けない"
    done
    IFS=$old_ifs
  done
  set +f
  export DOEFF_WORKER_CGROUP_DIR="$cgroup_dir"
  echo "boot: cgroup の層を $cgroup_dir に作った(${WORKER_CGROUP_LAYOUT}・委ねを ${waited} 秒待った)" >&2
}

# --- keeper(頭の註の「版の file と keeper」)。関数の中の変数は k_ で始める(sh の変数は関数の外と分け合う)。 -------------------------

# keeper の log の 1 行(container の log — この script の stderr)。
keeper_log() {
  echo "keeper: $*" >&2
}

# sha の頭 12 字(log の名指し)。
keeper_short() {
  printf '%.12s' "$1"
}

# pid $1 の process が生きているか。終わって回収を待つ zombie は生きていない — keeper は世代の親でない(回収は PID 1)。/proc の無い
# 機体は kill -0 だけで読む。
keeper_alive() {
  [ -n "$1" ] && kill -0 "$1" 2>/dev/null || return 1
  k_state=$(sed -n 's/^[0-9]* (.*) \(.\) .*/\1/p' "/proc/$1/stat" 2>/dev/null || true)
  [ "$k_state" != Z ]
}

# 版の file の今の版(40 字の小文字の 16 進だけを受ける — 読めない・形の違う中身は空)。
keeper_wanted() {
  k_want=$(tr -d ' \t\r\n' 2>/dev/null <"$WORKER_VERSION_FILE" || true)
  case "$k_want" in
    *[!0-9a-f]*) k_want="" ;;
  esac
  [ "${#k_want}" -eq 40 ] || k_want=""
  printf '%s' "$k_want"
}

# file $1 の書かれた刻(epoch 秒)。stat は GNU(Pod)と BSD(macOS)で綴りが違う。
keeper_mtime() {
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1"
}

# 世代の dir $1 の ready の file が ready で、READY_MAX_AGE 秒(既定 30 — 役 ready と同じ)以内に書かれたか。
keeper_ready() {
  [ -f "$1/ready" ] && [ "$(cat "$1/ready" 2>/dev/null || true)" = ready ] || return 1
  k_mtime=$(keeper_mtime "$1/ready") || return 1
  [ $(( $(date +%s) - k_mtime )) -le "${READY_MAX_AGE:-30}" ]
}

# 版 $1(root $2 — 準備済み)の世代を起こし、いちばん新しい世代にする。世代は keeper の子から外す(外側の subshell が抜けると PID 1 が
# 親になる — keeper が版の替わりで止まらず、世代の終わりの回収を keeper が持たない)。
keeper_start() {
  k_key="${POD_UID:-local}.$(boot_ms)"
  k_gen=$keeper_dir/gens/$k_key
  mkdir -p "$k_gen"
  printf '%s\n' "$1" >"$k_gen/commit"
  (
    # 起動の内訳の刻(#3676)は世代ごとに数える — keeper の起動の刻を継がない。
    unset DOEFF_BOOT_STARTED_MS DOEFF_BOOT_EXEC_MS
    ROLE=worker WORKER_DOEFF_COMMIT=$1 DOEFF_BOOT_FROM_ROOT=1 WORKER_GENERATION_DIRS=1 DOEFF_WORKER_GENERATION=$k_key
    DOEFF_WORKER_BOOT_FILE=$k_gen/boot DOEFF_WORKER_READY_FILE=$k_gen/ready
    export ROLE WORKER_DOEFF_COMMIT DOEFF_BOOT_FROM_ROOT WORKER_GENERATION_DIRS DOEFF_WORKER_GENERATION DOEFF_WORKER_BOOT_FILE \
      DOEFF_WORKER_READY_FILE
    sh "$2/packages/doeff-cluster/deploy/boot.sh" </dev/null &
    echo $! >"$k_gen/pid"
  )
  # 役 drain(preStop)が使う版 — いちばん新しい世代の版(準備済みで、keeper の世代を知る)。
  printf '%s\n' "$1" >"$keeper_dir/commit"
  newest=$k_gen
  newest_started=$(date +%s)
  keeper_log "doeff $(keeper_short "$1") の世代 $k_key を起こした(pid $(cat "$k_gen/pid"))"
}

# 前の世代 $1(世代の dir)を drain してから止める(背景 — keeper の子から外す)。drain は世代の版の venv の drain_main で、世代つきで頼む
# (旧い Pod の preStop と同じ — 上限 DRAIN_DEADLINE)。drained か上限の後に SIGTERM を送り、KEEPER_STOP_SECONDS 秒(既定 60)で終わら
# なければ SIGKILL。
keeper_retire() {
  k_gen=$1
  k_pid=$(cat "$k_gen/pid")
  k_commit=$(cat "$k_gen/commit")
  k_hy=$WORK_DIR/boot/roots/$k_commit/.venv/bin/hy
  [ -x "$k_hy" ] || k_hy=hy
  : >"$k_gen/draining"
  keeper_log "前の世代 ${k_gen##*/}(doeff $(keeper_short "$k_commit")・pid $k_pid)を drain してから止める"
  ( (
    "$k_hy" -m doeff_cluster.worker.entry.drain_main drain --coordinator "$COORDINATOR_URL" --name "$WORKER_NAME" \
      --deadline "${DRAIN_DEADLINE:-90}" --boot-file "$k_gen/boot" || true
    kill -TERM "$k_pid" 2>/dev/null || true
    k_waited=0
    while keeper_alive "$k_pid" && [ "$k_waited" -lt "${KEEPER_STOP_SECONDS:-60}" ]; do
      sleep 1
      k_waited=$((k_waited + 1))
    done
    if keeper_alive "$k_pid"; then
      kill -KILL "$k_pid" 2>/dev/null || true
      keeper_log "前の世代 ${k_gen##*/} が SIGTERM の後 ${KEEPER_STOP_SECONDS:-60} 秒で終わらない — SIGKILL を送った"
    else
      keeper_log "前の世代 ${k_gen##*/} が退いた"
    fi
  ) </dev/null & )
}

# 版 $1 へ替える歩(tick ごとに呼ぶ — 準備は背景で 1 度だけ起こし、済んだ後の tick で世代を起こす)。準備の結末は
# $keeper_dir/prepare/<版>/ の file(root = 準備した root の path・failed = 失敗の印 — 300 秒で消して撃ち直す・refused = 世代を知らない
# 版の印・pid = 準備の process)。準備は役 prepare(この script が展開し、その版の script が準備する — 版上げの前の準備と同じ道)で、
# cgroup の層が在れば job の層(DOEFF_WORKER_JOB_CGROUP — 準備の memory を worker の本体の守りに数えない)へ移してから起こす。
keeper_swap() {
  k_prep=$keeper_dir/prepare/$1
  if [ -f "$k_prep/root" ]; then
    k_root=$(cat "$k_prep/root")
    case "$(cat "$k_root/packages/doeff-cluster/deploy/boot.sh" 2>/dev/null || true)" in
      *DOEFF_WORKER_GENERATION*) ;;
      *)
        if [ ! -f "$k_prep/refused" ]; then
          : >"$k_prep/refused"
          keeper_log "doeff $(keeper_short "$1") の起動の script は keeper の世代を知らない — 替えずに doeff $(keeper_short "$target") のまま"
        fi
        return 0 ;;
    esac
    keeper_log "版の file が doeff $(keeper_short "$target") → $(keeper_short "$1") に替わった — 新しい世代を起こし、名乗れた後に前の世代を退かせる"
    target=$1
    target_root=$k_root
    restarts=0
    keeper_start "$target" "$target_root"
  elif [ -f "$k_prep/failed" ]; then
    k_mtime=$(keeper_mtime "$k_prep/failed") || k_mtime=0
    [ $(( $(date +%s) - k_mtime )) -lt 300 ] || rm -rf "$k_prep"
  elif [ -d "$k_prep" ]; then
    # 準備中。準備の process が結末を書かずに居なくなっていたら失敗とする。
    keeper_alive "$(cat "$k_prep/pid" 2>/dev/null || true)" || [ -f "$k_prep/root" ] || : >"$k_prep/failed"
  else
    mkdir -p "$k_prep"
    keeper_log "版の file が doeff $(keeper_short "$1") を名指す — その root を準備する(走っている世代は止めない)"
    ( (
      if [ -n "${DOEFF_WORKER_CGROUP_DIR:-}" ] && [ -n "${DOEFF_WORKER_JOB_CGROUP:-}" ]; then
        # この subshell の pid(dash の $$ は親の pid)を job の層へ移す — 準備の子はこの層で起きる。
        sh -c 'echo "$PPID"' >"$DOEFF_WORKER_CGROUP_DIR/$DOEFF_WORKER_JOB_CGROUP/cgroup.procs" 2>/dev/null ||
          keeper_log "準備を job の層 $DOEFF_WORKER_JOB_CGROUP へ移せない — keeper の層のまま準備する"
      fi
      unset DOEFF_BOOT_FROM_ROOT
      ROLE=prepare WORKER_DOEFF_COMMIT=$1
      export ROLE WORKER_DOEFF_COMMIT
      if k_out=$(sh "$keeper_script" </dev/null); then
        printf '%s\n' "$k_out" | tail -n 1 >"$k_prep/root.tmp" && mv -f "$k_prep/root.tmp" "$k_prep/root"
      else
        : >"$k_prep/failed"
        keeper_log "doeff $(keeper_short "$1") の root を準備できない — 300 秒後に撃ち直す(走っている世代はそのまま)"
      fi
    ) </dev/null & echo $! >"$k_prep/pid" )
  fi
}

# keeper の 1 歩: 世代の生死と ready を読み、Pod の ready を写し、いちばん新しい世代が ready なら前の世代を退かせ、落ちていれば間を
# 空けて起こし直し、版の file を読んで替える。止まり(印 leaving)の後は版を替えず、起こし直さない。
keeper_tick() {
  k_any_ready=""
  for k_gen in "$keeper_dir"/gens/*; do
    [ -d "$k_gen" ] || continue
    if ! keeper_alive "$(cat "$k_gen/pid" 2>/dev/null || true)"; then
      # 終わった前の世代の印を外す(状態の dir $WORK_DIR/gen/<世代> は、次の世代の起動の script が錠の空きを見て消す)。
      [ "$k_gen" = "$newest" ] || rm -rf "$k_gen"
      continue
    fi
    if keeper_ready "$k_gen"; then
      k_any_ready=1
    fi
  done
  if [ -n "$k_any_ready" ]; then
    printf 'ready' >"$pod_ready.tmp" && mv -f "$pod_ready.tmp" "$pod_ready"
  fi
  if keeper_alive "$(cat "$newest/pid" 2>/dev/null || true)"; then
    if keeper_ready "$newest"; then
      restarts=0
      if [ -f "$newest/boot" ] && [ "$(cat "$newest/boot")" != "$(cat "$pod_boot" 2>/dev/null || true)" ]; then
        cp "$newest/boot" "$pod_boot.tmp" && mv -f "$pod_boot.tmp" "$pod_boot"
      fi
      for k_gen in "$keeper_dir"/gens/*; do
        [ -d "$k_gen" ] && [ "$k_gen" != "$newest" ] && [ ! -f "$k_gen/draining" ] || continue
        keeper_alive "$(cat "$k_gen/pid" 2>/dev/null || true)" || continue
        keeper_retire "$k_gen"
      done
    fi
  elif [ ! -f "$keeper_dir/leaving" ]; then
    k_wait=$((30 << restarts))
    [ "$k_wait" -le 300 ] || k_wait=300
    if [ $(( $(date +%s) - newest_started )) -ge "$k_wait" ]; then
      keeper_log "いちばん新しい世代 ${newest##*/}(doeff $(keeper_short "$target"))が終わった — 同じ版で起こし直す"
      rm -rf "$newest"
      [ "$restarts" -ge 4 ] || restarts=$((restarts + 1))
      keeper_start "$target" "$target_root"
    fi
  fi
  [ ! -f "$keeper_dir/leaving" ] || return 0
  k_wanted=$(keeper_wanted)
  if [ -z "$k_wanted" ]; then
    if [ -z "$unreadable" ]; then
      unreadable=1
      keeper_log "版の file $WORKER_VERSION_FILE を読めない(40 字の sha でない)— 今の版 doeff $(keeper_short "$target") のまま"
    fi
  else
    unreadable=""
    [ "$k_wanted" = "$target" ] || keeper_swap "$k_wanted"
  fi
}

# SIGTERM・SIGINT(container の止め): 印 leaving を置き、生きている全部の世代へ SIGTERM を渡し、全部が終わるのを待って抜ける(kubelet は
# 猶予の後に container ごと SIGKILL する)。
keeper_stop() {
  trap - TERM INT
  : >"$keeper_dir/leaving"
  keeper_log "止めの合図を受けた — 生きている世代へ SIGTERM を渡し、終わるのを待つ"
  for k_gen in "$keeper_dir"/gens/*; do
    k_pid=$(cat "$k_gen/pid" 2>/dev/null || true)
    if keeper_alive "$k_pid"; then
      kill -TERM "$k_pid" 2>/dev/null || true
    fi
  done
  while :; do
    k_left=""
    for k_gen in "$keeper_dir"/gens/*; do
      k_pid=$(cat "$k_gen/pid" 2>/dev/null || true)
      if keeper_alive "$k_pid"; then
        k_left="$k_left $k_pid"
      fi
    done
    [ -n "$k_left" ] || break
    sleep 1
  done
  keeper_log "全部の世代が終わった — 抜ける"
  exit 0
}

# keeper として回る(戻らない)。自分の版(この script の root — 呼び手が版の file から WORKER_DOEFF_COMMIT に入れた版)の世代を起こし、
# KEEPER_TICK_SECONDS 秒(既定 2)ごとに 1 歩を回す。
# ポーリングの訳: 読む物はどれも替わりを知らせる道を持たない — 版の file は kubelet が ConfigMap の dir の symlink を入れ替えて届け
# (image に inotify を待つ道具が無く、sh は file の替わりを待てない)、世代の ready の file は世代が heartbeat の返事ごとに書き直すだけ、
# 世代の終わりは keeper の子でないので wait で受けられない(子にすると keeper が版の替わりで退けない)。1 歩は file を数個読むだけ。
keeper_run() {
  if [ -z "${WORKER_DOEFF_COMMIT:-}" ]; then
    echo "boot: WORKER_VERSION_FILE を渡す worker は WORKER_DOEFF_COMMIT(起動の版 — 呼び手が版の file から入れる)が要る" >&2
    exit 2
  fi
  keeper_dir=${DOEFF_KEEPER_DIR:-/tmp/doeff-worker-keeper}
  keeper_script=$0
  pod_ready=${DOEFF_WORKER_READY_FILE:-/tmp/doeff-worker-ready}
  pod_boot=${DOEFF_WORKER_BOOT_FILE:-/tmp/doeff-worker-boot}
  mkdir -p "$keeper_dir/gens" "$keeper_dir/prepare"
  rm -f "$keeper_dir/leaving"
  target=$sha
  target_root=$root
  restarts=0
  unreadable=""
  trap keeper_stop TERM INT
  keeper_log "版の file $WORKER_VERSION_FILE を読む keeper として起きた(doeff $(keeper_short "$sha"))"
  keeper_start "$target" "$target_root"
  while :; do
    # 待ちは背景の sleep を wait で待つ — 止めの合図を待ちの途中で受ける(前面の sleep は終わるまで trap を走らせない)。
    sleep "${KEEPER_TICK_SECONDS:-2}" &
    wait $! || true
    keeper_tick
  done
}

# ready・access・cgroup は hy を起こさないので root を要らない。
if [ -n "${WORKER_DOEFF_COMMIT:-}" ] && [ "$role" != ready ] && [ "$role" != access ] && [ "$role" != cgroup ]; then
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
  cgroup)
    # cgroup の層だけを作り、書ける dir を出す(頭の註の「cgroup の層」)— 検と、手元で層を確かめるため。
    [ -n "${WORKER_CGROUP_LAYOUT:-}" ] || cgroup_refused "ROLE=cgroup には WORKER_CGROUP_LAYOUT が要る"
    cgroup_layout
    echo "$cgroup_dir"
    exit 0 ;;
  ready)
    # hy を起こさず、この Pod の worker が heartbeat の返事ごとに書く file を読むだけ。Ready = file が在り、中身が ready
    # (worker は drain 中の返事でも ready を書く・#4177)で、READY_MAX_AGE 秒(既定 30)以内に書かれた。file は container の /tmp なので
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
    # keeper の在る container(頭の註の「版の file と keeper」)は、生きている全部の世代を世代つきで並べて drain し、全部の結末を待つ。
    # 先に印 leaving を置く — keeper はその後に版を替えず、落ちた世代を起こし直さない。
    if [ -n "${WORKER_VERSION_FILE:-}" ] && [ -d "${DOEFF_KEEPER_DIR:-/tmp/doeff-worker-keeper}/gens" ]; then
      keeper_dir=${DOEFF_KEEPER_DIR:-/tmp/doeff-worker-keeper}
      : >"$keeper_dir/leaving"
      for g in "$keeper_dir"/gens/*; do
        [ -f "$g/boot" ] && keeper_alive "$(cat "$g/pid" 2>/dev/null || true)" || continue
        hy -m doeff_cluster.worker.entry.drain_main drain --coordinator "$COORDINATOR_URL" --name "$WORKER_NAME" \
          --deadline "${DRAIN_DEADLINE:-90}" --boot-file "$g/boot" 2>>/proc/1/fd/2 &
      done
      wait
      exit 0
    fi
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
# WORKER_GENERATION_DIRS=1(同じ機体に同じ名の worker の 2 つの世代を並べる Deployment の RollingUpdate — AL-D2): 新しい Pod は旧い Pod の
# 終わりを待たずに起き、worker の状態(版の木・job の cwd・task の結果・温める子・準備の要求・入口の検め)を世代ごとの
# dir $WORK_DIR/gen/<Pod の uid>/state に置く。log・実行環境の root・Python・Rust の wheel・mirror とその錠は $WORK_DIR/state を分け合う
# (worker の --shared-dir — 重い cache と root を世代ごとに作り直さない・AL-D2g)。世代の dir の錠(fd 9)は exec した worker が終わるまで持ち、起きる時に、錠を誰も
# 持たない(worker が終わった)前の世代の dir を消す。まだ走る前の世代(入れ替えの重なり)の dir は、脇の子がその錠を待って、前の世代の
# worker が終わった時に消す(前の世代の root を次の入れ替えまで残さない)。POD_UID は downward API の metadata.uid。
# cgroup の層(頭の註)は、下の脇の子を起こす前に作る — 起動の後に居る process は全部どれかの層に居る。keeper が起こした世代は、keeper が
# 作った層の中で起き、書ける dir を DOEFF_WORKER_CGROUP_DIR で受けるので作り直さない。
if [ -n "${WORKER_CGROUP_LAYOUT:-}" ] && [ -z "${DOEFF_WORKER_CGROUP_DIR:-}" ]; then
  cgroup_layout
fi
# 版の file を受ける worker は keeper として回る(頭の註の「版の file と keeper」— 戻らない)。keeper が起こした世代
# (DOEFF_WORKER_GENERATION を受けた process)は下の worker の起動へ進む。
if [ -n "${WORKER_VERSION_FILE:-}" ] && [ -z "${DOEFF_WORKER_GENERATION:-}" ]; then
  keeper_run
fi
state_dir=$WORK_DIR/state
# 前の世代の worker がまだ走っている(その dir の錠が持たれている)なら、worker に --overlaps-elder を渡す — coordinator は新しい世代の
# 名乗りを前の世代の終わりの印と数えず、前の世代の process の lease を外さない(AL-D2e)。
overlaps_elder=
if [ "${WORKER_GENERATION_DIRS:-}" = 1 ]; then
  # 世代の名: keeper が起こした世代は keeper の振った名(DOEFF_WORKER_GENERATION — 同じ container に並ぶ世代ごとに違う)、それ以外は Pod の uid。
  generation=${DOEFF_WORKER_GENERATION:-${POD_UID:-}}
  if [ -z "$generation" ]; then
    echo "boot: WORKER_GENERATION_DIRS=1 には POD_UID(downward API の metadata.uid)か DOEFF_WORKER_GENERATION が要る" >&2
    exit 2
  fi
  gen_root=$WORK_DIR/gen
  mkdir -p "$gen_root/$generation"
  exec 9>"$gen_root/$generation/worker.lock"
  flock 9
  for gen_dir in "$gen_root"/*; do
    [ -d "$gen_dir" ] && [ "$gen_dir" != "$gen_root/$generation" ] || continue
    if flock -n "$gen_dir/worker.lock" true 2>/dev/null; then
      rm -rf "$gen_dir"
      echo "boot: worker の終わった前の世代の dir $gen_dir を消した" >&2
    else
      echo "boot: 前の世代の dir $gen_dir はその worker が終わった後に消す" >&2
      overlaps_elder=--overlaps-elder
      (flock "$gen_dir/worker.lock" rm -rf "$gen_dir" 9>&- && echo "boot: worker の終わった前の世代の dir $gen_dir を消した" >&2) 9>&- &
    fi
  done
  # 世代の dir を使う前の worker(WORKER_DIR_LOCK — $WORK_DIR/worker.lock を持ち、$WORK_DIR/state を状態の dir にした)が置いた世代ごとの物
  # (worker の入口が --state-dir から作る物)は、分け合う dir に残る。その worker が終わって錠が空いた後に消す(初めて世代の dir で起きた
  # 時の入れ替えでは、前の Pod がまだ走っている)。実行環境の root($WORK_DIR/state/roots)は世代をまたいで分け合う置き場なので消さない
  # (AL-D2g — 前の worker の root もそのまま使う)。
  if [ -e "$WORK_DIR/state/jobs" ]; then
    echo "boot: 前の worker の状態($WORK_DIR/state の jobs ほか)はその worker が終わった後に消す" >&2
    if ! flock -n "$WORK_DIR/worker.lock" true 2>/dev/null; then
      overlaps_elder=--overlaps-elder
    fi
    (
      flock "$WORK_DIR/worker.lock" true
      for name in code jobs programs warm env-requests probe tasks status.json; do
        rm -rf "${WORK_DIR:?}/state/$name"
      done
      echo "boot: 前の worker の状態($WORK_DIR/state の jobs ほか)を消した" >&2
    ) 9>&- &
  fi
  state_dir=$gen_root/$generation/state
  echo "boot: 世代の dir $state_dir(分け合う dir $WORK_DIR/state)${overlaps_elder:+ — 前の世代がまだ走っている}" >&2
elif [ "${WORKER_DIR_LOCK:-}" = 1 ]; then
  exec 9>"$WORK_DIR/worker.lock"
  echo "boot: $WORK_DIR/worker.lock を待つ $(date +%T)" >&2
  flock 9
  echo "boot: $WORK_DIR/worker.lock を取った $(date +%T)" >&2
fi
repo_keys=""
# 鍵の表を file で受ける worker は、準備の前ごとにこの script を ROLE=access で撃ち、表を組み直す(起動し直さずに表の変更を読む・
# card ki-2de748a0eac5)。env だけで受ける worker は起動の時の表のまま(空 = 組み直さない)。
repo_access_script=""
if [ -n "${WORKER_REPOS_FILE:-}" ]; then
  repo_access_script=$0
fi
if [ -n "${WORKER_REPOS:-}" ] || [ -n "${WORKER_REPOS_FILE:-}" ]; then
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
# 枠(card ki-6828eda880ce): WORKER_ROOM_FILE(worker の名 → capacity・taskReserve の JSON — k8s では名の固定した ConfigMap worker-room を
# volume で置いた file)が在れば、worker は起動の時と heartbeat の前にその file の自分の行を読み直して名乗る(枠を変えても Pod を作り
# 直さない)。WORKER_CAPACITY・WORKER_TASK_RESERVE は file を読めない起動の時だけ使う。file の無い worker(機体の上の worker)は
# task のために空けておく数(WORKER_TASK_RESERVE)を必ず渡す — 既定の値で埋めない(範囲 0 以上 capacity 以下は worker の入口が検める)。
if [ -z "${WORKER_ROOM_FILE:-}" ] && [ -z "${WORKER_TASK_RESERVE:-}" ]; then
  echo "boot: WORKER_ROOM_FILE も WORKER_TASK_RESERVE も無い — 枠の file か、capacity のうち task のために空けておく数(0 以上 WORKER_CAPACITY 以下)を渡す" >&2
  exit 2
fi
set --
[ -z "${WORKER_ROOM_FILE:-}" ] || set -- "$@" --room-file "$WORKER_ROOM_FILE"
[ -z "${WORKER_CAPACITY:-}" ] || set -- "$@" --capacity "$WORKER_CAPACITY"
[ -z "${WORKER_TASK_RESERVE:-}" ] || set -- "$@" --task-reserve "$WORKER_TASK_RESERVE"
# 実行環境の root の置き場の 2 つの量(#3732 — 掃除の下限を disk 全体の割合から絶対の量へ。既定の値はここの 1 か所で、worker の
# 入口は必ずの引数として受ける)。台ごとに変える時は Deployment の env に GiB の整数で書く:
#   WORKER_ENV_ROOTS_GIB    roots の合計の上限(既定 20)— 越えた時は、固定(走っている job・宣言の job・準備中・温める表)でも
#                           project ごとの新しい 2 つ(今の版と戻し先の版)でもない root を、最後に使った古い順に消す。合計は root ごとの
#                           大きさの和で、root どうしが hardlink で共有する木と .pyc を重ねて数える(実の使用量より大きく出る)。
#                           2026-10-06 の実測で root 1 つは重ねて数えて 0.19〜0.25 GB(zeus の service worker の業務の root)— 専用の
#                           service worker(/work が共有の USB の SSD の hostPath)は 1(root 4 本分)で足りる。
#   WORKER_ENV_MIN_FREE_GIB 共有の disk の空きの最低(既定 25)— 割った時も同じ候補の root を古い順に、空きが最低へ戻るまで消す
#                           (#4051)。戻るまでは準備を disk-full で断り、heartbeat で exhausted を名乗る(coordinator は準備済みでない
#                           env の task を置かない)。
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
  --provides "${WORKER_PROVIDES:-}" --exclusive "${WORKER_EXCLUSIVE:-}" --node "${NODE_NAME:-}" "$@" \
  --repo "$repo" --state-dir "$state_dir" --shared-dir "$WORK_DIR/state" ${overlaps_elder} --stop-grace 10 \
  --import-roots "${CODE_IMPORT_ROOTS:-.}" \
  --repo-keys "$repo_keys" --repo-access "$repo_access_script" --tools "$tools" --pass-env "${WORKER_PASS_ENV:-}" \
  --env-roots-cap "$((env_roots_gib * 1073741824))" --env-min-free "$((env_min_free_gib * 1073741824))" \
  --code-store "$DOEFF_HY_CODE_STORE" --uv-cache "$DOEFF_UV_CACHE_DIR"
