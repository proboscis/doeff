;;; worker の版ごとのコードの木の言い換え(handlers.hy の CodeStore を置き換えた・#2466)— PrepareCode と観測 ObserveCode・焼きの経過の
;;; 秒 CodeTimings を、汎用の子 process の効果(StartProcess・PollProcess)と file system の効果(StatPath・ReadText・ListDirectory・
;;; WalkTree・RenamePath・MakeDirectory・RemoveTree)へ言い換える。I/O を持たない — 本物は外側の subprocess-handler と os-file-handler。
;;;
;;; 振る舞いは前の CodeStore と同じ:
;;;   * revision ごとに repo のコードを cache へ展開する(準備の sh の script は worker/core/code_rules の prepare-script)。展開済みの dir は再利用する。
;;;   * 完成品 = cache の直下の、完成の印(code_plan の MARKER)が検めを通る dir。印の無い・検めの通らない dir は完成品として公開せず、
;;;     次にその版を求められた時に脇へ退けて(. で始まる名へ rename)作り直す。
;;;   * 引き継ぎ元 = 最後に完成した版の木(完成品は rename で現れるので mtime が完成の時刻)。
;;;   * 読む時の検めの答えは、同じ mtime と大きさの間は使い回す(木は rename で現れて以後変えない — 前は inode と mtime_ns を鍵にした。
;;;     汎用の StatPath の答えは inode を持たない)。
;;; 記録(準備中・失敗・経過の秒・検めの答え)は handler の session の値で持つ。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass])
(import pathlib [Path])
(import doeff_core_effects [slog])
(import doeff_core_effects.file_effects [PathKind PathStat FileFailed StatPath ReadText ListDirectory WalkTree RenamePath MakeDirectory
                                         RemoveTree file-done])
(import doeff_core_effects.process_effects [EnvEntry EnvMode StartProcess PollProcess ProcessNotStarted ProcessExited])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.intent.worker_model [CodeLayout CodeState CodeView PrepareCode WakeSet WorkerWakes])
(import doeff_cluster.worker.core.worker_due [wakes-with])
(import doeff_cluster.shared.intent.due_model [DueNever])
(import doeff_core_effects.process_effects [AwaitProcessExit])
(import doeff_cluster.worker.protocol.observations [ObserveCode CodeTimings])
(import doeff_cluster.worker.core.code_plan [MARKER marker-problem])
(import doeff_cluster.worker.core.code_prepare [tree-listing])
(import doeff_cluster.worker.core.code_rules [prepare-script])


;; 焼く道具の file(worker 自身のコードの code_prepare.hy — 版の木から -m で起動すると、道具を持たない古い版で見つからないので path で起動する)。
(val PREPARE-TOOL (str (/ (. (Path __file__) (resolve) parent parent) "entry" "code_prepare.hy")))


