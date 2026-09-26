;; worker の実 I/O。宣言の file・コードの展開(git archive)・子 process・状態の file・停止信号。
;; どれもループを塞がない: 展開と子 process は Popen で起動し、結果は ObserveWorld で観測する。
(require doeff-hy.macros [defhandler <-])
(import json os re shutil signal subprocess sys time uuid)
(import dataclasses [replace])
(import pathlib [Path])
(import urllib.parse [quote :as url-quote])
(import .coordinator_http [CoordinatorEndpoint REPLY-SECONDS])
(import .code_prepare [MARKER MARKER-FORMAT marker-problem scan])
(import doeff [run])
(import .cluster_model [PROTOCOL-FORMAT])
(import .runtime_env_model [runtime-env-of-json env-key current-platform EnvFailure EnvFailureKind])
(import .env_prepare [ENV-MARKER])
(import .env_upkeep [RootInfo PrepareLimits sweep-choice prepare-overdue env-capacity SWEEP-FLOOR-RATIO WHEEL-UNUSED-SECONDS])
(import .semaphore_model [SEMAPHORE-PREFIX drop-holders])
(import .worker_policy [kept-when-cut-off])
(import .worker_model [JobSpec CodeState CodeView ProcessView WorldView StopStage ProbeState ProbeView
  DesiredJobs DesiredUnreadable ReadDesired ObserveWorld WorkerStopRequested PublishStatus JobPhase EnvDisk WarmEnv
  PrepareCode PrepareEnv SweepEnvs StartJob SignalJob ReapJob RetireJob ReleaseLeases ProbeEntry spec-hash split-code-key probe-args CodeLayout
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
  "宣言の file の 1 行・heartbeat の返事の job 1 本 → worker が起動する形(runtimeEnv を持つ service は env の root で起こす)。"
  (setv #(revision runtime key) (env-placement (.get job "runtimeEnv") (get job "revision")))
  (JobSpec (get job "name") (get job "entry") (tuple (.get job "args" [])) revision
           :once (.get job "once" False) :placement (.get job "placement")
           :base (.get job "base") :handoff (bool (.get job "handoff" False))
           :ready-instance (.get job "readyInstance") :runtime-env runtime :env-key key
           ;; 入れ替えの諦め(coordinator の期限 — 返事の handoff の job だけが持つ・無ければ偽)。
           :handoff-abandoned (bool (.get job "handoffAbandoned" False))))


(defn #^ (| DesiredJobs DesiredUnreadable) parse-desired [#^ str text]
  (try
    (setv data (json.loads text))
    (DesiredJobs (tuple (gfor job (get data "jobs") (declared-job-spec job))))
    (except [error Exception]
      (DesiredUnreadable f"宣言を読めません: {(repr error)}"))))

(defhandler desired-file [#^ str path]
  (ReadDesired []
    (resume (try
      (parse-desired (.read-text (Path path) :encoding "utf-8"))
      (except [error OSError] (DesiredUnreadable f"宣言の file を開けません: {(repr error)}"))))))

(setv TOOL (str (/ (. (.resolve (Path __file__)) parent) "code_prepare.hy")))


(defclass CodeStore []
  "revision ごとに repo のコードを cache へ展開する。展開済みの dir は再利用する。"
  "hy-command = 焼きに使う hy(None なら bytecode の準備を省く)。"
  "完成品 = cache の直下の、完成の印(code_prepare の MARKER)が検めを通る dir。印の無い・検めの通らない dir は"
  "完成品として公開せず、次にその版を求められた時に脇へ退けて作り直す。"
  "tool = 焼く道具の file(既定は worker 自身のコードの code_prepare.hy。準備する版の木の物は使わない)。"
  "layout = 業務の repo の木の形(import の根・重ねる dir — worker_model.CodeLayout)。"
  (defn __init__ [self #^ str repo #^ str cache #^ (| str None) hy-command [tool TOOL] #^ CodeLayout [layout (CodeLayout)]]
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
          pycs (if (and want-bytecode (is-not text None)) (len (get (scan (str entry)) 1)) 0)
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

  (defn start [self #^ str revision]
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
    ;; revision = worker_model.code-key。「<base>~<重ねる commit>」なら base の木の重ねる dir(layout.overlay-path)を重ねる commit の
    ;; 物に差し替える(2026-09-24 — 業務コードは本番の commit・service の包みは宣言の commit)。前の木から引き継ぐ時の「変わった file」は、
    ;; 土台と重ねた dir を別々に比べた和(どちらの木も同じ規則で分けるので、重ねない木どうしなら従来の diff と同じ)。
    ;; 重ねる dir を持たない layout で重ねる木を求められたら、準備は失敗する(黙って base の木だけにしない)。
    (setv repo self.repo
          #(base overlay) (split-code-key revision)
          layered (!= base overlay)
          overlay-path self.layout.overlay-path
          tool (+ f"PYTHONDONTWRITEBYTECODE=1 \"{self.hy-command}\" \"{self.tool}\" \"$T\" --revision \"{revision}\""
                  f" --import-roots \"{(.roots-arg self.layout)}\"")
          prepare (cond
            (not self.hy-command)
              (+ f"printf '{{\"format\": {MARKER-FORMAT}, \"revision\": \"%s\", \"bytecode\": false}}\\n' "
                 f"\"{revision}\" > \"$T/{MARKER}\"\n")
            (is previous None) f"{tool}\n"
            (not overlay-path)
              (+ f"git -C \"{repo}\" diff --name-only \"{(get (split-code-key previous.name) 0)}\" \"{base}\" > \"$T.changed\"\n"
                 f"{tool} --from \"{previous}\" --changed \"$T.changed\"\n")
            True (do (setv #(pbase poverlay) (split-code-key previous.name))
                     (+ f"git -C \"{repo}\" diff --name-only \"{pbase}\" \"{base}\" -- . ':(exclude){overlay-path}' > \"$T.changed\"\n"
                        f"git -C \"{repo}\" diff --name-only \"{poverlay}\" \"{overlay}\" -- \"{overlay-path}\" >> \"$T.changed\"\n"
                        f"{tool} --from \"{previous}\" --changed \"$T.changed\"\n"))))
    (+ "set -eu\n"
       "if [ -n \"$B\" ]; then rm -rf \"$B\"; fi\n"
       "rm -rf \"$T\" \"$T.tar\" \"$T.changed\"\n"
       "mkdir -p \"$T\"\n"
       ;; 手元に無い版なら先に fetch する(Pod の mirror は起動時の版しか持たない)。
       f"if ! git -C \"{repo}\" cat-file -e \"{base}^{{commit}}\" 2>/dev/null || ! git -C \"{repo}\" cat-file -e \"{overlay}^{{commit}}\" 2>/dev/null; then\n"
       f"  git -C \"{repo}\" fetch -q origin '+refs/heads/*:refs/heads/*'\n"
       "fi\n"
       f"git -C \"{repo}\" archive --format=tar -o \"$T.tar\" \"{base}\"\n"
       "tar -x -C \"$T\" -f \"$T.tar\"\n"
       "rm -f \"$T.tar\"\n"
       (cond
         (not layered) ""
         (not overlay-path) "echo \"重ねる木を求められたが、この worker の layout に重ねる dir が無い\" >&2\nexit 1\n"
         True (+ f"rm -rf \"$T/{overlay-path}\"\n"
                 f"git -C \"{repo}\" archive --format=tar -o \"$T.tar\" \"{overlay}\" \"{overlay-path}\"\n"
                 "tar -x -C \"$T\" -f \"$T.tar\"\n"
                 "rm -f \"$T.tar\"\n"))
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
  (defn __init__ [self process #^ float started #^ Path result #^ Path progress #^ bool warm #^ bool cold]
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
  (defn __init__ [self #^ str state-dir #^ str hy-command #^ str [repo-keys ""] #^ str [uv "uv"] #^ int [min-free-bytes 0]
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

  (defn start [self #^ str key #^ str runtime-env #^ bool [warm False]]
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

  (defn launch-waiting [self]
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
              project (if owned
                          (let [declared (get marker "env") pr (get declared "project")]
                            (.format "{}:{}" (next (gfor r (get declared "repos") :if (= (get r "name") (get pr "repo")) (get r "url")) "")
                                     (get pr "path")))
                          ""))
        (.append out (RootInfo :key (+ ENV-KEY-PREFIX entry.name) :project project :made-ms (int (* 1000 made))
                               :last-used-ms (int (* 1000 used)) :bytes (if owned (tree-bytes entry) 0) :owned owned))))
    out)

  (defn sweep [self #^ frozenset pinned]
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
    ;; 7 日使われない native の wheel(使うたびに dir の時刻を進める — env_handlers の EnsureNativeWheel)。
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
     disk の条件。"
    (setv views (if (is self.views None) (.observe self) self.views)
          bare (fn [k] (cut k (len ENV-KEY-PREFIX) None))
          free (. (.disk-view self) free))
    {"ready" (sorted (gfor v views :if (= v.state CodeState.READY) (bare v.revision)))
     "preparing" (sorted (gfor v views :if (= v.state CodeState.PREPARING) (bare v.revision)))
     "failed" (lfor v views :if (and (= v.state CodeState.FAILED) (is-not v.failure None))
                    {"key" (bare v.revision) "kind" v.failure.kind.value "detail" v.failure.detail
                     "retryable" v.failure.retryable})
     "capacity" (run (env-capacity free self.min-free-bytes))}))


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


(setv PROBE-SECONDS 60)   ; 入口の検め 1 回の時間の上限(業務の module の import に掛かる時間の十分上)
(setv PROBE-DETAIL-CHARS 480)


(defn #^ str probe-reason [#^ int code #^ str stderr]
  "検めの process の終了 → 理由の 1 行(stderr の最後の空でない行。無ければ終了の番号)。"
  (setv lines (lfor line (.splitlines stderr) :if (.strip line) (.strip line)))
  (cut (if lines (get lines -1) f"入口の検めが終了 {code} で終わった(理由の出力なし)") 0 PROBE-DETAIL-CHARS))


;; 検めの本体(木の中の道具に依らない — 木の job_entry に probe の口が無い古い commit も同じく検められる)。引数 = 検める import path の列
;; (「module」か「module:attr」)。各々を import し attr の在否を見る。最初に失敗した物の理由を stderr の 1 行にして 1 で終わる。
(setv PROBE-PROGRAM (.join "\n" [
  "(import importlib sys)"
  "(for [path (cut sys.argv 1 None)]"
  "  (setv #(module _ attr) (.partition path \":\"))"
  "  (try (setv m (importlib.import-module module)) (when attr (getattr m attr))"
  "    (except [e Exception]"
  "      (print (.format \"{} を読み込めない: {}: {}\" path (. (type e) __name__) (.join \" \" (.split (str e)))) :file sys.stderr :flush True)"
  "      (sys.exit 1))))"]))


(defn #^ list probe-targets [#^ JobSpec spec]
  "検める import path の列: 入口の module(spec.entry)と、probe-args の factory と env(空は除く)。"
  (setv args (list (probe-args spec)))
  (+ [spec.entry] (lfor flag ["--factory" "--env"] :if (in flag args) :setv v (get args (+ (.index args flag) 1)) :if v v)))


(defclass ProbeStore []
  "入口の検め(2026-09-25): service の job の木で、worker の実行環境が入口(factory と env)を読み込めるかを子 process で試す。
   CodeStore と同じく Popen で起動し、結果は observe で拾う(ループを塞がない)。実行は ProcessHost と同じ hy・同じ PYTHONPATH
   (layout の import の根)・cwd = 木。実行環境の job(spec.runtime-env — 2026-09-26)は子と同じ起こし方で検める: root の venv の
   `uv run --no-sync --frozen --project <root の project> hy -c …`・環境変数は子と同じ許可表・PYTHONPATH を置かない・cwd = 空の dir
   (probe-dir)。worker の venv で検めると、env の root に無い module を worker の venv が読めて誤って通る。
   timeout-seconds を越えた検めは止めて FAILED(理由 = 時間切れ)。結果の鍵は spec-hash(同じ spec の検めは撃ち直されるまで答えを使い回す)。"
  (defn #^ None __init__ [self #^ str hy-command #^ (| int float) [timeout-seconds PROBE-SECONDS] #^ CodeLayout [layout (CodeLayout)]
                          #^ str [uv "uv"] #^ (| str None) [probe-dir None]]
    (setv self.hy-command hy-command self.timeout-seconds timeout-seconds self.layout layout self.pending {} self.done {}
          self.uv uv self.probe-dir (Path (or probe-dir "probe"))))

  (defn #^ list command [self #^ ProbeEntry action]
    "検めの子の #(argv cwd 環境変数)— 実行環境の job は子と同じ root の venv、それ以外は worker の hy と木の PYTHONPATH。"
    (setv targets (probe-targets action.spec))
    (if action.spec.runtime-env
        (do (setv declared (json.loads action.spec.runtime-env))
            (.mkdir self.probe-dir :parents True :exist-ok True)
            [[self.uv "run" "--no-sync" "--frozen" "--project" (env-project-dir action.code-path declared)
              "hy" "-c" PROBE-PROGRAM #* targets]
             (str self.probe-dir)
             (child-environment (dict os.environ) {}
                                (dfor v (.get declared "envVars" []) (get v "name") (get v "value"))
                                {"PYTHONDONTWRITEBYTECODE" "1"})])
        [[self.hy-command "-c" PROBE-PROGRAM #* targets]
         action.code-path
         (| (dict os.environ) {"PYTHONPATH" (.pythonpath self.layout action.code-path) "PYTHONDONTWRITEBYTECODE" "1"})]))

  (defn #^ None start [self #^ ProbeEntry action]
    (setv key (spec-hash action.spec))
    (when (in key self.pending) (return))
    (.pop self.done key None)
    (setv #(argv cwd env) (.command self action))
    (setv (get self.pending key)
      #((subprocess.Popen argv :cwd cwd :env env
          :stdin subprocess.DEVNULL :stdout subprocess.DEVNULL :stderr subprocess.PIPE)
        (time.monotonic))))

  (defn #^ tuple observe [self]
    (setv now-ms (int (* (time.time) 1000)))
    (for [#(key #(process started)) (list (.items self.pending))]
      (setv code (.poll process))
      (cond
        (is-not code None)
          (do (del (get self.pending key))
              (setv stderr (.decode (.read process.stderr) "utf-8" "replace"))
              (setv (get self.done key)
                    (if (= code 0)
                        (ProbeView key ProbeState.PASSED)
                        (ProbeView key ProbeState.FAILED :detail (probe-reason code stderr) :failed-ms now-ms))))
        (> (- (time.monotonic) started) self.timeout-seconds)
          (do (.kill process)
              (.wait process)
              (del (get self.pending key))
              (setv (get self.done key)
                    (ProbeView key ProbeState.FAILED :failed-ms now-ms
                               :detail (.format "入口の検めが {} 秒で終わらない(止めた)" self.timeout-seconds))))))
    (tuple (+ (lfor key self.pending (ProbeView key ProbeState.RUNNING)) (list (.values self.done))))))


(defclass ProcessHost []
  "job ごとに子 process を 1 本、専用の process group で起動する。extra-env = 子へ渡す worker の文脈(名前・coordinator)。
   layout = 業務の repo の木の形(子の PYTHONPATH — worker_model.CodeLayout)。
   実行環境の job(spec.runtime-env)は、env の root の venv で `uv run --no-sync --frozen --project <root の project> hy -m …` として
   起こす(PYTHONPATH を置かない・子の環境変数は許可表で組む・cwd = 空の作業 dir <jobs-dir>/<job の名>)。uv = uv の命令。"
  (defn __init__ [self #^ str log-dir #^ str hy-command [extra-env None] #^ CodeLayout [layout (CodeLayout)]
                  #^ str [uv "uv"] #^ (| str None) [jobs-dir None]]
    (setv self.log-dir (Path log-dir) self.hy-command hy-command self.table {} self.extra-env (or extra-env {})
          self.layout layout self.uv uv
          self.jobs-dir (if jobs-dir (Path jobs-dir) (/ (. (Path log-dir) parent) "jobs"))))

  (defn #^ Path work-dir [self #^ str name]
    "実行環境の job の子の cwd(job の名ごとの空の dir)。"
    (/ self.jobs-dir (.replace name "/" "_")))

  (defn #^ tuple launch [self #^ JobSpec spec #^ str code-path #^ str instance #^ int attempt]
    "子の #(argv cwd 環境変数)。実行環境の job は root の venv の uv run、それ以外は今の形(木の PYTHONPATH)。"
    (setv worker-env {"DOEFF_WORKER_JOB" spec.name
                      "DOEFF_WORKER_REVISION" spec.revision
                      "DOEFF_WORKER_ATTEMPT" (str attempt)
                      "DOEFF_WORKER_INSTANCE" instance
                      "DOEFF_WORKER_SPEC_HASH" (spec-hash spec)
                      "DOEFF_WORKER_PLACEMENT" (if (is spec.placement None) "" (str spec.placement))
                      "DOEFF_WORKER_PID" (str (os.getpid))})
    (if spec.runtime-env
        (do (setv declared (json.loads spec.runtime-env)
                  work (self.work-dir spec.name))
            ;; 使った印(掃除は最後に使った時刻の古い root から消す — env_upkeep.sweep-choice)。
            (.touch (/ (Path code-path) ".last-used"))
            (when (.exists work) (shutil.rmtree work))
            (.mkdir work :parents True)
            #([sys.executable "-m" "doeff_cluster.shim" "10" "--" self.uv "run" "--no-sync" "--frozen"
               "--project" (env-project-dir code-path declared) "hy" "-m" spec.entry #* spec.args]
              (str work)
              (child-environment (dict os.environ) self.extra-env
                                 (dfor v (.get declared "envVars" []) (get v "name") (get v "value"))
                                 (| worker-env {"DOEFF_RUNTIME_ENV" spec.runtime-env
                                                "DOEFF_RUNTIME_ENV_KEY" spec.env-key}))))
        #([sys.executable "-m" "doeff_cluster.shim" "10" "--" self.hy-command "-m" spec.entry #* spec.args]
          code-path
          (| (dict os.environ) self.extra-env {"PYTHONPATH" (.pythonpath self.layout code-path)} worker-env))))

  (defn start [self #^ StartJob action]
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

  (defn retire [self #^ RetireJob action]
    "入れ替え: 動いている process を止めずに名から外す(表の鍵と観測の名を new-name へ移す)。同じ名で新しい process を起こせる。"
    (setv entry (.get self.table action.name))
    (when (and entry (= (. (get entry 1) pid) action.pid))
      (del (get self.table action.name))
      (setv (get self.table action.new-name)
            #((get entry 0) (replace (get entry 1) :name action.new-name :retired-from action.name)))))

  (defn signal [self #^ SignalJob action]
    (setv sig (if (= action.stage StopStage.TERM) signal.SIGTERM signal.SIGKILL))
    ;; 孫 process まで届くよう process group へ送る(setsid で抜けた孫は届かない)。
    (try (os.killpg action.pid sig) (except [ProcessLookupError] None)))

  (defn reap [self #^ ReapJob action]
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
    (tuple (gfor #(process view) (.values self.table)
      (do (setv code (.poll process))
          (if (is code None) view
              (replace view :exit-code code)))))))

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

(defhandler status-file [#^ str path #^ CodeStore codes]
  (PublishStatus [statuses note]
    (setv target (Path path) tmp (Path f"{path}.tmp"))
    (.mkdir target.parent :parents True :exist-ok True)
    (.write-text tmp (json.dumps (status-json statuses note codes.timings) :ensure-ascii False :indent 1)
                 :encoding "utf-8")
    (os.replace tmp target)
    (resume None)))

(setv JOB-ENTRY "doeff_cluster.job_entry")


(defn #^ JobSpec task-spec [#^ dict task #^ Path task-dir]
  "coordinator が割り当てた task 1 本 → 1 度だけ走らせる job。blob と結果はこの worker の file(名前は task の id で決まる)。
   実行環境の task(runtimeEnv を持つ)は、env のキー(この worker の platform で計算)を root の置き場の鍵にする(env-placement)。"
  (setv id (get task "id"))
  (setv #(revision runtime key) (env-placement (.get task "runtimeEnv") (get task "revision")))
  (JobSpec (+ "task/" id) JOB-ENTRY
           #("task" "--blob" (str (/ task-dir f"{id}.blob")) "--result" (str (/ task-dir f"{id}.result"))
             "--env" (get task "env") "--versions" (json.dumps (get task "versions") :sort-keys True))
           revision :once True :detached (bool (.get task "detached" False)) :runtime-env runtime :env-key key))


(defclass CoordinatorLink []
  "coordinator との連絡。heartbeat で生存・版・状態(終わった task の結果を含む)を送り、自分に割り当てられた job と task を受け取る。
   task の blob は task-dir の file に置き、宣言から外れた task の file は消す(この worker が書いた物だけ)。"
  (defn __init__ [self #^ str url #^ str name #^ dict labels #^ int capacity #^ int fence-ms
                  [task-dir None] [versions None] [transport None] #^ (| dict None) [tools None] #^ (| EnvStore None) [envs None]]
    ;; tools = この worker が名乗る道具(名 → 版 — 実行環境の宣言の tools と照らして置き先を選ぶ)。
    ;; envs = 実行環境の root の置き場(在れば、準備済み・準備中・失敗の root と disk の条件を heartbeat で名乗り、温める表を受ける)。
    (setv self.name name self.labels labels self.capacity capacity self.tools (or tools {}) self.envs envs
          self.last-warm #() self.warm-keys {}
          self.fence-ms fence-ms self.statuses []
          ;; 宛先は `,` で並べた物(前ほど優先)。毎拍やり直すので一巡以上は送り直さない(拍を塞がない)・接続は使い回す。
          ;; 自己停止を数える last-ok は宛先と無関係にこの link が持つので、宛先を替えても途絶の数え方は続く。
          self.endpoint (CoordinatorEndpoint url REPLY-SECONDS 0 :transport transport :actor name)
          self.task-dir (Path (or task-dir "tasks")) self.versions (or versions {})
          ;; 起動した時点を最後の連絡とみなす: 一度も届かない worker は fence の後に何も動かさない。
          self.last-ok (time.monotonic)
          ;; 最後に受け取った job と task の宣言(途絶の間も動かす書き手と切り離した task を選ぶ — worker_policy.kept-when-cut-off)。
          self.last-jobs #()
          self.last-tasks #()
          ;; この process の世代(heartbeat の boot)。coordinator は drain を頼まれた時の世代に付け、別の世代(Pod を作り直した後の
          ;; worker)の heartbeat で drain を解く(cluster_policy.absorb-boot・2026-09-25)。
          self.boot (. (uuid.uuid4) hex))
    ;; 世代を Pod の中の file へ書く(DOEFF_WORKER_BOOT_FILE)— readinessProbe が「coordinator の見る worker がこの Pod の物か」を
    ;; 比べる(drain_client.ready-of)。同じ node の前の Pod と名が同じなので、名だけでは見分けられない。
    (setv boot-file (os.environ.get "DOEFF_WORKER_BOOT_FILE"))
    (when boot-file
      (setv tmp (Path (+ boot-file ".tmp")))
      (.write-text tmp (+ self.boot "\n") :encoding "utf-8")
      (os.replace tmp boot-file)))

  (defn #^ tuple accept-tasks [self #^ list tasks]
    (.mkdir self.task-dir :parents True :exist-ok True)
    (setv ids (sfor t tasks (get t "id")))
    (for [task tasks]
      (setv blob (/ self.task-dir (+ (get task "id") ".blob")))
      (when (not (.exists blob))
        (setv tmp (Path (+ (str blob) ".tmp")))
        (.write-text tmp (get task "blob") :encoding "ascii")
        (os.replace tmp blob)))
    (for [entry (.iterdir self.task-dir)]
      (when (and (in entry.suffix #(".blob" ".result")) (not-in entry.stem ids))
        (.unlink entry :missing-ok True)))
    (tuple (gfor task tasks (task-spec task self.task-dir))))

  (defn #^ dict env-body [self]
    "heartbeat に足す root の名乗り(実行環境を扱う worker だけ): platform・準備済み / 準備中 / 失敗の root・disk の条件。"
    (if (is self.envs None)
        {}
        (do (setv envs (.report self.envs))
            {"platform" (current-platform)
             "envs" {"ready" (get envs "ready") "preparing" (get envs "preparing") "failed" (get envs "failed")}
             "envCapacity" (get envs "capacity")})))

  (defn #^ tuple accept-warm [self #^ list rows]
    "heartbeat の返事の温める表の行 → この worker の root のキーの WarmEnv(キーは行ごとに 1 度だけ計算する)。"
    (when (is self.envs None) (return #()))
    (setv out [])
    (for [row rows]
      (setv text (json.dumps (get row "runtimeEnv") :sort-keys True :ensure-ascii False))
      (when (not-in text self.warm-keys)
        (setv (get self.warm-keys text)
              (+ ENV-KEY-PREFIX (run (env-key (run (runtime-env-of-json (get row "runtimeEnv"))) (current-platform))))))
      (.append out (WarmEnv :key (get self.warm-keys text) :runtime-env text)))
    (tuple out))

  (defn #^ list report [self #^ tuple statuses]
    "状態の報告。終わった task には結果の file の中身(無ければ None = 結果なし)を添える。"
    (lfor s statuses
      :setv row (status-row s)
      (if (and (.startswith s.name "task/") (= s.phase JobPhase.FINISHED))
          (do (setv result (/ self.task-dir (+ (cut s.name 5 None) ".result")))
              (| row {"result" (if (.exists result) (.read-text result :encoding "ascii") None)}))
          row)))

  (defn poll [self]
    (try
      (setv response (.request self.endpoint "POST" "/heartbeat"
        :json (| {"name" self.name "labels" self.labels "capacity" self.capacity "versions" self.versions
                  "statuses" self.statuses "endpoint" self.endpoint.url "boot" self.boot "format" PROTOCOL-FORMAT
                  "tools" self.tools}
                 (.env-body self))))
      (.raise-for-status response)
      (setv self.last-ok (time.monotonic))
      (setv body (.json response))
      (write-ready-file (os.environ.get "DOEFF_WORKER_READY_FILE") (bool (.get body "draining" False)))
      ;; 自己停止の時間は coordinator の ClusterTiming が持つ(移し替えの時間と組で決まる)。受け取った値に合わせる。
      (setv timing (.get body "timing"))
      (when (and timing (in "fence_ms" timing))
        (setv self.fence-ms (int (get timing "fence_ms"))))
      (setv self.last-jobs (tuple (gfor job (get body "jobs") (declared-job-spec job))))
      (setv self.last-tasks (.accept-tasks self (.get body "tasks" [])))
      (setv self.last-warm (.accept-warm self (.get body "warm" [])))
      (DesiredJobs (+ self.last-jobs self.last-tasks) :warm self.last-warm)
      (except [error Exception]
        (setv silent-ms (int (* 1000 (- (time.monotonic) self.last-ok))))
        ;; 連絡が fence を超えて途絶えたら、lease を持たない job と task を止める(coordinator は後で他へ移す)。書き手(入れ替えを
        ;; 宣言した job)と切り離した task は動かし続ける — 書きは lease の柵だけが守り、切り離した task の lease はこの worker の
        ;; heartbeat が延ばす(worker_policy.kept-when-cut-off・2026-09-25)。
        (if (> silent-ms self.fence-ms)
          (DesiredJobs (kept-when-cut-off (+ self.last-jobs self.last-tasks)) :warm self.last-warm)
          (DesiredUnreadable f"coordinator に届かない({silent-ms} ms): {(repr error)}"))))))

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

(defn #^ dict status-row [s]
  {"name" s.name "phase" s.phase.value "desiredRevision" s.desired-revision
   "runningRevision" s.running-revision "pid" s.pid "attempts" s.attempts "detail" s.detail
   ;; 動いている process の世代(coordinator の readiness と計器はこれと一致する報告だけを数える)。
   "instance" s.instance "specHash" s.spec-hash "placement" s.placement "retiredFrom" s.retired-from
   ;; 実行環境の準備の失敗(ENV-FAILED の行だけ): coordinator が置き直すか・答えの型を決める。
   #** (if (is s.failure None) {} {"failureKind" s.failure.kind.value "retryable" s.failure.retryable})})

(defhandler status-to-coordinator [#^ CoordinatorLink link]
  ;; 状態は次の heartbeat で送る。file にも書くので、外側の status-file へ渡す。
  (PublishStatus [statuses note]
    (setv link.statuses (.report link statuses))
    (<- (PublishStatus statuses note))
    (resume None)))

(defn release-leases [#^ CoordinatorLink link #^ str instance]
  "終わった process(世代の名 instance)が持っていた lease を返す。lease の token は「<worker>/<世代の名>/…」(services/envs.hy の
   lease-holder)。外すのは coordinator(POST /leases/<名> の drop — 2026-09-25)。drop の口を持たない旧い coordinator には、盤の行の
   compare-and-set で外す(以前の形)。届かない・競合が続く時はあきらめる(期限で切れる)。"
  (setv prefix (.format "{}/{}/" link.name instance))
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
  (ReleaseLeases [instance] (release-leases link instance) (resume None)))

(defhandler lease-release-none
  ;; 宣言の file で動く worker(coordinator も共有の保存も無い)は返す先が無い。
  (ReleaseLeases [instance] (resume None)))

(defhandler stop-flag [state]
  (WorkerStopRequested [] (resume state.requested)))
