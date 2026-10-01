;; worker の実 I/O。coordinator との連絡・コードの展開(git archive)・子 process・状態の file・停止信号。
;; どれもループを塞がない: 展開と子 process は Popen で起動し、結果は ObserveWorld で観測する。
(require doeff-hy.macros [defhandler defk deff <- val])
(require doeff-hy.record [defrecord])
(import json os re shutil signal subprocess sys tempfile threading time uuid)
(import httpx)
(import enum [Enum])
(import typing [IO])
(import dataclasses [dataclass replace])
(import pathlib [Path])
(import urllib.parse [quote :as url-quote])
(import doeff_cluster.foundation.coordinator_http [CoordinatorEndpoint REPLY-SECONDS])
(import .beat_policy [WatchKind WatchReading beat-interval-ms heartbeat-due watch-params watch-reading reply-revision
                      WATCH-RETRY-SECONDS WAKE-HOLD-SECONDS])
(import .code_prepare [MARKER MARKER-FORMAT marker-problem scan])
(import doeff [run])
(import doeff_core_effects.file_effects [MakeDirectory WriteText file-done])
(import doeff_cluster.shared.intent.protocol [PROTOCOL-FORMAT])
(import doeff_cluster.shared.core.capabilities [environ-pairs])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import .job_context [process-context-environ])
(import doeff_cluster.shared.intent.remote_model [program-sha])
(import doeff_cluster.shared.intent.runtime_env_model [runtime-env-of-json env-key current-platform EnvFailure EnvFailureKind])
(import .env_prepare [ENV-MARKER])
(import .env_upkeep [RootInfo PrepareLimits sweep-choice prepare-overdue env-capacity SWEEP-FLOOR-RATIO WHEEL-UNUSED-SECONDS])
(import doeff_cluster.shared.intent.semaphore_model [SEMAPHORE-PREFIX])
(import doeff_cluster.shared.core.lease_rules [drop-holders lease-holder holder-tokens-prefix])
(import .worker_policy [kept-when-cut-off])
(import .worker_model [JobSpec CodeState CodeView ProcessView WorldView StopStage ProbeState ProbeView
  DesiredJobs DesiredUnreadable ReadDesired ObserveWorld WorkerStopRequested PublishStatus JobPhase JobStatus EnvDisk WarmEnv
  PrepareCode PrepareEnv SweepEnvs ForgetProbes StartJob SignalJob ReapJob RetireJob ReleaseLeases ProbeEntry spec-hash probe-args probe-refusal CodeLayout
  ENV-KEY-PREFIX])

