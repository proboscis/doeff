;; worker の実 I/O。宣言の file・コードの展開(git archive)・子 process・状態の file・停止信号。
;; どれもループを塞がない: 展開と子 process は Popen で起動し、結果は ObserveWorld で観測する。
(require doeff-hy.macros [defhandler <-])
(import json os signal subprocess sys time uuid)
(import dataclasses [replace])
(import pathlib [Path])
(import urllib.parse [quote :as url-quote])
(import .coordinator_http [CoordinatorEndpoint REPLY-SECONDS])
(import .code_prepare [MARKER MARKER-FORMAT marker-problem scan])
(import .semaphore_model [SEMAPHORE-PREFIX drop-holders])
(import .worker_policy [kept-when-cut-off])
(import .worker_model [JobSpec CodeState CodeView ProcessView WorldView StopStage ProbeState ProbeView
  DesiredJobs DesiredUnreadable ReadDesired ObserveWorld WorkerStopRequested PublishStatus JobPhase
  PrepareCode StartJob SignalJob ReapJob RetireJob ReleaseLeases ProbeEntry spec-hash split-code-key probe-args CodeLayout])

(defn #^ (| DesiredJobs DesiredUnreadable) parse-desired [#^ str text]
  (try
    (setv data (json.loads text))
    (DesiredJobs (tuple (gfor job (get data "jobs")
      (JobSpec (get job "name") (get job "entry") (tuple (.get job "args" [])) (get job "revision")
               :base (.get job "base") :handoff (bool (.get job "handoff" False))))))
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
   (layout の import の根)・cwd = 木。timeout-seconds を越えた検めは止めて FAILED(理由 = 時間切れ)。
   結果の鍵は spec-hash(同じ spec の検めは撃ち直されるまで答えを使い回す)。"
  (defn #^ None __init__ [self #^ str hy-command #^ (| int float) [timeout-seconds PROBE-SECONDS] #^ CodeLayout [layout (CodeLayout)]]
    (setv self.hy-command hy-command self.timeout-seconds timeout-seconds self.layout layout self.pending {} self.done {}))

  (defn #^ None start [self #^ ProbeEntry action]
    (setv key (spec-hash action.spec))
    (when (in key self.pending) (return))
    (.pop self.done key None)
    (setv (get self.pending key)
      #((subprocess.Popen [self.hy-command "-c" PROBE-PROGRAM #* (probe-targets action.spec)]
          :cwd action.code-path
          :env (| (dict os.environ) {"PYTHONPATH" (.pythonpath self.layout action.code-path)
                                     "PYTHONDONTWRITEBYTECODE" "1"})
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
   layout = 業務の repo の木の形(子の PYTHONPATH — worker_model.CodeLayout)。"
  (defn __init__ [self #^ str log-dir #^ str hy-command [extra-env None] #^ CodeLayout [layout (CodeLayout)]]
    (setv self.log-dir (Path log-dir) self.hy-command hy-command self.table {} self.extra-env (or extra-env {})
          self.layout layout))

  (defn start [self #^ StartJob action]
    (setv spec action.spec)
    (when (in spec.name self.table) (raise (RuntimeError f"{spec.name} は既に動いています")))
    (.mkdir self.log-dir :parents True :exist-ok True)
    (setv log-name (.replace spec.name "/" "_"))  ; task の名前は task/<id>
    (setv log (open (/ self.log-dir f"{log-name}.{action.attempt}.log") "ab"))
    ;; process の世代の名: 起こすたびに新しく振る(試行の番号は worker の再起動で 1 に戻るので、それだけでは前の process と重なる)。
    (setv instance f"{action.attempt}-{(cut (. (uuid.uuid4) hex) 0 12)}")
    (setv env (| (dict os.environ) self.extra-env
                 {"PYTHONPATH" (.pythonpath self.layout action.code-path)
                  "DOEFF_WORKER_JOB" spec.name
                  "DOEFF_WORKER_REVISION" spec.revision
                  "DOEFF_WORKER_ATTEMPT" (str action.attempt)
                  "DOEFF_WORKER_INSTANCE" instance
                  "DOEFF_WORKER_SPEC_HASH" (spec-hash spec)
                  "DOEFF_WORKER_PLACEMENT" (if (is spec.placement None) "" (str spec.placement))}))
    (try
      ;; shim を group の先頭に置き、stdin のパイプを worker が握る。worker が死ぬとパイプが閉じ、
      ;; shim が job の group を止める(kill -9 された worker の job が残って二重に動くのを防ぐ)。
      (setv process (subprocess.Popen
        [sys.executable "-m" "doeff_cluster.shim" "10" "--" self.hy-command "-m" spec.entry #* spec.args]
        :cwd action.code-path :env env :stdout log :stderr subprocess.STDOUT
        :stdin subprocess.PIPE :start-new-session True))
      (finally (.close log)))
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
      (del (get self.table action.name))))

  (defn #^ tuple observe [self]
    (tuple (gfor #(process view) (.values self.table)
      (do (setv code (.poll process))
          (if (is code None) view
              (replace view :exit-code code)))))))

(defhandler local-host [#^ CodeStore codes #^ ProcessHost host #^ ProbeStore probes]
  (ObserveWorld [] (resume (WorldView (.observe codes) (.observe host) (.observe probes))))
  (PrepareCode [revision] (.start codes revision) (resume None))
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
  "coordinator が割り当てた task 1 本 → 1 度だけ走らせる job。blob と結果はこの worker の file(名前は task の id で決まる)。"
  (setv id (get task "id"))
  (JobSpec (+ "task/" id) JOB-ENTRY
           #("task" "--blob" (str (/ task-dir f"{id}.blob")) "--result" (str (/ task-dir f"{id}.result"))
             "--env" (get task "env") "--versions" (json.dumps (get task "versions") :sort-keys True))
           (get task "revision") :once True :detached (bool (.get task "detached" False))))


(defclass CoordinatorLink []
  "coordinator との連絡。heartbeat で生存・版・状態(終わった task の結果を含む)を送り、自分に割り当てられた job と task を受け取る。
   task の blob は task-dir の file に置き、宣言から外れた task の file は消す(この worker が書いた物だけ)。"
  (defn __init__ [self #^ str url #^ str name #^ dict labels #^ int capacity #^ int fence-ms
                  [task-dir None] [versions None] [transport None]]
    (setv self.name name self.labels labels self.capacity capacity
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
        :json {"name" self.name "labels" self.labels "capacity" self.capacity "versions" self.versions
               "statuses" self.statuses "endpoint" self.endpoint.url "boot" self.boot}))
      (.raise-for-status response)
      (setv self.last-ok (time.monotonic))
      (setv body (.json response))
      (write-ready-file (os.environ.get "DOEFF_WORKER_READY_FILE") (bool (.get body "draining" False)))
      ;; 自己停止の時間は coordinator の ClusterTiming が持つ(移し替えの時間と組で決まる)。受け取った値に合わせる。
      (setv timing (.get body "timing"))
      (when (and timing (in "fence_ms" timing))
        (setv self.fence-ms (int (get timing "fence_ms"))))
      (setv self.last-jobs (tuple (gfor job (get body "jobs")
                                        (JobSpec (get job "name") (get job "entry") (tuple (get job "args")) (get job "revision")
                                                 :once (.get job "once" False) :placement (.get job "placement")
                                                 :base (.get job "base") :handoff (bool (.get job "handoff" False))
                                                 :ready-instance (.get job "readyInstance")))))
      (setv self.last-tasks (.accept-tasks self (.get body "tasks" [])))
      (DesiredJobs (+ self.last-jobs self.last-tasks))
      (except [error Exception]
        (setv silent-ms (int (* 1000 (- (time.monotonic) self.last-ok))))
        ;; 連絡が fence を超えて途絶えたら、lease を持たない job と task を止める(coordinator は後で他へ移す)。書き手(入れ替えを
        ;; 宣言した job)と切り離した task は動かし続ける — 書きは lease の柵だけが守り、切り離した task の lease はこの worker の
        ;; heartbeat が延ばす(worker_policy.kept-when-cut-off・2026-09-25)。
        (if (> silent-ms self.fence-ms)
          (DesiredJobs (kept-when-cut-off (+ self.last-jobs self.last-tasks)))
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
   "instance" s.instance "specHash" s.spec-hash "placement" s.placement "retiredFrom" s.retired-from})

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
