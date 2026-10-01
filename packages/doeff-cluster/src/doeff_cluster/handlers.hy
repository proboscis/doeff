;; worker の実 I/O。coordinator との連絡・コードの展開(git archive)・子 process・状態の file・停止信号。
;; どれもループを塞がない: 展開と子 process は Popen で起動し、結果は ObserveWorld で観測する。
(require doeff-hy.macros [defhandler defk deff <- val])
(require doeff-hy.record [defrecord])
(import json os re shutil subprocess sys threading time uuid)
(import httpx)
(import enum [Enum])
(import typing [IO])
(import dataclasses [dataclass replace])
(import pathlib [Path])
(import urllib.parse [quote :as url-quote])
(import doeff_cluster.foundation.coordinator_http [CoordinatorEndpoint REPLY-SECONDS])
(import doeff_cluster.worker.core.beat_policy [WatchKind WatchReading beat-interval-ms heartbeat-due watch-params watch-reading reply-revision
                      WATCH-RETRY-SECONDS WAKE-HOLD-SECONDS])
(import doeff [run])
(import doeff_cluster.shared.intent.protocol [PROTOCOL-FORMAT])
(import doeff_cluster.shared.core.capabilities [environ-pairs])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import .job_context [process-context-environ])
(import doeff_cluster.shared.intent.remote_model [program-sha])
(import doeff_cluster.shared.intent.runtime_env_model [runtime-env-of-json env-key current-platform EnvFailure EnvFailureKind])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.worker.core.env_upkeep [RootInfo PrepareLimits sweep-choice prepare-overdue env-capacity SWEEP-FLOOR-RATIO WHEEL-UNUSED-SECONDS])
(import doeff_cluster.shared.intent.semaphore_model [SEMAPHORE-PREFIX])
(import doeff_cluster.shared.core.lease_rules [drop-holders lease-holder holder-tokens-prefix])
(import doeff_cluster.worker.protocol.heartbeat [env-report env-heartbeat-part heartbeat-body status-report status-row])
(import doeff_cluster.worker.core.heartbeat_rules [warm-env-of-row finished-task-id desired-when-unreachable])
(import doeff_cluster.foundation.ready_file [write-ready-file])
(import doeff_cluster.worker.core.launch [program-file])
(import doeff_cluster.worker.intent.worker_model [ObserveCode ObserveProcesses ObserveProbes CodeState CodeView ProcessView WorldView StopStage ProbeState ProbeView
  DesiredJobs DesiredUnreadable ReadDesired ObserveWorld WorkerStopRequested PublishStatus JobStatus EnvDisk WarmEnv
  PrepareEnv SweepEnvs ForgetProbes StartJob SignalJob ReapJob RetireJob ReleaseLeases ProbeEntry
] doeff_cluster.shared.intent.job_model [JobSpec JobPhase] doeff_cluster.shared.core.job_rules [spec-hash] doeff_cluster.worker.core.worker_rules [probe-args probe-refusal ENV-KEY-PREFIX])

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


(defhandler local-host [#^ (| EnvStore None) [envs None]]
  ;; 引数に残す理由: worker の process が持つ I/O の資源(準備の process の表)で、同じ組が観測と action の両方に答える。
  ;; envs = 実行環境の root の準備(None = 実行環境の job を扱わない worker — PrepareEnv は断る)。版ごとのコードの木は
  ;; worker/protocol/code_store(#2466)、job の子 process は worker/protocol/process_host(#2464)、入口の検めは
  ;; worker/protocol/probes(#2465)の言い換え(外側に置く)が答え、観測は ObserveCode・ObserveProcesses・ObserveProbes で問う。
  (ObserveWorld []
    (<- codes tuple (ObserveCode))
    (<- processes tuple (ObserveProcesses))
    (<- probed tuple (ObserveProbes))
    (resume (WorldView (+ codes (if (is envs None) #() (.observe envs))) processes probed
                       :env-disk (if (is envs None) None (.disk-view envs)))))
  (PrepareEnv [key runtime-env warm]
    (when (is envs None)
      (raise (RuntimeError "この worker は実行環境の job を扱えない(EnvStore が無い)")))
    (.start envs key runtime-env :warm warm)
    (resume None))
  (SweepEnvs [pinned]
    (when (is-not envs None) (.sweep envs pinned))
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


(defhandler coordinator-desired [#^ CoordinatorLink link]
  (ReadDesired [] (resume (.poll link))))


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