(defrecord CodeSettings
  "版ごとのコードの木の置き場と準備の設定(worker の組み立ての入口 main が作る): repo = 展開する repo・cache = 木の置き場・hy-command = 焼きに
   使う hy(None なら bytecode の準備を省く)・tool = 焼く道具の file(worker 自身のコードの code_prepare.hy)・layout = 業務の repo の木の形。"
  (#^ str repo)
  (#^ str cache)
  (#^ (| str None) hy-command)
  (#^ str tool)
  (#^ CodeLayout layout))


(defrecord TreeCheck
  "cache の直下の dir 1 つの検め: name = 版・modified と size = 検めた時の dir の mtime と大きさ(答えを使い回す鍵)・reason = 完成品なら None・
   そうでなければ理由。"
  (#^ str name)
  (#^ float modified)
  (#^ int size)
  (#^ (| str None) reason))


(defk tree-check [settings name seen]
  {:pre [(: settings CodeSettings) (: name str) (: seen (| TreeCheck None))] :post [(: % (| TreeCheck None))]
   :tags {:context "worker" :role "protocol"}}
  "cache の直下の dir 1 つを検めるため(seen = 前の拍の検め — 同じ mtime と大きさなら使い回す)。dir が無ければ None。"
  (val path (+ settings.cache "/" name))
  (<- stat (StatPath path))
  (cond
    (or (isinstance stat FileFailed) (!= stat.kind PathKind.DIRECTORY)) None
    (and seen (= #(seen.modified seen.size) #(stat.modified stat.size))) seen
    True
      (do (<- read (ReadText (+ path "/" MARKER)))
          (val text (if (isinstance read str) read None))
          (val want-bytecode (is-not settings.hy-command None))
          (var pycs 0)
          (when (and want-bytecode (is-not text None))
            (<- entries tuple (WalkTree path))
            (<- listing tuple (tree-listing (lfor e entries e.name)))
            (:= pycs (len (get listing 1))))
          (<- reason (| str None) (marker-problem text name want-bytecode pycs))
          (TreeCheck :name name :modified stat.modified :size stat.size :reason reason))))


(defk cache-checks [settings checked]
  {:pre [(: settings CodeSettings) (: checked dict)] :post [(: % tuple)] :tags {:context "worker" :role "protocol"}}
  "cache の直下の dir(. で始まる名を除く)の検めの列を返すため(checked = 前の拍の検め — 版 → TreeCheck)。"
  (<- top (ListDirectory settings.cache))
  (var checks #())
  (when (not (isinstance top FileFailed))
    (for [entry top]
      (when (and (= entry.kind PathKind.DIRECTORY) (not (.startswith entry.name ".")))
        (<- check (tree-check settings entry.name (.get checked entry.name)))
        (when (is-not check None) (:= checks (+ checks #(check)))))))
  checks)


(defhandler code-host [#^ CodeSettings settings]
  ;; 引数に残す理由: repo と木の置き場と焼きの道具は worker の process ごとの設定(main が引数から作る)。
  ;; 記録: 版 → #(準備の子の pid 起こした時刻 標準エラーの file)・版 → #(失敗の理由 時刻)・版 → 準備の秒・版 → 検め(TreeCheck)。
  (session var pending {})
  (session var failed {})
  (session var timings {})
  (session var checked {})
  (PrepareCode [revision]
    (when (not-in revision pending)
      (val final (+ settings.cache "/" revision))
      (<- found (| TreeCheck None) (tree-check settings revision (.get checked revision)))
      (var broken None)
      (var skip False)
      (when (is-not found None)
        (if (is found.reason None)
            (:= skip True)
            (do
              ;; 完成品に見えて検めの通らない木(印の無い古い形・焼きの失敗が完成品になった木)は脇へ退けて作り直す。
              ;; 名前は . で始まるので、消し終わるまでの間も完成品としては読まれない。
              (<- now-ms int (now-epoch-ms))
              (:= broken (.format "{}/.{}.broken.{}" settings.cache revision now-ms))
              (<- (file-done (RenamePath final broken)))
              (:= checked (dfor #(k v) (.items checked) :if (!= k revision) k v))
              (<- (slog (.format "worker: 版 {} の木を作り直します({})" revision found.reason))))))
      (when (not skip)
        (:= failed (dfor #(k v) (.items failed) :if (!= k revision) k v))
        (<- (file-done (MakeDirectory settings.cache)))
        ;; 版の木の検めを読み直す(.pyc は source の中身で引く保存先から書くので、前の版の木から引き継がない — #3858)。
        (<- checks tuple (cache-checks settings checked))
        (:= checked (dfor c checks c.name c))
        (<- script str (prepare-script settings.repo revision :hy-command settings.hy-command :tool settings.tool
                                       :layout settings.layout))
        (val tmp (.format "{}/.{}.tmp" settings.cache revision))
        (val err (.format "{}/.{}.err" settings.cache revision))
        (<- answer (StartProcess :argv #("sh" "-c" script) :stderr-path err :env-mode EnvMode.EXTEND
                                 :env #((EnvEntry :name "B" :value (or broken "")) (EnvEntry :name "F" :value final)
                                        (EnvEntry :name "PYTHONPATH" :value (.pythonpath settings.layout tmp)) (EnvEntry :name "T" :value tmp))))
        (when (isinstance answer ProcessNotStarted)
          (raise (OSError answer.detail)))
        (<- started-ms int (now-epoch-ms))
        (:= pending (| pending {revision #(answer.pid started-ms err)}))))
    (resume None))
  (ObserveCode []
    (<- now-ms int (now-epoch-ms))
    (for [#(revision #(pid started-ms err)) (list (.items pending))]
      (<- polled (PollProcess pid))
      (when (isinstance polled ProcessExited)
        (:= pending (dfor #(k v) (.items pending) :if (!= k revision) k v))
        (:= timings (| timings {revision (/ (- now-ms started-ms) 1000.0)}))
        (when (!= polled.exit-code 0)
          (<- text (ReadText err))
          (val detail (.strip (if (isinstance text str) text "")))
          (:= failed (| failed {revision #((+ f"準備に失敗(終了 {polled.exit-code}): " (cut detail -480 None)) now-ms)})))
        (<- (RemoveTree err))))  ; 無い file の断りは捨てる
    (<- checks tuple (cache-checks settings checked))
    (:= checked (dfor c checks c.name c))
    (resume (tuple (+ (lfor revision pending (CodeView revision CodeState.PREPARING))
                      (lfor #(revision #(detail failed-ms)) (.items failed)
                            (CodeView revision CodeState.FAILED :detail detail :failed-ms failed-ms))
                      (lfor c checks :if (and (is c.reason None) (not-in c.name pending) (not-in c.name failed))
                            (CodeView c.name CodeState.READY :path (+ settings.cache "/" c.name)))))))
  (CodeTimings []
    (resume (dict timings)))
  (WorkerWakes []
    ;; 周の間の待ちを起こす物(#3871 の単位 4): 走っている準備の子(pending — まだ終わりを観測していない物)の終わりを待つ効果を足す。
    ;; 期限と呼び鈴は足さない(準備の期限は持たない・終わりは ObserveCode が読む)。
    (<- outer WakeSet effect)
    (val exits (tuple (gfor #(pid started-ms err) (.values pending) (AwaitProcessExit pid))))
    (<- merged WakeSet (wakes-with outer (DueNever) #() exits))
    (resume merged)))