(defn #^ tuple env-placement [#^ (| dict None) declared #^ str revision]  ; defk にできない: 宣言の読み(Program の外の I/O の道具)が呼ぶ
  "job の宣言の runtimeEnv(在れば)と版 → #(版 宣言の JSON の正規化した文字列 env のキー)。実行環境の job(task も service も —
   2026-09-26)は、env のキー(この worker の platform で計算)を root の置き場の鍵にする。版は宣言のまま運ぶ — coordinator が同じ
   宣言から計算する版と指紋に合わせるため(版を持たない task だけは \"env-<キー>\" を版の代わりにする)。無ければ版のまま。"
  (if (is declared None)
      #(revision None None)
      (do (setv key (run (env-key (run (runtime-env-of-json declared)) (current-platform))))
          #((or revision (+ ENV-KEY-PREFIX key)) (json.dumps declared :sort-keys True :ensure-ascii False) key))))


(defn #^ JobSpec declared-job-spec [#^ dict job]  ; defk にできない: 宣言の読み(Program の外の I/O の道具)が呼ぶ
  "heartbeat の返事の job 1 本 → worker が起動する形(runtimeEnv を持つ service は env の root で起こす)。worker が job を受けるのは
   coordinator からだけ(宣言の file を直に読む口は無い — ADR-DOE-CLUSTER-001 R1)。"
  (setv #(revision runtime key) (env-placement (.get job "runtimeEnv") (get job "revision")))
  (JobSpec (get job "name") (get job "entry") (tuple (.get job "args" [])) revision
           :once (.get job "once" False) :placement (.get job "placement")
           :handoff (bool (.get job "handoff" False))
           :ready-instance (.get job "readyInstance") :runtime-env runtime :env-key key
           ;; 入れ替えの諦め(coordinator の期限 — 返事の handoff の job だけが持つ・無ければ偽)。
           :handoff-abandoned (bool (.get job "handoffAbandoned" False))
           ;; Program の job(改訂 1 の F・G): 詰めた Program の置き場のキーと、子の環境変数。
           :program (.get job "program")
           :environ (environ-pairs (.get job "environ" {}))))


(deff program-file [#^ Path program-dir #^ str sha]  ; defk にできない: worker の I/O の道具(CoordinatorLink・ProcessHost)が呼ぶ純粋な読み
  {:pre [(: program-dir Path) (: sha str)] :post [(: % Path)] :tags {:context "doeff-cluster" :role "judgment"}}
  "詰めた Program の置き場のキー → この worker の cache の file(CoordinatorLink が取って書き、ProcessHost が子へ渡す — 定義点は 1 つ)。"
  (/ program-dir (+ sha ".json")))


(deff write-program-file [#^ Path program-dir #^ str sha #^ str blob #^ dict versions]  ; defk にできない: worker の I/O の道具(CoordinatorLink)が呼ぶ
  {:pre [(: program-dir Path) (: sha str) (: blob str) (: versions dict)] :post [(: % Path)] :tags {:context "doeff-cluster" :role "foundation"}}
  "coordinator の /programs/<sha> から取った詰めた Program を、子の入口(job_entry の read-program)が読む形の cache の file に書くため。
   形の定義点はここ 1 つ({\"blob\" \"versions\"} — service と task で同じ)。書きかけを子に見せないよう rename で置く。"
  (let [path (program-file program-dir sha)
        tmp (Path (+ (str path) ".tmp"))]
    (.mkdir program-dir :parents True :exist-ok True)
    (.write-text tmp (json.dumps {"blob" blob "versions" versions}) :encoding "utf-8")
    (os.replace tmp path)
    path))

(setv TOOL (str (/ (. (.resolve (Path __file__)) parent) "code_prepare.hy")))


(defclass CodeStore []
  "revision ごとに repo のコードを cache へ展開する。展開済みの dir は再利用する。"
  "hy-command = 焼きに使う hy(None なら bytecode の準備を省く)。"
  "完成品 = cache の直下の、完成の印(code_prepare の MARKER)が検めを通る dir。印の無い・検めの通らない dir は"
  "完成品として公開せず、次にその版を求められた時に脇へ退けて作り直す。"
  "tool = 焼く道具の file(既定は worker 自身のコードの code_prepare.hy。準備する版の木の物は使わない)。"
  "layout = 業務の repo の木の形(import の根 — worker_model.CodeLayout)。"
  (defn #^ None __init__ [self #^ str repo #^ str cache #^ (| str None) hy-command #^ str [tool TOOL] #^ CodeLayout [layout (CodeLayout)]]
    (setv self.repo repo self.cache (Path cache) self.hy-command hy-command self.tool tool self.layout layout
          self.pending {} self.failed {} self.timings {}
          ;; 読む時の検めの答え(dir の名前 → #(inode mtime_ns 理由 or None))。木は rename で現れて以後変えないので、
          ;; 同じ inode と mtime の間は答えを使い回す。
          self.checked {}))

  (defn #^ Path final-dir [self #^ str revision] (/ self.cache revision))

  (defn #^ (| str None) problem [self #^ Path entry]
    "cache の直下の dir 1 つの検め。完成品なら None、そうでなければ理由。"
    (setv st (.stat entry) key #(st.st-ino st.st-mtime-ns) seen (.get self.checked entry.name))
    (when (and seen (= (cut seen 0 2) key)) (return (get seen 2)))
    (setv marker (/ entry MARKER)
          text (if (.exists marker) (.read-text marker :encoding "utf-8") None)
          want-bytecode (is-not self.hy-command None)
          pycs (if (and want-bytecode (is-not text None)) (len (get (run (scan (str entry))) 1)) 0)
          reason (marker-problem text entry.name want-bytecode pycs))
    (setv (get self.checked entry.name) #(#* key reason))
    reason)

  (defn #^ list ready-dirs [self]
    (when (not (.exists self.cache)) (return []))
    (lfor e (.iterdir self.cache)
          :if (and (.is-dir e) (not (.startswith e.name ".")) (is (self.problem e) None))
          e))

  (defn #^ (| Path None) latest-ready [self]
    ;; 引き継ぎ元 = 最後に完成した版の木(完成品は rename で現れるので mtime が完成の時刻)。
    (setv ready (self.ready-dirs))
    (if ready (max ready :key (fn [e] (. (.stat e) st-mtime))) None))

  (defn #^ None start [self #^ str revision]
    (when (in revision self.pending) (return))
    (setv final (self.final-dir revision) broken None)
    (when (.exists final)
      (setv reason (self.problem final))
      (when (is reason None) (return))
      ;; 完成品に見えて検めの通らない木(印の無い古い形・焼きの失敗が完成品になった木)は脇へ退けて作り直す。
      ;; 名前は . で始まるので、消し終わるまでの間も完成品としては読まれない。
      (setv broken (/ self.cache f".{revision}.broken.{(time.time-ns)}"))
      (os.rename final broken)
      (.pop self.checked revision None)
      (print f"worker: 版 {revision} の木を作り直します({reason})" :file sys.stderr :flush True))
    (.pop self.failed revision None)
    (.mkdir self.cache :parents True :exist-ok True)
    (setv tmp (/ self.cache f".{revision}.tmp")
          previous (self.latest-ready))
    (setv (get self.pending revision)
      #((subprocess.Popen ["sh" "-c" (self.script revision previous)]
          :env (| (dict os.environ) {"T" (str tmp) "F" (str final) "B" (if broken (str broken) "")
                                     "PYTHONPATH" (.pythonpath self.layout (str tmp))})
          :stdout subprocess.DEVNULL :stderr subprocess.PIPE)
        (time.monotonic))))

  (defn #^ str script [self #^ str revision #^ (| Path None) previous]
    ;; 展開 → bytecode の準備(木の中だけ・実行時に検める方式・前の版から引き継ぐ・検めて完成の印を置く)→ rename。
    ;; どの命令も単独の文にして set -e を効かせる(`a && b` の a の失敗は set -e が拾わない — 以前はそれで
    ;; 焼きの失敗が完成品になった)。git archive は pipe にせず file へ書く(pipe の失敗は最後の tar しか見えない)。
    ;; rename が最後で、その前に印が在ることを確かめるので、final の在る dir は常に完成品。
    ;; 焼く道具そのもの(Hy)の import が timestamp 方式の .pyc を木へ書かないよう、PYTHONDONTWRITEBYTECODE を立てる。
    ;; revision = worker_model.code-key = 1 つの commit(木の全体がその commit — 以前の「<base>~<重ねる commit>」の重ねる木は
    ;; 2026-09-28 に消した)。前の木から引き継ぐ時の「変わった file」は前の木の名(版)と revision の git diff。前の木の版を
    ;; この repo で解けない時(以前の重ねる木の名・履歴から消えた commit)は引き継がずに全部を焼く — 引き継ぎは速さのためだけで、
    ;; 引き継げないことを準備の失敗にしない(set -e は if の条件の失敗を拾わない)。
    (setv repo self.repo
          tool (+ f"PYTHONDONTWRITEBYTECODE=1 \"{self.hy-command}\" \"{self.tool}\" \"$T\" --revision \"{revision}\""
                  f" --import-roots \"{(.roots-arg self.layout)}\"")
          prepare (cond
            (not self.hy-command)
              (+ f"printf '{{\"format\": {MARKER-FORMAT}, \"revision\": \"%s\", \"bytecode\": false}}\\n' "
                 f"\"{revision}\" > \"$T/{MARKER}\"\n")
            (is previous None) f"{tool}\n"
            True
              (+ f"if git -C \"{repo}\" diff --name-only \"{previous.name}\" \"{revision}\" > \"$T.changed\" 2>/dev/null; then\n"
                 f"  {tool} --from \"{previous}\" --changed \"$T.changed\"\n"
                 "else\n"
                 f"  echo \"前の木 {previous.name} の版をこの repo で解けない — 引き継がずに全部を焼く\" >&2\n"
                 f"  {tool}\n"
                 "fi\n")))
    (+ "set -eu\n"
       "if [ -n \"$B\" ]; then rm -rf \"$B\"; fi\n"
       "rm -rf \"$T\" \"$T.tar\" \"$T.changed\"\n"
       "mkdir -p \"$T\"\n"
       ;; 手元に無い版なら先に fetch する(Pod の mirror は起動時の版しか持たない)。
       f"if ! git -C \"{repo}\" cat-file -e \"{revision}^{{commit}}\" 2>/dev/null; then\n"
       f"  git -C \"{repo}\" fetch -q origin '+refs/heads/*:refs/heads/*'\n"
       "fi\n"
       f"git -C \"{repo}\" archive --format=tar -o \"$T.tar\" \"{revision}\"\n"
       "tar -x -C \"$T\" -f \"$T.tar\"\n"
       "rm -f \"$T.tar\"\n"
       "cd \"$T\"\n"
       prepare
       f"test -f \"$T/{MARKER}\" || {{ echo \"完成の印が置かれていない\" >&2; exit 1; }}\n"
       "rm -f \"$T.changed\"\n"
       "mv \"$T\" \"$F\"\n"))

  (defn #^ tuple observe [self]
    (setv views [] now-ms (int (* (time.time) 1000)))
    (for [#(revision #(process started)) (list (.items self.pending))]
      (setv code (.poll process))
      (when (is-not code None)
        (del (get self.pending revision))
        (setv (get self.timings revision) (- (time.monotonic) started))
        (when (!= code 0)
          (setv detail (.strip (.decode (.read process.stderr) "utf-8" "replace")))
          (setv (get self.failed revision)
                #((+ f"準備に失敗(終了 {code}): " (cut detail -480 None)) now-ms)))))
    (for [revision self.pending]
      (.append views (CodeView revision CodeState.PREPARING)))
    (for [#(revision #(detail failed-ms)) (.items self.failed)]
      (.append views (CodeView revision CodeState.FAILED :detail detail :failed-ms failed-ms)))
    (for [entry (self.ready-dirs)]
      (when (and (not-in entry.name self.pending) (not-in entry.name self.failed))
        (.append views (CodeView entry.name CodeState.READY :path (str entry)))))
    (tuple views)))

(setv ENV-TOOL "doeff_cluster.env_handlers")   ; 準備の process の入口(worker 自身の環境の module — root の路は worker に足さない)


(setv ROOT-NAME-PATTERN (re.compile r"[0-9a-f]{24}"))
(setv SWEEP-EVERY-SECONDS 30)   ; 空きが下限を切っている間の掃除の間隔(固定の集合が変わった時はすぐ)
(setv PRUNE-EVERY-SECONDS 1800)  ; uv の cache の prune を起こし直す間隔の下限(node の disk を他の物が使うと掃除では下限に戻らず、拍ごとに起き続けるため)


(defclass PendingPrepare []
  "走っている準備 1 本の記録(EnvStore の中だけ): process・始めた時刻(epoch 秒)・答えの file・進みの印の file・
   warm = 先読みの準備か(job がその root を求めたら job の準備へ上げる)・cold = 冷たい準備か(引き継げる root が無い)。"
  (defn #^ None __init__ [self #^ subprocess.Popen process #^ float started #^ Path result #^ Path progress #^ bool warm #^ bool cold]
    (setv self.process process self.started started self.result result self.progress progress self.warm warm self.cold cold)))


(defclass EnvStore []
  "実行環境(runtime env)の root を env のキーごとに準備する(2026-09-26)。準備は worker 自身の code の env_handlers.hy を別の
   process として起こし(worker のループは待たない)、完成マーカーの在る root だけを READY として観測する。
   root は state/roots/<キー> の最終の path に作る(venv が絶対 path を持つので rename しない)。マーカーの無い root は次に
   求められた時に脇へ退けて作り直す。準備は同時に max-parallel 本まで・同じキーは 1 本。
   repo-keys = 許可表の JSON の file(clone してよい URL → deploy key)・uv = uv の命令・min-free-bytes = 準備を始める空きの下限。
   先読み(warm・2026-09-26): 温める表の root は job の準備より後に起こし、同時の枠の 1 つを job に残す。期限は limits
   (env_upkeep.prepare-overdue — 先読みは停滞だけ・job は冷たい / 温い)。
   掃除(sweep): 空きが下限(sweep-floor-bytes か volume の SWEEP-FLOOR-RATIO と min-free-bytes の大きい方)を切ったら、固定されていない
   root を消す(選びは env_upkeep.sweep-choice)・uv の cache を prune・7 日使われない wheel を消す。消すのは worker が作った dir だけ。"
  (defn #^ None __init__ [self #^ str state-dir #^ str hy-command #^ str [repo-keys ""] #^ str [uv "uv"] #^ int [min-free-bytes 0]
                  #^ PrepareLimits [limits (PrepareLimits)] #^ int [max-parallel 2] #^ str [tool ENV-TOOL]
                  #^ str [code-prepare TOOL] #^ (| int None) [sweep-floor-bytes None]]
    (setv self.state (Path state-dir) self.hy-command hy-command self.repo-keys repo-keys self.uv uv
          self.min-free-bytes min-free-bytes self.limits limits self.max-parallel max-parallel
          self.tool tool self.code-prepare code-prepare self.sweep-floor-bytes sweep-floor-bytes
          self.pending {} self.failed {} self.waiting {} self.started 0
          self.pinned (frozenset) self.swept-at 0.0 self.views None
          ;; 走っている uv の cache の prune(待たない — 下の sweep)。
          self.pruning None self.pruned-at 0.0))

  (defn #^ Path root-of [self #^ str key]
    (/ self.state "roots" (cut key (len ENV-KEY-PREFIX) None)))

  (defn #^ (| dict None) marker [self #^ Path root]
    "root の完成マーカーの中身(無い・読めなければ None)。"
    (setv path (/ root ENV-MARKER))
    (when (not (.is-file path)) (return None))
    (try (json.loads (.read-text path :encoding "utf-8")) (except [ValueError] None)))

  (defn #^ list known [self]
    "完成した root の列(展開の複製と bytecode の引き継ぎの元)— 要求の JSON の形。"
    (setv roots (/ self.state "roots"))
    (when (not (.is-dir roots)) (return []))
    (lfor e (sorted (.iterdir roots)) :if (and (.is-dir e) (not (.startswith e.name ".")))
          :setv m (self.marker e) :if (is-not m None)
          {"env" (get m "env") "root" (str e)}))

  (defn #^ None start [self #^ str key #^ str runtime-env #^ bool [warm False]]
    "root の準備を頼む。job の頼み(warm = False)は、同じ root の先読みが走っていれば job の準備へ上げ、待っていれば前へ出す。"
    (setv pending (.get self.pending key))
    (when (is-not pending None)
      (when (and pending.warm (not warm))
        ;; 先読みの準備を job の準備へ上げる: 期限は job の物(始めた時刻から数える)になる。
        (setv pending.warm False))
      (return))
    (when (in key self.waiting)
      (when (not warm) (setv (get self.waiting key) #(runtime-env False)))
      (return))
    (setv root (self.root-of key))
    (when (is-not (self.marker root) None) (return))
    (.pop self.failed key None)
    (setv (get self.waiting key) #(runtime-env warm))
    (self.launch-waiting))

  (defn #^ bool cold-for [self #^ dict declared]
    "準備が冷たいか(同じ lock と Python の完成した root が無い = 依存も bytecode も引き継げない)。job の準備の期限を分けるため。"
    (setv project (get declared "project"))
    (not (any (gfor k (self.known)
                    (and (= (get (get k "env") "project" "lockSha256") (get project "lockSha256"))
                         (= (get (get k "env") "project" "python") (get project "python")))))))

  (defn #^ None launch-waiting [self]
    ;; 同時の準備を max-parallel 本に絞る(走っている手番の CPU を奪わない)。job の準備を先に起こし、先読みは枠の 1 つを job に残す。
    (setv order (+ (lfor #(k #(_ w)) (.items self.waiting) :if (not w) k) (lfor #(k #(_ w)) (.items self.waiting) :if w k)))
    (for [key order]
      (setv #(runtime-env warm) (get self.waiting key)
            warm-running (len (lfor p (.values self.pending) :if p.warm p)))
      (when (>= (len self.pending) self.max-parallel) (break))
      (when (and warm (>= warm-running (max 1 (- self.max-parallel 1)))) (continue))
      (del (get self.waiting key))
      (setv root (self.root-of key))
      (when (.exists root)
        ;; マーカーの無い root(途中で止まった準備)は脇へ退ける。名は . で始まるので完成品としては読まれない。
        (os.rename root (/ root.parent (.format ".{}.broken.{}" root.name (time.time-ns)))))
      (setv requests (/ self.state "env-requests"))
      (.mkdir requests :parents True :exist-ok True)
      (setv declared (json.loads runtime-env)
            request (/ requests (+ key ".json")) result (/ requests (+ key ".result.json"))
            progress (/ requests (+ key ".progress")))
      (.unlink result :missing-ok True)
      (.unlink progress :missing-ok True)
      (setv cold (.cold-for self declared))
      (.write-text request (json.dumps {"env" declared "key" (cut key (len ENV-KEY-PREFIX) None)
                                        "platform" (current-platform) "root" (str root) "known" (self.known)
                                        "minFreeBytes" self.min-free-bytes}
                                       :ensure-ascii False)
                   :encoding "utf-8")
      (+= self.started 1)
      (setv log (open (/ requests (+ key ".log")) "ab"))
      (try
        (setv process (subprocess.Popen ["nice" "-n" "10" self.hy-command "-m" self.tool "--request" (str request)
                                         "--result" (str result) "--state" (str self.state)
                                         "--repo-keys" self.repo-keys "--code-prepare" self.code-prepare "--uv" self.uv
                                         "--progress" (str progress)]
                                        :stdout log :stderr subprocess.STDOUT :stdin subprocess.DEVNULL))
        (finally (.close log)))
      (setv (get self.pending key) (PendingPrepare process (time.time) result progress warm cold))))

  (defn #^ float progressed-at [self #^ PendingPrepare pending]
    "準備の最後の進み(処理ステージの頭の印の時刻・印が無ければ始めた時刻)。"
    (try (max pending.started (. (.stat pending.progress) st-mtime)) (except [OSError] pending.started)))

  (defn #^ tuple observe [self]
    (setv views [] now-ms (int (* (time.time) 1000)))
    (for [#(key pending) (list (.items self.pending))]
      (setv code (.poll pending.process))
      (cond
        (is-not code None)
          (do (del (get self.pending key))
              (setv answer (try (json.loads (.read-text pending.result :encoding "utf-8")) (except [[OSError ValueError]] None)))
              (cond
                (and answer (in "failure" answer))
                  (do (setv f (get answer "failure"))
                      (setv (get self.failed key)
                            #((EnvFailure :kind (EnvFailureKind (get f "kind")) :detail (get f "detail")
                                          :retryable (get f "retryable"))
                              now-ms)))
                (and answer (in "ready" answer)) None
                True
                  (setv (get self.failed key)
                        #((EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :retryable False
                                      :detail (.format "準備の process が答えを書かずに終わった(終了 {})— log: {}"
                                                       code (/ self.state "env-requests" (+ key ".log"))))
                          now-ms))))
        (run (prepare-overdue pending.warm pending.cold pending.started (.progressed-at self pending) (time.time) self.limits))
          (do (.kill pending.process)
              (.wait pending.process)
              (del (get self.pending key))
              (setv (get self.failed key)
                    #((EnvFailure :kind EnvFailureKind.PREPARE-TIMEOUT :retryable True
                                  :detail (if pending.warm
                                              (.format "先読みの準備が {} 秒進まない(止めた)" self.limits.stall-seconds)
                                              (.format "準備が期限({})を過ぎた(止めた)" (if pending.cold "冷たい" "温い"))))
                      now-ms)))))
    (self.launch-waiting)
    (for [key (+ (list self.pending) (list self.waiting))]
      (.append views (CodeView key CodeState.PREPARING)))
    (for [#(key #(failure failed-ms)) (.items self.failed)]
      (.append views (CodeView key CodeState.FAILED :detail failure.detail :failed-ms failed-ms :failure failure)))
    (setv roots (/ self.state "roots"))
    (when (.is-dir roots)
      (for [entry (sorted (.iterdir roots))]
        (setv key (+ ENV-KEY-PREFIX entry.name))
        (when (and (.is-dir entry) (not (.startswith entry.name ".")) (not-in key self.pending) (not-in key self.failed)
                   (is-not (self.marker entry) None))
          (.append views (CodeView key CodeState.READY :path (str entry))))))
    (setv self.views (tuple views))
    (tuple views))

  ;; --- disk と掃除 ---------------------------------------------------------------------

  (defn #^ int floor-bytes [self #^ int total]
    "掃除を始める空きの下限。"
    (if (is-not self.sweep-floor-bytes None)
        self.sweep-floor-bytes
        (max self.min-free-bytes (int (* SWEEP-FLOOR-RATIO total)))))

  (defn #^ EnvDisk disk-view [self]
    "root の置き場の disk の観測(worker の判断が掃除の時を決める)。"
    (.mkdir self.state :parents True :exist-ok True)
    (setv usage (shutil.disk-usage self.state))
    (EnvDisk :free usage.free :floor (.floor-bytes self usage.total) :pinned self.pinned))

  (defn #^ list root-infos [self]
    "掃除の候補(roots の直下の dir)。worker が作った root = キーの形の名で完成マーカーを持つ dir。"
    (setv roots (/ self.state "roots") out [])
    (when (not (.is-dir roots)) (return out))
    (for [entry (sorted (.iterdir roots))]
      (when (and (.is-dir entry) (not (.startswith entry.name ".")))
        (setv marker (self.marker entry)
              owned (and (is-not marker None) (bool (ROOT-NAME-PATTERN.fullmatch entry.name)))
              made (if owned (. (.stat (/ entry ENV-MARKER)) st-mtime) 0.0)
              used-file (/ entry ".last-used")
              used (if (.exists used-file) (. (.stat used-file) st-mtime) made)
              project (if (and owned (is-not marker None))
                          (let [declared (get marker "env") pr (get declared "project")]
                            (.format "{}:{}" (next (gfor r (get declared "repos") :if (= (get r "name") (get pr "repo")) (get r "url")) "")
                                     (get pr "path")))
                          ""))
        (.append out (RootInfo :key (+ ENV-KEY-PREFIX entry.name) :project project :made-ms (int (* 1000 made))
                               :last-used-ms (int (* 1000 used)) :bytes (if owned (tree-bytes entry) 0) :owned owned))))
    out)

  (defn #^ None sweep [self #^ frozenset pinned]
    "固定の集合を持ち替え、空きが下限を切っていれば掃除する(下限を切っている間は SWEEP-EVERY-SECONDS ごと・固定が変わればすぐ)。"
    (setv changed (!= pinned self.pinned))
    (setv self.pinned pinned)
    (setv disk (.disk-view self) now (time.time))
    (when (or (>= disk.free disk.floor) (and (not changed) (< (- now self.swept-at) SWEEP-EVERY-SECONDS) (> self.swept-at 0)))
      (return))
    (setv self.swept-at now)
    ;; 固定には走っている準備(pending と waiting)も足す(判断の側の観測より新しいので)。
    (setv busy (| pinned (frozenset self.pending) (frozenset self.waiting)))
    (setv chosen (run (sweep-choice (tuple (.root-infos self)) busy disk.free disk.floor)))
    (for [key chosen]
      (setv root (self.root-of key))
      (print (.format "worker: 掃除 — 固定されていない root {} を消す(空き {} byte < 下限 {} byte)" root.name disk.free disk.floor)
             :file sys.stderr :flush True)
      (shutil.rmtree root :ignore-errors True))
    ;; 途中で止まった準備の残り(.<キー>.broken.<時刻>)は worker が退けた物なので消してよい。
    (setv roots (/ self.state "roots"))
    (when (.is-dir roots)
      (for [entry (.iterdir roots)]
        (when (and (.startswith entry.name ".") (in ".broken." entry.name)) (shutil.rmtree entry :ignore-errors True))))
    ;; 7 日使われない native の wheel(使うたびに dir の中の印の file を置き換えて dir の時刻を進める — env_handlers の EnsureNativeWheel)。
    (setv wheels (/ self.state "wheels"))
    (when (.is-dir wheels)
      (for [entry (.iterdir wheels)]
        (when (and (.is-dir entry) (> (- now (. (.stat entry) st-mtime)) WHEEL-UNUSED-SECONDS))
          (shutil.rmtree entry :ignore-errors True))))
    ;; まだ下限を切っていれば uv の cache を prune する(venv の中の file は hardlink なので残る)。prune は uv の cache の lock を取るので、
    ;; 待つと root の準備の uv run が終わるまで worker のループ(heartbeat)が止まる — 別の process として起こして待たない。前の prune が
    ;; 走っている間と、root の準備が走っている間と、前の prune から PRUNE-EVERY-SECONDS の間は起こさない(準備と lock を競わない・
    ;; 他の物が使う node の disk では prune で下限に戻らないので、拍ごとに起こし続けない)。
    (when (and (is-not self.pruning None) (is-not (.poll self.pruning) None))
      (setv self.pruning None))
    (when (and (< (. (.disk-view self) free) disk.floor) (is self.pruning None) (not self.pending) (not self.waiting)
               (or (= self.pruned-at 0.0) (>= (- now self.pruned-at) PRUNE-EVERY-SECONDS)))
      (setv self.pruned-at now)
      (setv self.pruning (subprocess.Popen [self.uv "cache" "prune"]
                                           :env (| (dict os.environ) {"UV_CACHE_DIR" (str (/ self.state "uv-cache"))})
                                           :stdin subprocess.DEVNULL :stdout subprocess.DEVNULL :stderr subprocess.DEVNULL
                                           :start-new-session True))))

  (defn #^ dict report [self]
    "heartbeat で名乗る root の姿(coordinator の置き先と温める表の読みが使う): 準備済み・準備中・失敗のキー(env- を外した物)と
     disk の条件(形は env-report — sim の宿と同じ関数)。"
    (setv views (if (is self.views None) (.observe self) self.views)
          free (. (.disk-view self) free))
    (env-report (tuple views) (run (env-capacity free self.min-free-bytes)))))


(defn #^ int tree-bytes [#^ Path root]  ; defk にできない: EnvStore(Program の外の I/O の道具)が呼ぶ
  "dir の下の file の大きさの合計(掃除で空く量の見積り — hardlink は重ねて数える)。"
  (setv total 0)
  (for [#(dirpath _ filenames) (os.walk root)]
    (for [name filenames]
      (try (+= total (. (os.lstat (os.path.join dirpath name)) st-size)) (except [OSError] None))))
  total)


;; 子 process へ継ぐ worker の環境変数の許可表(実行環境の job — 2026-09-26)。これ以外(PYTHON*・HY_*・UV_* の他・LD_*・VIRTUAL_ENV・
;; 資格を運ぶ変数)は継がない。宣言の env-vars と worker が組む DOEFF_WORKER_*・DOEFF_RUNTIME_ENV* を足す。
(setv CHILD-ENV-ALLOWED (frozenset #("PATH" "HOME" "USER" "LOGNAME" "SHELL" "LANG" "LANGUAGE" "TZ" "TMPDIR" "TERM"
                                     "SSL_CERT_FILE" "SSL_CERT_DIR" "UV_CACHE_DIR" "UV_PYTHON_INSTALL_DIR")))


(defn #^ dict child-environment [#^ dict base #^ dict extra #^ dict declared #^ dict worker]  ; defk にできない: ProcessHost(Program の外の I/O の道具)が呼ぶ
  "実行環境の job の子の環境変数: base(worker の環境)のうち許可表の物と LC_* だけ → worker の文脈(extra)→ 宣言の env-vars(declared)
   → worker が組む DOEFF_*(worker)の順に重ねる。PYTHONPATH は置かない(import の解け先は root の venv と .pth だけ)。"
  (| (dfor #(k v) (.items base) :if (or (in k CHILD-ENV-ALLOWED) (.startswith k "LC_")) k v)
     extra declared worker))


(defn #^ str env-project-dir [#^ str root #^ dict declared]  ; defk にできない: ProcessHost(Program の外の I/O の道具)が呼ぶ
  "root と宣言の JSON → uv の --project に渡す project の dir(env_prepare.project-dir と同じ規則)。"
  (setv project (get declared "project"))
  (if (= (get project "path") ".")
      (.format "{}/{}" root (get project "repo"))
      (.format "{}/{}/{}" root (get project "repo") (get project "path"))))


;; 入口の検め 1 回(同じ木の束 1 本)の時間の上限(2026-09-27 に 60 → 300)。上限は import の速さを測る物ではなく、import の途中で
;; 固まった入口(module の直下の待ち等)を止めるための物。bytecode の無い冷えた root では、込んでいない Pod でも doeff と業務の Hy の
;; compile に壁時計 60 秒前後かかる(本番の実測 = CPU 60 秒)ので、60 秒は冷えた root で必ず切れて撃ち直しを繰り返した。検めは木ごとに
;; 1 本(ProbeStore)・時間切れは process group ごと止める(孫を残さない)ので、上限を広げても重なって積み上がらない。
(setv PROBE-SECONDS 300)
(setv PROBE-DETAIL-CHARS 480)
;; 検めの process を包む shim の猶予(秒)。worker が消えた時(kill -9 を含む)に shim が検めの group を止める — job の子と同じ仕組み。
(setv PROBE-STOP-GRACE "5")


(defn #^ str probe-reason [#^ int code #^ str stderr]
  "検めの process の終了 → 理由の 1 行(stderr の最後の空でない行。無ければ終了の番号)。"
  (setv lines (lfor line (.splitlines stderr) :if (.strip line) (.strip line)))
  (cut (if lines (get lines -1) f"入口の検めが終了 {code} で終わった(理由の出力なし)") 0 PROBE-DETAIL-CHARS))


;; 検めの本体(木の中の道具に依らない — 木の job_entry に probe の口が無い古い commit も同じく検められる)。引数 = 検める import path の列
;; (「module」か「module:attr」)。各々を import し attr の在否を見る(同じ木の束の対象を 1 つの process で — 2026-09-27)。
;; 出力 = 対象ごとに、終わった時点で 1 行 `<PROBE-MARK>["対象", 読み込めない理由 | null]` を flush で出す(時間切れで止めても、それまでに
;; 終わった対象の結果が残る — 固まった入口 1 つが束の全部を道連れにしない)。行の前に改行を 1 つ置く(import した module が改行なしで
;; 標準出力に書いても行が壊れない)。どれが読めなくても 0 で終わる(理由は対象ごとの行が運ぶ)。
(setv PROBE-MARK "doeff-probe-result: ")
(setv PROBE-PROGRAM (.join "\n" [
  "(import importlib json sys)"
  "(for [path (cut sys.argv 1 None)]"
  "  (setv #(module _ attr) (.partition path \":\"))"
  "  (setv reason None)"
  "  (try (setv m (importlib.import-module module)) (when attr (getattr m attr))"
  "    (except [e Exception]"
  "      (setv reason (.format \"{} を読み込めない: {}: {}\" path (. (type e) __name__) (.join \" \" (.split (str e)))))))"
  (+ "  (.write sys.stdout (+ \"\\n\" \"" PROBE-MARK "\" (json.dumps [path reason] :ensure-ascii False) \"\\n\"))")
  "  (.flush sys.stdout))"]))


(defn #^ list probe-targets [#^ JobSpec spec]
  "検める import path の列: 入口の module(spec.entry)だけ(Program の job は関数の参照を持たない — 版と復元は起こした子が検める)。"
  [spec.entry])


(defn #^ tuple probe-launches [#^ list waiting #^ frozenset busy]  ; defk にできない: ProbeStore(Program の外の I/O の道具)が呼ぶ純粋な判断
  "純粋: 待っている検めの束の鍵(#(木 実行環境の宣言の JSON か None 単独の spec-hash か None) — 来た順)と、検めの process が走っている木
   → 今起こす束の鍵(木ごとに 1 つ・来た順)。同じ木(root)の検めは import の閉包の大半が同じなので、並べると同じ compile を本数ぶん
   撃つ(2026-09-27 の本番: 7 本が並んで CPU の上限 4 の Pod を締め付けた)— 1 本ずつにし、同じ拍に来た物は 1 本の束にまとめる。
   単独の鍵(3 つ目が spec-hash)= 直前に時間切れになった spec(束に混ぜず 1 本で検める — 固まる入口が次の束を道連れにしない)。"
  (setv chosen [] trees (set busy))
  (for [key waiting]
    (when (not-in (get key 0) trees)
      (.append chosen key)
      (.add trees (get key 0))))
  (tuple chosen))


(defclass ProbeSettle [Enum]
  ;; 束が終わった時の spec 1 つの行き先: 結果どおりに決まる・固まった対象を持つので時間切れ・結果が出る前に束が止まったので待ちへ戻す。
  (setv DECIDED "decided" TIMED-OUT "timed-out" REQUEUE "requeue"))


(defrecord ProbeSettled
  "spec 1 つの行き先と、決まった時の理由(DECIDED で None = 読み込めた)。"
  (#^ ProbeSettle kind)
  (setv #^ (| str None) reason None))


(defn #^ (| str None) probe-verdict [#^ list targets #^ dict results]  ; defk にできない: ProbeStore(Program の外の I/O の道具)が呼ぶ純粋な判断
  "純粋: spec の検める対象と、束の結果(対象 → 読み込めない理由か None)→ 最初に読み込めなかった対象の理由(全部読めたら None)。
   結果に無い対象は、検めの本体が答えなかった物として読み込めない扱いにする。"
  (for [target targets]
    (cond
      (not-in target results) (return (.format "{} の検めの答えが無い" target))
      (is-not (get results target) None) (return (cut (str (get results target)) 0 PROBE-DETAIL-CHARS))))
  None)


(defn #^ ProbeSettled probe-settle [#^ list targets #^ dict results #^ bool timed-out #^ (| str None) stuck #^ (| str None) crash]  ; defk にできない: ProbeStore(Program の外の I/O の道具)が呼ぶ純粋な判断
  "純粋: 束が終わった後の spec 1 つの行き先。targets = spec の対象・results = 束が出した対象ごとの結果・timed-out = 時間切れで止めた・
   stuck = 時間切れの時に進んでいた対象(束の順で結果の無い最初の物)・crash = 本体が 0 以外で終わった時の理由(0 で終わったら None)。
   対象の結果が全部出ていれば結果どおり。時間切れなら、進んでいた対象を持つ spec だけ時間切れ、ほかは待ちへ戻す。0 以外で終わった束の
   結果の欠けた spec はその理由で失敗。"
  (cond
    (all (gfor t targets (in t results))) (ProbeSettled :kind ProbeSettle.DECIDED :reason (probe-verdict targets results))
    (and timed-out (in stuck targets)) (ProbeSettled :kind ProbeSettle.TIMED-OUT)
    timed-out (ProbeSettled :kind ProbeSettle.REQUEUE)
    (is-not crash None) (ProbeSettled :kind ProbeSettle.DECIDED :reason crash)
    True (ProbeSettled :kind ProbeSettle.DECIDED :reason (probe-verdict targets results))))


(defn #^ dict probe-results [#^ str stdout]  ; defk にできない: ProbeStore(Program の外の I/O の道具)が呼ぶ
  "検めの process の標準出力 → 対象ごとの結果(PROBE-MARK で始まる行・途中で止めた束は、それまでに終わった対象だけ)。"
  (setv results {})
  (for [line (.splitlines stdout)]
    (setv body (.strip line))
    (when (.startswith body PROBE-MARK)
      (try
        (setv pair (json.loads (cut body (len PROBE-MARK) None)))
        (except [ValueError] (continue)))
      (when (and (isinstance pair list) (= (len pair) 2) (isinstance (get pair 0) str))
        (setv (get results (get pair 0)) (get pair 1)))))
  results)


(defrecord ProbeRun
  "走っている検めの束 1 本(木ごとに 1 本): shim の process(process group の先頭)・束の spec・束の対象(検める順)・実行環境の宣言・
   起こした時刻(単調時計と epoch ms)・標準出力と標準エラーを受ける無名の一時 file(pipe は溜まると子が止まるので使わない)。"
  (#^ subprocess.Popen process)
  (#^ tuple specs)
  (#^ tuple targets)
  (#^ (| str None) runtime-env)
  (#^ float started)
  (#^ int started-ms)
  (#^ (get IO bytes) out)
  (#^ (get IO bytes) err))


(defclass ProbeStore []
  "入口の検め(2026-09-25): service の job の木で、worker の実行環境が入口(factory と env)を読み込めるかを子 process で試す。
   結果は observe で拾う(ループを塞がない)。実行は ProcessHost と同じ hy・同じ PYTHONPATH(layout の import の根)・cwd = 木。
   実行環境の job(spec.runtime-env — 2026-09-26)は子と同じ起こし方で検める: root の venv の
   `uv run --no-sync --frozen --project <root の project> hy -c …`・環境変数は子と同じ許可表・PYTHONPATH を置かない・cwd = 空の dir
   (probe-dir)。worker の venv で検めると、env の root に無い module を worker の venv が読めて誤って通る。
   子と同じく bytecode を書く(PYTHONDONTWRITEBYTECODE を置かない — 置くと撃つたびに同じ Hy を source から compile し直した)。
   前提: 入口の module の import は冪等(読むだけで外へ書かない)。束は同じ木の入口を 1 つの process で順に import する。

   process の寿命(2026-09-27): 検めは job の子と同じ shim(doeff_cluster.shim)を新しい process group の先頭にして起こし、束が
   終わったら(通った・失敗した・時間切れ・shim が先に死んだのどれでも)group ごと KILL する(以前は時間切れの時に直の子 = uv だけを
   kill し、孫の hy が孤児として CPU を使い続けた)。worker が消えれば shim が stdin の EOF で group を止める。
   shim は -B で起こす(2026-09-28・日次 t97 の柵の赤)— shim 自身は worker の install から import される。job の子の env は許可表で
   しか継がないので PYTHONDONTWRITEBYTECODE が落ち、shim の import が worker の install(検では checkout)へ .pyc を書いていた。
   並べ方(2026-09-27): 同じ木の検めは 1 本ずつ(probe-launches)。start は束に積むだけで、process は observe の頭で起こす —
   同じ拍に来た同じ木の spec は 1 本の process にまとめ、対象ごとの結果の行から、読み込めない理由をその対象を持つ spec にだけ付ける。
   timeout-seconds を越えた束は止め、結果の出た対象は結果どおり・進んでいた対象を持つ spec だけ時間切れの FAILED・残りの spec は
   待ちへ戻す(probe-settle)。時間切れの spec の撃ち直しは単独の束で起こす。結果の鍵は spec-hash(同じ spec の検めは撃ち直される
   まで答えを使い回す)。回数と前の回の失敗の理由は撃ち直しの間も持つ(状態の報告の probing)。"
  (defn #^ None __init__ [self #^ str hy-command #^ (| int float) [timeout-seconds PROBE-SECONDS] #^ CodeLayout [layout (CodeLayout)]
                          #^ str [uv "uv"] #^ (| str None) [probe-dir None]]
    (setv self.hy-command hy-command self.timeout-seconds timeout-seconds self.layout layout
          self.uv uv self.probe-dir (Path (or probe-dir "probe"))
          ;; 束の鍵 #(木 実行環境の宣言 単独の spec-hash か None) → 待っている spec の列(dict の順 = 来た順)・木 → 走っている束・
          ;; spec-hash → 答え。
          self.waiting {} self.runs {} self.done {}
          ;; spec-hash → 検めた回数・前の回の失敗の理由。timed-out = 直前の検めが時間切れだった spec-hash(次は単独で起こす)。
          self.attempts {} self.last-failure {} self.timed-out (set)))

  (defn #^ list command [self #^ str code-path #^ (| str None) runtime-env #^ list targets]
    "検めの子の #(argv cwd 環境変数)— 実行環境の job は子と同じ root の venv、それ以外は worker の hy と木の PYTHONPATH。"
    (if runtime-env
        (do (setv declared (json.loads runtime-env))
            (.mkdir self.probe-dir :parents True :exist-ok True)
            [[self.uv "run" "--no-sync" "--frozen" "--project" (env-project-dir code-path declared)
              "hy" "-c" PROBE-PROGRAM #* targets]
             (str self.probe-dir)
             (child-environment (dict os.environ) {}
                                (dfor v (.get declared "envVars" []) (get v "name") (get v "value"))
                                {})])
        [[self.hy-command "-c" PROBE-PROGRAM #* targets]
         code-path
         (| (dict os.environ) {"PYTHONPATH" (.pythonpath self.layout code-path)})]))

  (defn #^ bool in-flight [self #^ str key]
    (or (any (gfor specs (.values self.waiting) spec specs (= (spec-hash spec) key)))
        (any (gfor run (.values self.runs) spec run.specs (= (spec-hash spec) key)))))

  (defn #^ None start [self #^ ProbeEntry action]
    (setv key (spec-hash action.spec))
    (when (.in-flight self key) (return))
    (setv prior (.pop self.done key None))
    (when (and (is-not prior None) (= prior.state ProbeState.FAILED))
      (setv (get self.last-failure key) prior.detail))
    (setv (get self.attempts key) (+ (.get self.attempts key 0) 1))
    ;; 旧い形の service の spec は process を起こさずに失敗とする(理由つき — 計画 2.8 の入口 15)。
    (setv refusal (probe-refusal action.spec))
    (when (is-not refusal None)
      (setv now-ms (int (* (time.time) 1000)))
      (setv (get self.done key) (.view self action.spec ProbeState.FAILED now-ms :detail refusal :failed-ms now-ms))
      (return))
    ;; 直前が時間切れの spec は単独の束(鍵の 3 つ目 = spec-hash)。
    (setv solo (in key self.timed-out))
    (.discard self.timed-out key)
    (.append (.setdefault self.waiting #(action.code-path action.spec.runtime-env (if solo key None)) []) action.spec))

  (defn #^ None forget [self #^ frozenset keep]
    "宣言から消えた spec の検めの記録を落とすため(2026-09-27 — worker_model.ForgetProbes): keep(今の宣言の spec-hash)に無い spec の
     答え・回数・前の回の失敗の理由・時間切れの印と、待っている束の中のその spec を落とす。走っている束の process は止めない
     (終わった後の答えは次の拍の観測に出て、その拍の ForgetProbes で落ちる)。"
    (setv running (sfor run (.values self.runs) spec run.specs (spec-hash spec)))
    (for [batch (list self.waiting)]
      (setv kept (lfor spec (get self.waiting batch) :if (in (spec-hash spec) keep) spec))
      (if kept (setv (get self.waiting batch) kept) (del (get self.waiting batch))))
    (for [table [self.done self.attempts self.last-failure]]
      (for [key (list table)]
        (when (and (not-in key keep) (not-in key running)) (del (get table key)))))
    (for [key (list self.timed-out)]
      (when (and (not-in key keep) (not-in key running)) (.discard self.timed-out key))))

  (defn #^ None launch [self #^ tuple batch #^ list specs]
    "束 1 本を起こす: 束の spec の対象を重ねずに並べ、shim を group の先頭にして 1 つの process で検める。"
    (setv #(code-path runtime-env _) batch
          targets (list (dict.fromkeys (gfor spec specs target (probe-targets spec) target)))
          #(argv cwd env) (.command self code-path runtime-env targets)
          out (tempfile.TemporaryFile) err (tempfile.TemporaryFile))
    (setv process (subprocess.Popen [sys.executable "-B" "-m" "doeff_cluster.shim" PROBE-STOP-GRACE "--" #* argv]
                                    :cwd cwd :env env :stdin subprocess.PIPE :stdout out :stderr err
                                    :start-new-session True))
    (setv (get self.runs code-path)
          (ProbeRun :process process :specs (tuple specs) :targets (tuple targets) :runtime-env runtime-env
                    :started (time.monotonic) :started-ms (int (* (time.time) 1000)) :out out :err err)))

  (defn #^ ProbeView view [self #^ JobSpec spec #^ ProbeState state #^ (| int None) started-ms #^ str [detail ""] #^ (| int None) [failed-ms None]]
    (setv key (spec-hash spec))
    (ProbeView key state :detail detail :failed-ms failed-ms :started-ms started-ms
               :attempts (.get self.attempts key 1) :last-failure (.get self.last-failure key "")))

  (defn #^ None finish [self #^ str tree #^ ProbeRun run #^ (| int None) code]
    "束を片づけて答えを置く。code = 終了の番号(None = 時間切れ)。どの終わり方でも group ごと KILL する(本体が起こした孫・shim だけが
     先に死んだ時の本体を残さない — ProcessHost.reap と同じ)。"
    (try (os.killpg run.process.pid signal.SIGKILL) (except [ProcessLookupError] None))
    (when (is code None) (.wait run.process))
    (when (is run.process.stdin None)
      (raise (RuntimeError "検めの shim は stdin を pipe で起こしているのに、pipe が無い")))
    (.close run.process.stdin)
    (.seek run.out 0)
    (.seek run.err 0)
    (setv stdout (.decode (.read run.out) "utf-8" "replace")
          stderr (.decode (.read run.err) "utf-8" "replace")
          results (probe-results stdout)
          stuck (next (gfor t run.targets :if (not-in t results) t) None)
          crash (if (or (is code None) (= code 0)) None (probe-reason code stderr))
          now-ms (int (* (time.time) 1000)))
    (.close run.out)
    (.close run.err)
    (del (get self.runs tree))
    (for [spec run.specs]
      (setv key (spec-hash spec)
            settled (probe-settle (probe-targets spec) results (is code None) stuck crash))
      (cond
        (= settled.kind ProbeSettle.REQUEUE)
          ;; 結果の出る前に束が止まった spec は、回数を増やさずに待ちへ戻す(次の束で検める)。
          (.append (.setdefault self.waiting #(tree run.runtime-env None) []) spec)
        (= settled.kind ProbeSettle.TIMED-OUT)
          (do (.add self.timed-out key)
              (setv (get self.done key)
                    (.view self spec ProbeState.FAILED run.started-ms :failed-ms now-ms
                           :detail (.format "入口の検めが {} 秒で終わらない(process group ごと止めた): {} の読み込みの途中"
                                            self.timeout-seconds stuck))))
        (is settled.reason None) (setv (get self.done key) (.view self spec ProbeState.PASSED run.started-ms))
        True (setv (get self.done key) (.view self spec ProbeState.FAILED run.started-ms :detail settled.reason :failed-ms now-ms)))))

  (defn #^ tuple observe [self]
    ;; 待っている束を起こす(木ごとに 1 本 — 走っている木の束は、その終わりを待つ)。
    (for [batch (probe-launches (list self.waiting) (frozenset self.runs))]
      (.launch self batch (.pop self.waiting batch)))
    (for [#(tree run) (list (.items self.runs))]
      (setv code (.poll run.process))
      (cond
        (is-not code None) (.finish self tree run code)
        (> (- (time.monotonic) run.started) self.timeout-seconds) (.finish self tree run None)))
    (tuple (+ (lfor specs (.values self.waiting) spec specs (.view self spec ProbeState.QUEUED None))
              (lfor run (.values self.runs) spec run.specs (.view self spec ProbeState.RUNNING run.started-ms))
              (list (.values self.done))))))


(defclass ProcessHost []
  "job ごとに子 process を 1 本、専用の process group で起動する。extra-env = 子へ渡す worker の文脈(名前・coordinator)。
   layout = 業務の repo の木の形(子の PYTHONPATH — worker_model.CodeLayout)。
   実行環境の job(spec.runtime-env)は、env の root の venv で `uv run --no-sync --frozen --project <root の project> hy -m …` として
   起こす(PYTHONPATH を置かない・子の環境変数は許可表で組む・cwd = 空の作業 dir <jobs-dir>/<job の名>)。uv = uv の命令。"
  (defn #^ None __init__ [self #^ str log-dir #^ str hy-command #^ (| dict None) [extra-env None] #^ CodeLayout [layout (CodeLayout)]
                  #^ str [uv "uv"] #^ (| str None) [jobs-dir None]]
    (setv self.log-dir (Path log-dir) self.hy-command hy-command self.table {} self.extra-env (or extra-env {})
          self.layout layout self.uv uv
          self.jobs-dir (if jobs-dir (Path jobs-dir) (/ (. (Path log-dir) parent) "jobs"))
          ;; 詰めた Program の cache(CoordinatorLink が /programs/<sha> から取って書く — 既定は同じ state dir の programs)。
          self.program-dir (/ (. (Path log-dir) parent) "programs")))

  (defn #^ Path work-dir [self #^ str name]
    "実行環境の job の子の cwd(job の名ごとの空の dir)。"
    (/ self.jobs-dir (.replace name "/" "_")))

  (defn #^ tuple launch [self #^ JobSpec spec #^ str code-path #^ str instance #^ int attempt]
    "子の #(argv cwd 環境変数)。実行環境の job は root の venv の uv run、それ以外は今の形(木の PYTHONPATH)。"
    ;; 子の文脈の環境変数は sim の宿(local.run-context-of)と同じ関数 process-context-environ で作る(実行環境の job だけが
    ;; DOEFF_RUNTIME_ENV・DOEFF_RUNTIME_ENV_KEY を受ける)。pid は本番の子だけが読む欄。
    (setv worker-env (| (run (process-context-environ spec instance attempt)) {"DOEFF_WORKER_PID" (str (os.getpid))})
          ;; Program の job(改訂 1 の F・H): 詰めた Program の file を引数と環境変数(宿の契約 HOST-CONTRACT)で渡す。
          program-args (if spec.program #("--program" (str (program-file self.program-dir spec.program))) #())
          environ (dict spec.environ))
    (when spec.program
      (setv (get worker-env HOST-CONTRACT.program-env) (str (program-file self.program-dir spec.program))))
    (if spec.runtime-env
        (do (setv declared (json.loads spec.runtime-env)
                  work (self.work-dir spec.name))
            ;; 使った印(掃除は最後に使った時刻の古い root から消す — env_upkeep.sweep-choice)。
            (.touch (/ (Path code-path) ".last-used"))
            (when (.exists work) (shutil.rmtree work))
            (.mkdir work :parents True)
            #([sys.executable "-B" "-m" "doeff_cluster.shim" "10" "--" self.uv "run" "--no-sync" "--frozen"
               "--project" (env-project-dir code-path declared) "hy" "-m" spec.entry #* spec.args #* program-args]
              (str work)
              (child-environment (dict os.environ) self.extra-env
                                 (| (dfor v (.get declared "envVars" []) (get v "name") (get v "value")) environ)
                                 worker-env)))
        #([sys.executable "-B" "-m" "doeff_cluster.shim" "10" "--" self.hy-command "-m" spec.entry #* spec.args #* program-args]
          code-path
          (| (dict os.environ) self.extra-env environ {"PYTHONPATH" (.pythonpath self.layout code-path)} worker-env))))

  (defn #^ None start [self #^ StartJob action]
    (setv spec action.spec)
    (when (in spec.name self.table) (raise (RuntimeError f"{spec.name} は既に動いています")))
    (.mkdir self.log-dir :parents True :exist-ok True)
    (setv log-name (.replace spec.name "/" "_"))  ; task の名前は task/<id>
    (setv log (open (/ self.log-dir f"{log-name}.{action.attempt}.log") "ab"))
    ;; process の世代の名: 起こすたびに新しく振る(試行の番号は worker の再起動で 1 に戻るので、それだけでは前の process と重なる)。
    (setv instance f"{action.attempt}-{(cut (. (uuid.uuid4) hex) 0 12)}")
    (setv #(argv cwd env) (.launch self spec action.code-path instance action.attempt))
    (try
      ;; shim を group の先頭に置き、stdin のパイプを worker が握る。worker が死ぬとパイプが閉じ、
      ;; shim が job の group を止める(kill -9 された worker の job が残って二重に動くのを防ぐ)。
      (setv process (subprocess.Popen argv :cwd cwd :env env :stdout log :stderr subprocess.STDOUT
                                      :stdin subprocess.PIPE :start-new-session True))
      (finally (.close log)))
    ;; 起こした job の記録(2026-09-26 — 「新しい版の job は worker を再起動せず、worker が展開した版の木の子 process で走る」を
    ;; worker の記録で示すため): job の名・版・木の path・子の pid・worker の pid を 1 行。env と引数の値は書かない(資格を運びうる)。
    (.write sys.stderr (.format "worker: job-start name={} revision={} tree={} pid={} worker-pid={}\n"
                                spec.name spec.revision action.code-path process.pid (os.getpid)))
    (.flush sys.stderr)
    (setv (get self.table spec.name)
      #(process (ProcessView spec.name spec action.attempt process.pid (int (* (time.time) 1000)) :instance instance))))

  (defn #^ None retire [self #^ RetireJob action]
    "入れ替え: 動いている process を止めずに名から外す(表の鍵と観測の名を new-name へ移す)。同じ名で新しい process を起こせる。"
    (setv entry (.get self.table action.name))
    (when (and entry (= (. (get entry 1) pid) action.pid))
      (del (get self.table action.name))
      (setv (get self.table action.new-name)
            #((get entry 0) (replace (get entry 1) :name action.new-name :retired-from action.name)))))

  (defn #^ None signal [self #^ SignalJob action]
    (setv sig (if (= action.stage StopStage.TERM) signal.SIGTERM signal.SIGKILL))
    ;; 孫 process まで届くよう process group へ送る(setsid で抜けた孫は届かない)。
    (try (os.killpg action.pid sig) (except [ProcessLookupError] None)))

  (defn #^ None reap [self #^ ReapJob action]
    (setv entry (.get self.table action.name))
    (when (and entry (= (. (get entry 1) pid) action.pid))
      ;; 本体の終了後も同じ group の孫が残っていれば KILL で回収する。
      (try (os.killpg action.pid signal.SIGKILL) (except [ProcessLookupError] None))
      (.close (. (get entry 0) stdin))
      ;; 実行環境の job の作業 dir(worker が作った物だけ)は、終わった後に消す。
      (when (. (get entry 1) spec runtime-env)
        (shutil.rmtree (self.work-dir action.name) :ignore-errors True))
      (del (get self.table action.name))))

  (defn #^ tuple observe [self]
    ;; 終わりの code は内包の :setv で 1 度だけ読む(do の中の setv は内包の外の名への束縛に見え、型検査が束縛を見つけない — #1690)
    (tuple (gfor #(process view) (.values self.table)
                 :setv code (.poll process)
                 (if (is code None) view
                     (replace view :exit-code code))))))

(defhandler local-host [#^ CodeStore codes #^ ProcessHost host #^ ProbeStore probes #^ (| EnvStore None) [envs None]]
  ;; 引数に残す理由: 4 つとも worker の process が持つ I/O の資源(子 process と準備の process の表)で、同じ組が観測と action の
  ;; 両方に答える。envs = 実行環境の root の準備(None = 実行環境の job を扱わない worker — PrepareEnv は断る)。
  (ObserveWorld [] (resume (WorldView (+ (.observe codes) (if (is envs None) #() (.observe envs))) (.observe host) (.observe probes)
                                      :env-disk (if (is envs None) None (.disk-view envs)))))
  (PrepareCode [revision] (.start codes revision) (resume None))
  (PrepareEnv [key runtime-env warm]
    (when (is envs None)
      (raise (RuntimeError "この worker は実行環境の job を扱えない(EnvStore が無い)")))
    (.start envs key runtime-env :warm warm)
    (resume None))
  (SweepEnvs [pinned]
    (when (is-not envs None) (.sweep envs pinned))
    (resume None))
  (ProbeEntry [spec code-path] (.start probes (ProbeEntry spec code-path)) (resume None))
  (ForgetProbes [keep] (.forget probes keep) (resume None))
  (StartJob [spec attempt code-path]
    (.start host (StartJob spec attempt code-path)) (resume None))
  (SignalJob [name pid stage] (.signal host (SignalJob name pid stage)) (resume None))
  (ReapJob [name pid outcome exit-code]
    (.reap host (ReapJob name pid outcome exit-code)) (resume None))
  (RetireJob [name pid new-name]
    (.retire host (RetireJob name pid new-name)) (resume None)))

(defn #^ dict status-json [#^ tuple statuses #^ str note #^ dict timings]
  {"note" note
   "codePrepareSeconds" timings
   "jobs" (lfor s statuses (status-row s))})

;; 状態の file の mode(前の形の Path.write-text が umask 022 の下で作った物と同じ — 置き換えの書きの一時 file は 0600 なので明示する)。
(val STATUS-FILE-MODE 0o644)


(defk write-status-file [path content]
  {:pre [(: path str) (: content dict)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "worker の状態を、外から覗ける 1 つの JSON の file として置くため(親の dir を作り、置き換えで書いて書きかけを読ませない)。
   file の I/O は file system の effect(本番 = os-file-handler・検 = memory-file-handler)で、断りは OSError で上げる。"
  (<- (file-done (MakeDirectory (os.path.dirname path))))
  (<- (file-done (WriteText path (json.dumps content :ensure-ascii False :indent 1) :mode STATUS-FILE-MODE :replace True)))
  None)


(defhandler status-file [#^ str path #^ CodeStore codes]
  ;; 引数に残す理由: 置き場の path と、焼きの経過の秒を持つ CodeStore(worker の process の資源)は worker ごとの値。
  (PublishStatus [statuses note]
    (<- (write-status-file path (status-json statuses note codes.timings)))
    (resume None)))

(setv JOB-ENTRY "doeff_cluster.job_entry")


(defn #^ JobSpec task-spec [#^ dict task #^ Path task-dir]
  "coordinator が割り当てた task 1 本 → 1 度だけ走らせる job。結果はこの worker の file(名前は task の id で決まる)。詰めた Program は
   service の job と同じく置き場のキー program(sha)で持ち、CoordinatorLink.accept-programs が /programs/<sha> から cache へ取り、
   ProcessHost が `--program <cache の file>` を足す(入口は `task --result <file> --program <file>` — 版は file の中の versions)。
   実行環境の task(runtimeEnv を持つ)は、env のキー(この worker の platform で計算)を root の置き場の鍵にする(env-placement)。"
  (setv id (get task "id"))
  (setv #(revision runtime key) (env-placement (.get task "runtimeEnv") (get task "revision")))
  (JobSpec (+ "task/" id) JOB-ENTRY
           #("task" "--result" (str (/ task-dir f"{id}.result")))
           revision :once True :detached (bool (.get task "detached" False)) :runtime-env runtime :env-key key
           :program (get task "program")
           ;; 子の環境変数(service の job と同じ欄・同じ路 — ProcessHost.launch が宣言の env-vars の上に重ねる)。
           :environ (environ-pairs (.get task "environ" {}))))


(defclass WatchState []
  "背景の待ちの thread と拍(CoordinatorLink.poll)が分ける状態(#1933 — beat_policy)。after = 次の待ちの版(前の heartbeat の返事の
   版・lock で守る)・confirmed = 待ちが 1 度答えた(口を確かめた)・unsupported = 待つ口が無い(404)・woken = 待ちが「変わった」と
   答えた印・beat-done = heartbeat が届いた合図・closing = 止めの合図・failure = thread が止まった理由・told = 最後に出した 1 行の鍵。"
  (defn #^ None __init__ [self]
    (setv self.lock (threading.Lock) self.after None self.confirmed False self.unsupported False
          self.woken (threading.Event) self.beat-done (threading.Event) self.closing (threading.Event)
          self.failure "" self.told None))

  (defn #^ (| int None) after-now [self]
    "次の待ちの版を読むため(拍の thread が書き換える)。"
    (with [self.lock]
      (setv after self.after))
    after)

  (defn #^ None beaten [self #^ (| int None) revision]
    "heartbeat が届いた時に、次の待ちの版を返事の版にし、起こした後の待ちを解くため。"
    (with [self.lock]
      (setv self.after revision))
    (.set self.beat-done))

  (defn #^ None advance [self #^ int after #^ int revision]
    "「変わっていない」と答えた待ちの版へ進めるため(その間に heartbeat が版を書き換えていれば、そちらを残す)。"
    (with [self.lock]
      (when (= self.after after)
        (setv self.after revision))))

  (defn #^ None confirm [self]
    "待ちが答えた(待つ口を使える)と記すため。"
    (setv self.confirmed True))

  (defn #^ None tell-watch [self #^ str key #^ str line]
    "待ちの結果の変わり目だけを stderr へ 1 行出すため(同じ失敗を拍ごとに繰り返さない)。"
    (when (!= key self.told)
      (setv self.told key)
      (print line :file sys.stderr :flush True))))


(defclass CoordinatorLink []
  "coordinator との連絡。heartbeat で生存・版・状態(終わった task の結果を含む)を送り、自分に割り当てられた job と task を受け取る。
   task の blob は task-dir の file に置き、宣言から外れた task の file は消す(この worker が書いた物だけ)。"
  (defn #^ None __init__ [self #^ str url #^ str name #^ tuple provides #^ int capacity #^ int fence-ms
                  #^ (| str None) [task-dir None] #^ (| dict None) [versions None] #^ (| httpx.BaseTransport None) [transport None] #^ (| dict None) [tools None] #^ (| EnvStore None) [envs None]
                  #^ tuple [exclusive #()] #^ str [node ""] #^ bool [watch False]]
    ;; watch = heartbeat を拍から切り離し、desired の変化を名指しの待ち(GET /watch)で受けるか(#1933 — beat_policy)。真なら返事に版を
    ;; 持つ coordinator に背景の thread で待ちを送り続け、heartbeat は beat_policy.heartbeat-due の時だけ送る。偽(既定 — 検の道具の
    ;; link)なら今までどおり拍ごとに送る。本番の worker の入口(main.hy)が真にする。
    ;; node = この worker の置かれた k8s の node の名(downward API の spec.nodeName・k8s の外の機体は空)。coordinator がその node の
    ;; label から能力(company-machine など)を導く — worker の自己申告にしない(改訂 1 の I)。
    ;; provides / exclusive = この worker が提供する能力・専用の能力の名(名の順 — cluster_model.capabilities-of・ADR-DOE-CLUSTER-001 R4b)。
    ;; tools = この worker が名乗る道具(名 → 版 — 実行環境の宣言の tools と照らして置き先を選ぶ)。
    ;; envs = 実行環境の root の置き場(在れば、準備済み・準備中・失敗の root と disk の条件を heartbeat で名乗り、温める表を受ける)。
    (setv self.name name self.provides provides self.exclusive exclusive self.node node self.capacity capacity self.tools (or tools {}) self.envs envs
          self.last-warm #() self.warm-keys {}
          self.fence-ms fence-ms self.statuses []
          ;; 宛先は `,` で並べた物(前ほど優先)。毎拍やり直すので一巡以上は送り直さない(拍を塞がない)・接続は使い回す。
          ;; 自己停止を数える last-ok は宛先と無関係にこの link が持つので、宛先を替えても途絶の数え方は続く。
          self.endpoint (CoordinatorEndpoint url REPLY-SECONDS 0 :transport transport :actor name)
          self.task-dir (Path (or task-dir "tasks")) self.versions (or versions {})
          ;; 詰めた Program の cache(/programs/<sha> から取る — ProcessHost と同じ state dir の programs・改訂 1 の F)。
          self.program-dir (/ (. (Path (or task-dir "tasks")) parent) "programs")
          ;; 起動した時点を最後の連絡とみなす: 一度も届かない worker は fence の後に何も動かさない。
          self.last-ok (time.monotonic)
          ;; 最後に受け取った job と task の宣言(途絶の間も動かす書き手と切り離した task を選ぶ — worker_policy.kept-when-cut-off)。
          self.last-jobs #()
          self.last-tasks #()
          ;; この process の世代(heartbeat の boot)。coordinator は drain を頼まれた時の世代に付け、別の世代(Pod を作り直した後の
          ;; worker)の heartbeat で drain を解く(cluster_policy.absorb-boot・2026-09-25)。
          self.boot (. (uuid.uuid4) hex)
          ;; この process の起動時刻(heartbeat の bootAt・epoch ms — 2026-09-27)。世代を決める所で 1 回だけ決める。coordinator は
          ;; 同じ名の 2 つの世代の新旧を、両方の起動時刻を知る時はこれで決める(cluster_policy.generation-order)。
          self.boot-at (int (* (time.time) 1000))
          ;; 切り離した task の id → 置かれた時の返事の行(blob を除く)。状態の報告に写して添え、状態を失った coordinator が
          ;; 走っている task を引き取れるようにする(cluster_policy.adopt-running-detached・2026-09-27)。
          self.task-echo {}
          ;; 最後に stderr へ出した heartbeat の結果(None = まだ出していない・"" = 名乗れた・それ以外 = 名乗れない理由)— tell。
          self.told None
          ;; --- heartbeat の切り離し(#1933 — beat_policy)---
          ;; watch-state = 背景の待ちの thread と拍(poll)の間で分ける状態(lock で守る)。last-desired = 前の heartbeat の返事の desired
          ;; (届かなかったら None — 次の拍で必ず送る)・sent-statuses = 前に届けた状態の報告・beat-interval-ms = 送る間隔。
          self.watch-enabled watch
          self.watch-state (WatchState)
          self.watch-endpoint (CoordinatorEndpoint url REPLY-SECONDS 0 :transport transport :actor name)
          self.watcher None
          self.last-desired None
          self.sent-statuses None
          self.beat-interval-ms (beat-interval-ms None {}))
    ;; 世代を Pod の中の file へ書く(DOEFF_WORKER_BOOT_FILE)— readinessProbe が「coordinator の見る worker がこの Pod の物か」を
    ;; 比べる(drain_client.ready-of)。同じ node の前の Pod と名が同じなので、名だけでは見分けられない。
    (setv boot-file (os.environ.get "DOEFF_WORKER_BOOT_FILE"))
    (when boot-file
      (setv tmp (Path (+ boot-file ".tmp")))
      (.write-text tmp (+ self.boot "\n") :encoding "utf-8")
      (os.replace tmp boot-file)))

  (defn #^ tuple accept-tasks [self #^ list tasks]
    "heartbeat の返事の task の行 → 1 度だけ走らせる job。task ごとに、その Program の置き場のキーを印の file <id>.program に残し
     (返事から外れた task の Program の cache を後で消すため — accept-programs)、返事から外れた task の結果の file を消す(この worker が
     書いた物だけ)。切り離した task は返事の行をそのまま写しとして持つ(状態の報告に添える — 欄 task)。"
    (.mkdir self.task-dir :parents True :exist-ok True)
    (setv ids (sfor t tasks (get t "id")))
    (for [task tasks]
      (setv mark (/ self.task-dir (+ (get task "id") ".program")))
      ;; 同じ id の印が別の sha を指していれば書き直す(印は cache の掃除にだけ使う — 子へ渡す file は返事の行の sha で決まる)。
      (when (!= (if (.exists mark) (.read-text mark :encoding "ascii") None) (get task "program"))
        (.write-text mark (get task "program") :encoding "ascii")))
    ;; .blob = 詰めた Program を行に持っていた版の worker が書いた file(置き場 /programs の前)— 残っていれば一緒に消す。
    (for [entry (.iterdir self.task-dir)]
      (when (and (in entry.suffix #(".blob" ".result")) (not-in entry.stem ids))
        (.unlink entry :missing-ok True)))
    (setv self.task-echo (dfor task tasks :if (.get task "detached") (get task "id") (dict task)))
    (tuple (gfor task tasks (task-spec task self.task-dir))))

  (defn #^ None accept-programs [self #^ tuple specs]
    "宣言の job と task のうち、cache に無い詰めた Program を coordinator の /programs/<sha> から取って書く(改訂 1 の F — service と
     task で同じ仕組み)。中身の sha256 がキーと合わない物は書かない。取れなければ書かずに次の拍で試し直す(子は file が無いので起動の
     時に理由つきで落ちる — task は結果の file に RemoteJobFailed)。返事から外れた task の Program の cache は、今の job と task の
     どれも参照していなければ消す(task ごとの印 <id>.program から引く — service の job の Program は消さない)。"
    (.mkdir self.program-dir :parents True :exist-ok True)
    (setv wanted (sfor s specs :if s.program s.program))
    (for [sha (sorted wanted)]
      (setv path (program-file self.program-dir sha))
      (when (not (.exists path))
        (try
          (setv response (.request self.endpoint "GET" (+ "/programs/" sha)))
          (.raise-for-status response)
          (setv body (.json response))
          (when (!= (program-sha (get body "blob")) sha)
            (raise (ValueError (+ "中身の sha256 がキーと合わない: " sha))))
          (write-program-file self.program-dir sha (get body "blob") (.get body "versions" {}))
          (except [error Exception]
            (print (.format "worker: Program {} を取れない: {!r}" sha error) :file sys.stderr :flush True)))))
    (setv current (sfor s specs :if (.startswith s.name "task/") (cut s.name 5 None)))
    (when (.exists self.task-dir)
      (for [mark (.glob self.task-dir "*.program")]
        (when (not-in mark.stem current)
          (setv sha (.strip (.read-text mark :encoding "ascii")))
          (when (and sha (not-in sha wanted))
            (.unlink (program-file self.program-dir sha) :missing-ok True))
          (.unlink mark :missing-ok True)))))

  (defn #^ dict env-body [self]
    "heartbeat に足す root の名乗り(実行環境を扱う worker だけ): platform・準備済み / 準備中 / 失敗の root・disk の条件
     (形は env-heartbeat-part — sim の宿と同じ関数)。"
    (if (is self.envs None)
        {}
        (env-heartbeat-part (.report self.envs) (current-platform))))

  (defn #^ tuple accept-warm [self #^ list rows]
    "heartbeat の返事の温める表の行 → この worker の root のキーの WarmEnv(キーは行ごとに 1 度だけ計算する — 計算は warm-env-of-row、
     sim の宿と同じ関数)。"
    (when (is self.envs None) (return #()))
    (setv out [])
    (for [row rows]
      (setv text (json.dumps (get row "runtimeEnv") :sort-keys True :ensure-ascii False))
      (when (not-in text self.warm-keys)
        (setv (get self.warm-keys text) (warm-env-of-row row (current-platform))))
      (.append out (get self.warm-keys text)))
    (tuple out))

  (defn #^ list report [self #^ tuple statuses]
    "状態の報告。終わった task には結果の file の中身(無ければ None = 結果なし)を添える。切り離した task には置かれた時の返事の行
     (blob を除く — 欄 task)を添える(形は status-report — sim の宿と同じ関数)。"
    (status-report statuses self.task-echo
                   ;; task の id は 1 度だけ読んで絞る(読み直すと型検査が None を絞れない — #1690)
                   (dfor s statuses
                         :setv task-id (finished-task-id s)
                         :if task-id
                         :setv result (/ self.task-dir (+ task-id ".result"))
                         task-id (if (.exists result) (.read-text result :encoding "ascii") None))))

  (defn #^ None tell [self #^ str outcome #^ str line]
    "heartbeat の結果の変わり目(初めて名乗れた・名乗れない理由が変わった・戻った)だけを stderr へ 1 行出すため。同じ結果の繰り返しは
     出さない。以前は断りも途絶も黙っていて、13 回目の本番の切り替えでは coordinator が heartbeat を 400 で断り続けたのに、worker の
     log は「起動します」の後に 32 分何も出さなかった(fence を越えると状態の file の note も空になる — 2026-09-29・#1005)。"
    (when (!= outcome self.told)
      (setv self.told outcome)
      (print line :file sys.stderr :flush True)))

  (defn #^ bool watching [self]
    "待ちの口を使えているか(heartbeat を拍から切り離してよいか): 待ちを使う link で、待ちの thread が生きていて、口を確かめ(最初の
     待ちが答えた)、404 でない。thread が思わぬ例外で止まっていれば、1 行出して毎拍の heartbeat に戻る。"
    (setv state self.watch-state)
    (when (and self.watcher (not (.is-alive self.watcher)) (not state.unsupported) (not (.is-set state.closing)))
      (.tell-watch state (+ "thread-dead:" state.failure)
                   (+ "worker: 待ちの thread が止まっている — 拍ごとの heartbeat に戻ります: " state.failure)))
    (bool (and self.watch-enabled (is-not self.watcher None) (.is-alive self.watcher) state.confirmed (not state.unsupported))))

  (defn #^ (| DesiredJobs DesiredUnreadable) poll [self]
    "拍ごとの ReadDesired に答えるため: heartbeat を送る拍(beat_policy.heartbeat-due)なら送り、それ以外は前の返事の desired を返す。"
    (setv silent-ms (int (* 1000 (- (time.monotonic) self.last-ok))))
    (if (heartbeat-due (.watching self) (is-not self.last-desired None) (.is-set self.watch-state.woken)
                       (!= self.statuses self.sent-statuses) silent-ms self.beat-interval-ms)
        (.beat self)
        self.last-desired))

  (defn #^ None start-watch [self]
    "返事に版を持つ coordinator へ、名指しの待ちを送り続ける背景の thread を 1 度だけ起こすため(待ちを使う link だけ)。"
    (when (and self.watch-enabled (is self.watcher None) (not self.watch-state.unsupported))
      (setv self.watcher (threading.Thread :target self.watch-loop :name (+ "watch-" self.name) :daemon True))
      (.start self.watcher)))

  (defn #^ None close [self]
    "worker の終わりに待ちの thread を止めるため(次の待ちを送らない — 送っている待ちは daemon の thread ごと捨てる)。"
    (.set self.watch-state.closing)
    (.set self.watch-state.beat-done)
    (when self.watcher
      (.join self.watcher 0.2)))

  (defn #^ WatchReading watch-once [self #^ int after #^ bool confirmed]
    "名指しの待ちを 1 回送り、答えを読むため(届かない・読めない返事も WatchReading の FAILED に畳む — thread を例外で落とさない)。"
    (try
      (setv response (.request self.watch-endpoint "GET" "/watch" :params (watch-params after self.name self.boot confirmed)))
      (watch-reading response.status-code (try (.json response) (except [ValueError] response.text)))
      (except [error Exception]
        (WatchReading :kind WatchKind.FAILED :detail (repr error)))))

  (defn #^ None watch-loop [self]
    "背景の thread の本体: 前の heartbeat の版の後の変化を待ち、「変わった」なら拍に heartbeat を送らせ(woken)、その heartbeat が
     版を進めるまで待ってから次を待つ。404 なら口が無いと記して抜ける(拍ごとの heartbeat に戻る)。届かなければ間を置いて送り直す。
     思わぬ例外は理由を記して抜ける(拍が watching で気づいて戻る — 黙って待ちを失わない)。"
    (setv state self.watch-state)
    (try
      (while (not (.is-set state.closing))
        (setv after (.after-now state))
        (if (is after None)
            (.wait state.beat-done WATCH-RETRY-SECONDS)
            (do (setv reading (.watch-once self after state.confirmed))
                (match reading.kind
                  WatchKind.UNSUPPORTED
                    (do (setv state.unsupported True)
                        (.tell-watch state "unsupported" "worker: coordinator に待ちの口が無い — 拍ごとの heartbeat を続けます")
                        (return None))
                  WatchKind.FAILED
                    (do (.tell-watch state (+ "failed:" reading.detail) (+ "worker: 待ちを送れない(送り直します): " reading.detail))
                        (.wait state.closing WATCH-RETRY-SECONDS))
                  WatchKind.CHANGED
                    (do (.confirm state)
                        (.clear state.beat-done)
                        (.set state.woken)
                        (.wait state.beat-done WAKE-HOLD-SECONDS))
                  WatchKind.UNCHANGED
                    (do (.confirm state)
                        (.advance state after reading.revision))))))
      (except [error Exception]
        (setv state.failure (repr error))
        (print (+ "worker: 待ちの thread が止まりました: " (repr error)) :file sys.stderr :flush True))))

  (defn #^ (| DesiredJobs DesiredUnreadable) beat [self]
    "heartbeat を 1 回送り、返事の job・task・温める表を desired にするため。届かなければ desired-when-unreachable(fence の判断)。"
    (setv sending self.statuses)
    ;; 送る前に起こしの印を下ろす(送った後に来た変化の印を消さない)。
    (.clear self.watch-state.woken)
    (try
      (setv response (.accepted self.endpoint (.request self.endpoint "POST" "/heartbeat"
        :json (| (heartbeat-body :name self.name :provides self.provides :exclusive self.exclusive :node self.node
                                 :capacity self.capacity :versions self.versions :statuses sending
                                 :endpoint self.endpoint.url :boot self.boot :boot-at self.boot-at :tools self.tools)
                 (.env-body self)))))
      (setv self.last-ok (time.monotonic))
      (setv body (.json response))
      (write-ready-file (os.environ.get "DOEFF_WORKER_READY_FILE") (bool (.get body "draining" False)))
      ;; 自己停止の時間は coordinator の ClusterTiming が持つ(移し替えの時間と組で決まる)。受け取った値に合わせる。
      (setv timing (.get body "timing"))
      (when (and timing (in "fence_ms" timing))
        (setv self.fence-ms (int (get timing "fence_ms"))))
      (setv self.last-jobs (tuple (gfor job (get body "jobs") (declared-job-spec job))))
      (setv self.last-tasks (.accept-tasks self (.get body "tasks" [])))
      (.accept-programs self (+ self.last-jobs self.last-tasks))
      (setv self.last-warm (.accept-warm self (.get body "warm" [])))
      ;; 返事を読み終えてから出す(返事の読みが毎回落ちる時に、名乗れた・名乗れないの 2 行を拍ごとに繰り返さない)。
      (.tell self "" (.format "worker: coordinator {} に名乗りました" self.endpoint.url))
      ;; 次の拍の判断の材料(#1933): 届けた状態の報告・送る間隔・待ちの after(返事の版 — 無ければ旧い coordinator)。
      (setv self.sent-statuses sending
            self.beat-interval-ms (beat-interval-ms timing self.task-echo)
            self.last-desired (DesiredJobs (+ self.last-jobs self.last-tasks) :warm self.last-warm))
      (.beaten self.watch-state (reply-revision body))
      (when (is-not (reply-revision body) None)
        (.start-watch self))
      self.last-desired
      (except [error Exception]
        (.tell self (repr error) (+ "worker: coordinator に名乗れない: " (repr error)))
        ;; 届かない間は毎拍送り直す(前の desired を使い続けない — fence の判断を毎拍する)。
        (setv self.last-desired None)
        (desired-when-unreachable (int (* 1000 (- (time.monotonic) self.last-ok))) self.fence-ms
                                  (+ self.last-jobs self.last-tasks) self.last-warm (repr error))))))


;; --- heartbeat の形(本番の CoordinatorLink と手元の sim-cluster の偽の宿 local.hy が同じ関数を使う — 本文を写さない)-------------

(deff env-report [#^ tuple views #^ str capacity]  ; defk にできない: worker の I/O の道具(EnvStore)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: views tuple) (: capacity str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "実行環境の root の観測(CodeView — 鍵が env- で始まる物だけを読む)と disk の条件を、heartbeat で名乗る root の姿(準備済み・準備中・
   失敗のキーを env- を外して・disk の条件)にするため。"
  (let [roots (lfor v views :if (.startswith v.revision ENV-KEY-PREFIX) v)
        bare (fn [k] (cut k (len ENV-KEY-PREFIX) None))]
    {"ready" (sorted (gfor v roots :if (= v.state CodeState.READY) (bare v.revision)))
     "preparing" (sorted (gfor v roots :if (= v.state CodeState.PREPARING) (bare v.revision)))
     "failed" (lfor v roots :if (and (= v.state CodeState.FAILED) (is-not v.failure None))
                    {"key" (bare v.revision) "kind" v.failure.kind.value "detail" v.failure.detail
                     "retryable" v.failure.retryable})
     "capacity" capacity}))


(deff env-heartbeat-part [#^ dict report #^ str platform]  ; defk にできない: worker の I/O の道具(CoordinatorLink)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: report dict) (: platform str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "root の姿(env-report)を heartbeat の本文に足す欄(platform・envs・envCapacity)にするため。"
  {"platform" platform
   "envs" {"ready" (get report "ready") "preparing" (get report "preparing") "failed" (get report "failed")}
   "envCapacity" (get report "capacity")})


(deff warm-env-of-row [#^ dict row #^ str platform]  ; defk にできない: worker の I/O の道具(CoordinatorLink)と sim の宿が同じ判断で返事を読む
  {:pre [(: row dict) (: platform str)] :post [(: % WarmEnv)] :tags {:context "doeff-cluster" :role "judgment"}}
  "heartbeat の返事の温める表の行 1 つを、この worker の root のキー(platform で計算した env のキーに env- を付けた物)の WarmEnv に
   するため。"
  (WarmEnv :key (+ ENV-KEY-PREFIX (run (env-key (run (runtime-env-of-json (get row "runtimeEnv"))) platform)))
           :runtime-env (json.dumps (get row "runtimeEnv") :sort-keys True :ensure-ascii False)))


(deff heartbeat-body [* #^ str name #^ tuple provides #^ tuple exclusive #^ str node #^ int capacity #^ dict versions
                      #^ list statuses #^ str endpoint #^ str boot #^ int boot-at #^ dict tools]  ; defk にできない: worker の I/O の道具(CoordinatorLink)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: name str) (: provides tuple) (: exclusive tuple) (: node str) (: capacity int) (: versions dict) (: statuses list)
         (: endpoint str) (: boot str) (: boot-at int) (: tools dict)] :post [(: % dict)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "POST /heartbeat の本文(生存・能力・版・状態の報告・世代)を作るため。実行環境の root の名乗り(env-body)は本番の worker だけが足す。"
  {"name" name "provides" (list provides) "exclusive" (list exclusive) "node" node "capacity" capacity "versions" versions
   "statuses" statuses "endpoint" endpoint "boot" boot "bootAt" boot-at
   "format" PROTOCOL-FORMAT
   "tools" tools})


(deff finished-task-id [s]  ; defk にできない: worker の I/O の道具(CoordinatorLink)と sim の宿が状態の行を読む純粋な判断
  {:pre [(: s JobStatus)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "終わった task の状態の行なら task の id(結果を添える相手)、それ以外は None — 結果の file を読む・世界の結果を引く所を 1 つにするため。"
  (if (and (.startswith s.name "task/") (= s.phase JobPhase.FINISHED)) (cut s.name 5 None) None))


(deff status-report [#^ tuple statuses #^ dict task-echo #^ dict results]  ; defk にできない: worker の I/O の道具(CoordinatorLink)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: statuses tuple) (: task-echo dict) (: results dict)] :post [(: % list)] :tags {:context "doeff-cluster" :role "protocol"}}
  "状態の行の列を heartbeat の statuses にするため。終わった task には結果(results の task の id → 詰めた結果の文字列 か None =
   結果なし)を、切り離した task には置かれた時の返事の行(task-echo の id → 行 — 欄 task)を添える。"
  (lfor s statuses
    :setv row (status-row s)
    :setv echo (if (.startswith s.name "task/") (.get task-echo (cut s.name 5 None)) None)
    :setv row (if (is echo None) row (| row {"task" echo}))
    :setv done (finished-task-id s)
    (if (is done None) row (| row {"result" (.get results done)}))))


(deff desired-when-unreachable [#^ int silent-ms #^ int fence-ms #^ tuple last #^ tuple warm #^ str reason]  ; defk にできない: worker の I/O の道具(CoordinatorLink)と sim の宿が同じ判断を使う
  {:pre [(: silent-ms int) (: fence-ms int) (: last tuple) (: warm tuple) (: reason str)] :post [(: % (| DesiredJobs DesiredUnreadable))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator に届かなかった拍の宣言を決めるため。連絡が fence を超えて途絶えたら、lease を持たない job と task を止める(coordinator は
   後で他へ移す)。書き手(入れ替えを宣言した job)と切り離した task は動かし続ける — 書きは lease の柵だけが守り、切り離した task の
   lease はこの worker の heartbeat が延ばす(worker_policy.kept-when-cut-off・2026-09-25)。fence の内なら「読めない」(直前の宣言を
   使い続ける)。"
  (if (> silent-ms fence-ms)
      (DesiredJobs (kept-when-cut-off last) :warm warm)
      (DesiredUnreadable f"coordinator に届かない({silent-ms} ms): {reason}")))

(defn #^ None write-ready-file [#^ (| str None) path #^ bool draining]
  "readinessProbe が sh で読む file(2026-09-25)へ、heartbeat が届いた拍ごとに「ready」か「draining」を書く(mtime = 最後に届いた時刻)。
   probe は中身が ready で新しい時だけ Ready — hy を起こさない(込んだ node で 10 秒の timeout を越えて両方の Pod が NotReady に
   なり、DaemonSet が 2 台を同時に消した実弾)。file は Pod の中(container の /tmp)— 同じ node の前の Pod の物と混ざらない。"
  (when path
    (setv tmp (Path (+ path ".tmp")))
    (.write-text tmp (if draining "draining\n" "ready\n") :encoding "utf-8")
    (os.replace tmp path)))


(defhandler coordinator-desired [#^ CoordinatorLink link]
  (ReadDesired [] (resume (.poll link))))

(defn #^ dict status-row [#^ JobStatus s]
  {"name" s.name "phase" s.phase.value "desiredRevision" s.desired-revision
   "runningRevision" s.running-revision "pid" s.pid "attempts" s.attempts "detail" s.detail
   ;; 動いている process の世代(coordinator の readiness と計器はこれと一致する報告だけを数える)。
   "instance" s.instance "specHash" s.spec-hash "placement" s.placement "retiredFrom" s.retired-from
   ;; 実行環境の準備の失敗(ENV-FAILED の行だけ): coordinator が置き直すか・答えの型を決める。
   #** (if (is s.failure None) {} {"failureKind" s.failure.kind.value "retryable" s.failure.retryable})
   ;; 入口の検めの姿(検めが通っていない間だけ — 2026-09-27): 状態・今の検めの経過の秒・回数・直前の失敗の理由。
   #** (if (is s.probe None) {} {"probe" {"state" s.probe.state "elapsedSeconds" s.probe.elapsed-seconds
                                          "attempts" s.probe.attempts "lastFailure" s.probe.last-failure}})})

(defhandler status-to-coordinator [#^ CoordinatorLink link]
  ;; 状態は次の heartbeat で送る。file にも書くので、外側の status-file へ渡す。
  (PublishStatus [statuses note]
    (setv link.statuses (.report link statuses))
    (<- (PublishStatus statuses note))
    (resume None)))

(defn #^ None release-leases [#^ CoordinatorLink link #^ str job #^ str instance]
  "終わった process(job の名 job・世代の名 instance)が持っていた lease を返す。token の頭は子が名乗った担い手と同じ定義
   (lease_rules.lease-holder と holder-tokens-prefix — <job>/<世代の名>/)。外すのは coordinator(POST /leases/<名> の drop —
   2026-09-25)。drop の口を持たない旧い coordinator には、盤の行の compare-and-set で外す(以前の形)。届かない・競合が続く時は
   あきらめる(期限で切れる)。"
  (setv prefix (holder-tokens-prefix (lease-holder job instance)))
  (try
    (setv response (.request link.endpoint "GET" "/board" :params {"prefix" SEMAPHORE-PREFIX}))
    (.raise-for-status response)
    (for [#(key row) (.items (.json response))]
      (when (is (drop-holders row prefix) None) (continue))
      (setv name (cut key (len SEMAPHORE-PREFIX) None))
      (setv dropped (.request link.endpoint "POST" (+ "/leases/" (url-quote name :safe ""))
                              :json {"op" "drop" "token" prefix}))
      (cond
        (< dropped.status-code 300)
          (print (.format "worker: 終わった process {} の lease を返しました({})" instance key) :file sys.stderr :flush True)
        (= dropped.status-code 404) (release-by-board link key row prefix instance)
        True (print (.format "worker: lease を返せなかった({}・{}): {}" instance key dropped.status-code) :file sys.stderr :flush True)))
    (except [error Exception]
      (print (.format "worker: lease を返せなかった({}): {!r}" instance error) :file sys.stderr :flush True))))

(defn #^ None release-by-board [#^ CoordinatorLink link #^ str key #^ dict row #^ str prefix #^ str instance]
  "旧い coordinator(/leases の口が無い)へ: 盤の行の compare-and-set で担い手を外す。"
  (for [attempt (range 3)]
    (setv updated (drop-holders row prefix))
    (when (is updated None) (break))
    (setv put (.request link.endpoint "PUT" (+ "/board/" key) :json {"value" updated "expect" row}))
    (when (< put.status-code 300)
      (print (.format "worker: 終わった process {} の lease を返しました({})" instance key) :file sys.stderr :flush True)
      (break))
    (when (!= put.status-code 409) (break))
    ;; 競合(延長と重なった)は読み直す。
    (setv again (.request link.endpoint "GET" "/board" :params {"prefix" key}))
    (setv row (.get (.json again) key))
    (when (is row None) (break))))

(defhandler lease-release-coordinator [#^ CoordinatorLink link]
  (ReleaseLeases [job instance] (release-leases link job instance) (resume None)))

(defclass StopState []
  "worker の止めの印(main の信号の handler が立て、stop-flag が WorkerStopRequested に答える)。"
  (defn #^ None __init__ [self] (setv self.requested False)))

(defhandler stop-flag [#^ StopState state]
  (WorkerStopRequested [] (resume state.requested)))
