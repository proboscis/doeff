;;; worker の実行環境(runtime env)の root の言い換え(handlers.hy の EnvStore を置き換えた・#2467)— PrepareEnv・SweepEnvs と観測
;;; ObserveEnvs・ObserveEnvDisk・heartbeat の名乗り EnvReport を、汎用の子 process の効果(StartProcess・PollProcess・StopProcess)と
;;; file system の効果(StatPath・ReadText・WriteText・ListDirectory・RenamePath・MakeDirectory・RemoveTree・ReadDiskUsage・MeasureTree)
;;; へ言い換える。I/O を持たない — 本物は外側の subprocess-handler と os-file-handler。
;;;
;;; 振る舞いは前の EnvStore と同じ:
;;;   * root を env のキーごとに準備する。準備は worker 自身の code の env_handlers を別の process として起こし(worker のループは待たない)、
;;;     完成マーカーの在る root だけを READY として観測する。root は state/roots/<キー> の最終の path に作る(venv が絶対 path を持つので
;;;     rename しない)。マーカーの無い root は次に求められた時に脇へ退けて作り直す。準備は同時に max-parallel 本まで・同じキーは 1 本。
;;;   * 先読み(warm): 温める表の root は job の準備より後に起こし、同時の枠の 1 つを job に残す(起こす順と数は env_rules.launch-order)。
;;;     期限は env_upkeep.prepare-overdue(先読みは停滞だけ・job は冷たい / 温い)。
;;;   * 掃除(sweep): 空きが下限を切ったら、固定されていない root を消す(選びは env_upkeep.sweep-choice)・uv の cache を prune(待たない)・
;;;     7 日使われない wheel を消す。消すのは worker が作った dir だけ。
;;; 記録(待ち・準備中・失敗・固定の集合・掃除と prune の時刻・最後の観測)は handler の session の値で持つ。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import dataclasses [dataclass replace])
(import json)
(import re)
(import doeff_core_effects [slog])
(import doeff_core_effects.file_effects [PathKind FileFailed StatPath ReadText WriteText ListDirectory RenamePath MakeDirectory RemoveTree
                                         ReadDiskUsage MeasureTree file-done])
(import doeff_core_effects.process_effects [EnvEntry EnvMode StartProcess PollProcess StopProcess ProcessNotStarted ProcessRunning
                                            ProcessExited])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView EnvDisk PrepareEnv SweepEnvs EnvReport])
(import doeff_cluster.worker.protocol.observations [ObserveEnvs ObserveEnvDisk])
(import doeff_cluster.worker.core.worker_rules [ENV-KEY-PREFIX])
(import doeff_cluster.worker.core.env_upkeep [RootInfo PrepareLimits sweep-choice prepare-overdue env-capacity WHEEL-UNUSED-SECONDS])
(import doeff_cluster.worker.core.env_rules [launch-order cold-for prepare-request prepare-argv prepare-outcome overdue-failure root-project
                                             floor-bytes])
(import doeff_cluster.worker.protocol.heartbeat [env-report])


(val ENV-TOOL "doeff_cluster.env_handlers")   ; 準備の process の入口(worker 自身の環境の module — root の路は worker に足さない)
(val ROOT-NAME-PATTERN (re.compile r"[0-9a-f]{24}"))
(val SWEEP-EVERY-MS 30000)      ; 空きが下限を切っている間の掃除の間隔(固定の集合が変わった時はすぐ)
(val PRUNE-EVERY-MS 1800000)    ; uv の cache の prune を起こし直す間隔の下限(node の disk を他の物が使うと掃除では下限に戻らず、拍ごとに起き続けるため)


(defrecord EnvSettings
  "実行環境の root の置き場と準備の設定(worker の組み立ての入口 main が作る): state = worker の state の dir(root は state/roots の下)・
   hy-command = 準備の process を起こす hy・platform = この worker の platform(準備の頼みに書く)・code-prepare = 焼く道具の file・
   repo-keys = 許可表の JSON の file(clone してよい URL → deploy key)・uv = uv の命令・min-free-bytes = 準備を始める空きの下限・
   limits = 準備の期限・max-parallel = 同時の準備の上限・tool = 準備の process の入口・sweep-floor-bytes = 掃除の下限(None = volume の割合)。"
  (#^ str state)
  (#^ str hy-command)
  (#^ str platform)
  (#^ str code-prepare)
  (setv #^ str repo-keys "")
  (setv #^ str uv "uv")
  (setv #^ int min-free-bytes 0)
  (setv #^ PrepareLimits limits (PrepareLimits))
  (setv #^ int max-parallel 2)
  (setv #^ str tool ENV-TOOL)
  (setv #^ (| int None) sweep-floor-bytes None))


(defrecord PendingEnv
  "走っている準備 1 本の記録: pid = 準備の子・started-ms = 起こした時刻・result = 答えの file・progress = 進みの印の file・
   warm = 先読みの準備か(job がその root を求めたら job の準備へ上げる)・cold = 冷たい準備か(引き継げる root が無い)。"
  (#^ int pid)
  (#^ int started-ms)
  (#^ str result)
  (#^ str progress)
  (#^ bool warm)
  (#^ bool cold))


(defk env-root [settings key]
  {:pre [(: settings EnvSettings) (: key str)] :post [(: % str)]}
  "env のキー(env-<キー>)の root の path を返すため。"
  (+ settings.state "/roots/" (cut key (len ENV-KEY-PREFIX) None)))


(defk read-marker [root]
  {:pre [(: root str)] :post [(: % (| dict None))]}
  "root の完成マーカーの中身を返すため(無い・読めなければ None)。"
  (<- text (ReadText (+ root "/" ENV-MARKER)))
  (if (isinstance text str)
      (try (json.loads text) (except [ValueError] None))
      None))


(defk root-dirs [settings]
  {:pre [(: settings EnvSettings)] :post [(: % tuple)]}
  "state/roots の直下の dir の名(. で始まる名を除く・名の順)を返すため。"
  (<- entries (ListDirectory (+ settings.state "/roots")))
  (if (isinstance entries FileFailed)
      #()
      (tuple (gfor e entries :if (and (= e.kind PathKind.DIRECTORY) (not (.startswith e.name "."))) e.name))))


(defk known-roots [settings]
  {:pre [(: settings EnvSettings)] :post [(: % tuple)]}
  "完成した root の列(展開の複製と bytecode の引き継ぎの元)を、頼みの JSON の形 {\"env\" 宣言 \"root\" path} で返すため。"
  (<- names tuple (root-dirs settings))
  (var known #())
  (for [name names]
    (val root (+ settings.state "/roots/" name))
    (<- marker (read-marker root))
    (when (is-not marker None)
      (:= known (+ known #({"env" (get marker "env") "root" root})))))
  known)


(defk launch-prepare [settings key runtime-env warm]
  {:pre [(: settings EnvSettings) (: key str) (: runtime-env str) (: warm bool)] :post [(: % PendingEnv)]}
  "root の準備の process を 1 本起こして記録を返すため。マーカーの無い root(途中で止まった準備)は脇へ退ける(名は . で始まるので
   完成品としては読まれない)。"
  (<- root str (env-root settings key))
  (<- now-ms int (now-epoch-ms))
  (<- seen (StatPath root))
  (when (and (not (isinstance seen FileFailed)) (!= seen.kind PathKind.MISSING))
    (val name (cut key (len ENV-KEY-PREFIX) None))
    (<- (file-done (RenamePath root (.format "{}/roots/.{}.broken.{}" settings.state name now-ms)))))
  (val requests (+ settings.state "/env-requests"))
  (<- (file-done (MakeDirectory requests)))
  (val declared (json.loads runtime-env))
  (val request (+ requests "/" key ".json"))
  (val result (+ requests "/" key ".result.json"))
  (val progress (+ requests "/" key ".progress"))
  (val log (+ requests "/" key ".log"))
  (<- (RemoveTree result))     ; 無い file の断りは捨てる
  (<- (RemoveTree progress))
  (<- known tuple (known-roots settings))
  (<- cold bool (cold-for declared known))
  (<- body dict (prepare-request declared (cut key (len ENV-KEY-PREFIX) None) settings.platform root known settings.min-free-bytes))
  (<- (file-done (WriteText request (json.dumps body :ensure-ascii False))))
  (<- argv tuple (prepare-argv settings.hy-command settings.tool request result settings.state settings.repo-keys settings.code-prepare
                               settings.uv progress))
  ;; 子は worker の環境を継ぐ(env = None)。出力は標準出力と標準エラーを同じ log の末尾へ。
  (<- started (StartProcess :argv argv :stdout-path log :stderr-path log))
  (when (isinstance started ProcessNotStarted)
    (raise (OSError started.detail)))
  (PendingEnv :pid started.pid :started-ms now-ms :result result :progress progress :warm warm :cold cold))


(defk progressed-ms [pending]
  {:pre [(: pending PendingEnv)] :post [(: % int)]}
  "準備の最後の進み(処理ステージの頭の印の時刻・印が無ければ起こした時刻)を返すため。"
  (<- seen (StatPath pending.progress))
  (if (or (isinstance seen FileFailed) (!= seen.kind PathKind.FILE))
      pending.started-ms
      (max pending.started-ms (int (* 1000 seen.modified)))))


(defk ready-views [settings busy]
  {:pre [(: settings EnvSettings) (: busy frozenset)] :post [(: % tuple)]}
  "完成マーカーの在る root の観測(READY)を返すため(busy = 準備中か失敗の記録の在るキー — 除く)。"
  (<- names tuple (root-dirs settings))
  (var views #())
  (for [name names]
    (val key (+ ENV-KEY-PREFIX name))
    (when (not-in key busy)
      (val root (+ settings.state "/roots/" name))
      (<- marker (read-marker root))
      (when (is-not marker None)
        (:= views (+ views #((CodeView key CodeState.READY :path root)))))))
  views)


(defk disk-view [settings pinned]
  {:pre [(: settings EnvSettings) (: pinned frozenset)] :post [(: % EnvDisk)]}
  "root の置き場の disk の観測を返すため(worker の判断が掃除の時を決める)。"
  (<- (file-done (MakeDirectory settings.state)))
  (<- usage (file-done (ReadDiskUsage settings.state)))
  (<- floor int (floor-bytes settings.sweep-floor-bytes settings.min-free-bytes usage.total))
  (EnvDisk :free usage.free :floor floor :pinned pinned))


(defk modified-ms [path]
  {:pre [(: path str)] :post [(: % (| int None))]}
  "path の mtime(epoch ミリ秒)を返すため(無ければ None)。"
  (<- seen (StatPath path))
  (if (or (isinstance seen FileFailed) (= seen.kind PathKind.MISSING)) None (int (* 1000 seen.modified))))


(defk root-infos [settings]
  {:pre [(: settings EnvSettings)] :post [(: % tuple)]}
  "掃除の候補(roots の直下の dir)を返すため。worker が作った root = キーの形の名で完成マーカーを持つ dir。"
  (<- names tuple (root-dirs settings))
  (var infos #())
  (for [name names]
    (val root (+ settings.state "/roots/" name))
    (<- marker (read-marker root))
    (val owned (and (is-not marker None) (bool (ROOT-NAME-PATTERN.fullmatch name))))
    (var made 0)
    (var used 0)
    (var size 0)
    (var project "")
    (when owned
      (<- marker-ms (modified-ms (+ root "/" ENV-MARKER)))
      (:= made (or marker-ms 0))
      (<- measured (MeasureTree root))
      (:= size (if (isinstance measured int) measured 0))
      (<- named str (root-project marker))
      (:= project named))
    (<- used-ms (modified-ms (+ root "/.last-used")))
    (:= used (if (is-not used-ms None) used-ms made))
    (:= infos (+ infos #((RootInfo :key (+ ENV-KEY-PREFIX name) :project project :made-ms made :last-used-ms used :bytes size
                                   :owned owned)))))
  infos)


(defk sweep-leftovers [settings now-ms]
  {:pre [(: settings EnvSettings) (: now-ms int)] :post [(: % None)]}
  "途中で止まった準備の残り(.<キー>.broken.<時刻> — worker が退けた物)と、7 日使われない native の wheel(使うたびに dir の中の印の
   file を置き換えて dir の時刻を進める — env_handlers の EnsureNativeWheel)を消すため。"
  (<- roots (ListDirectory (+ settings.state "/roots")))
  (when (not (isinstance roots FileFailed))
    (for [entry roots]
      (when (and (.startswith entry.name ".") (in ".broken." entry.name))
        (<- (RemoveTree (+ settings.state "/roots/" entry.name))))))
  (<- wheels (ListDirectory (+ settings.state "/wheels")))
  (when (not (isinstance wheels FileFailed))
    (for [entry wheels]
      (when (= entry.kind PathKind.DIRECTORY)
        (val path (+ settings.state "/wheels/" entry.name))
        (<- at (modified-ms path))
        (when (and (is-not at None) (> (- now-ms at) (* 1000 WHEEL-UNUSED-SECONDS)))
          (<- (RemoveTree path))))))
  None)


(defk launch-waiting [settings waiting pending]
  {:pre [(: settings EnvSettings) (: waiting dict) (: pending dict)] :post [(: % tuple)]}
  "待っている準備のうち起こせる物を起こし、#(残りの待ち 準備中) を返すため(起こす順と数は env_rules.launch-order)。"
  (<- order tuple (launch-order (tuple (gfor #(k #(_ w)) (.items waiting) #(k w))) (len pending)
                                (len (lfor p (.values pending) :if p.warm p)) settings.max-parallel))
  (var left waiting)
  (var running pending)
  (for [key order]
    (val entry (get left key))
    (:= left (dfor #(k v) (.items left) :if (!= k key) k v))
    (<- started PendingEnv (launch-prepare settings key (get entry 0) (get entry 1)))
    (:= running (| running {key started})))
  #(left running))


(defk observe-envs [settings waiting pending failed]
  {:pre [(: settings EnvSettings) (: waiting dict) (: pending dict) (: failed dict)] :post [(: % tuple)]}
  "準備の子の終わりと期限を見て記録を進め、待ちを起こし、root の観測を返すため。答え = #(待ち 準備中 失敗 観測の tuple)。"
  (<- now-ms int (now-epoch-ms))
  (var running pending)
  (var failures failed)
  (for [#(key p) (.items pending)]
    (<- polled (PollProcess p.pid))
    (if (isinstance polled ProcessRunning)
        (do (<- progressed int (progressed-ms p))
            (<- overdue bool (prepare-overdue p.warm p.cold (/ p.started-ms 1000.0) (/ progressed 1000.0) (/ now-ms 1000.0)
                                              settings.limits))
            (when overdue
              (<- (StopProcess :pid p.pid :stop-grace 0.0))
              (:= running (dfor #(k v) (.items running) :if (!= k key) k v))
              (<- failure (overdue-failure p.warm p.cold settings.limits))
              (:= failures (| failures {key #(failure now-ms)}))))
        (do (:= running (dfor #(k v) (.items running) :if (!= k key) k v))
            (<- text (ReadText p.result))
            ;; 答えの file が無い・読めない = 答えを書かずに終わった準備(prepare-outcome が log の在処を添えた失敗にする)。
            (val answer (if (isinstance text str) (try (json.loads text) (except [ValueError] None)) None))
            ;; 立てた覚えの無い子(ProcessNotChild)は終わりの番号が分からない — -1 で名指す。
            (val code (if (isinstance polled ProcessExited) polled.exit-code -1))
            (<- failure (prepare-outcome answer code (+ settings.state "/env-requests/" key ".log")))
            (when (is-not failure None)
              (:= failures (| failures {key #(failure now-ms)}))))))
  (<- launched tuple (launch-waiting settings waiting running))
  (val left (get launched 0))
  (val now-running (get launched 1))
  (<- ready tuple (ready-views settings (frozenset (+ (list now-running) (list failures)))))
  (val views (tuple (+ (lfor key (+ (list now-running) (list left)) (CodeView key CodeState.PREPARING))
                       (lfor #(key #(failure failed-ms)) (.items failures)
                             (CodeView key CodeState.FAILED :detail failure.detail :failed-ms failed-ms :failure failure))
                       (list ready))))
  #(left now-running failures views))


(defhandler env-host [#^ EnvSettings settings]
  ;; 引数に残す理由: root の置き場と準備の道具は worker の process ごとの設定(main が引数から作る)。
  ;; 記録: 待ち(キー → #(宣言の JSON 先読みか) — 頼まれた順)・準備中(キー → PendingEnv)・失敗(キー → #(EnvFailure 時刻))・
  ;; 固定の集合 held・最後の掃除と prune の時刻・走っている prune の pid・最後の観測(heartbeat の名乗りが読む)。
  (session var waiting {})
  (session var pending {})
  (session var failed {})
  (session var held (frozenset))
  (session var swept-ms 0)
  (session var pruned-ms 0)
  (session var pruning None)
  (session var views None)
  (PrepareEnv [key runtime-env warm]
    ;; job の頼み(warm = False)は、同じ root の先読みが走っていれば job の準備へ上げ(期限は job の物 — 起こした時刻から数える)、
    ;; 待っていれば先読みの印を下ろす。
    (cond
      (in key pending)
        (when (and (. (get pending key) warm) (not warm))
          (:= pending (| pending {key (replace (get pending key) :warm False)})))
      (in key waiting)
        (when (not warm)
          (:= waiting (| waiting {key #(runtime-env False)})))
      True
        (do (<- root str (env-root settings key))
            (<- marker (read-marker root))
            (when (is marker None)
              (:= failed (dfor #(k v) (.items failed) :if (!= k key) k v))
              (<- launched tuple (launch-waiting settings (| waiting {key #(runtime-env warm)}) pending))
              (:= waiting (get launched 0))
              (:= pending (get launched 1)))))
    (resume None))
  (ObserveEnvs []
    (<- observed tuple (observe-envs settings waiting pending failed))
    (:= waiting (get observed 0))
    (:= pending (get observed 1))
    (:= failed (get observed 2))
    (:= views (get observed 3))
    (resume views))
  (ObserveEnvDisk []
    (<- disk EnvDisk (disk-view settings held))
    (resume disk))
  (EnvReport []
    ;; heartbeat で名乗る root の姿(coordinator の置き先と温める表の読みが使う): 最後の観測(まだ無ければ今観測する)と disk の条件
    ;; (形は env-report — sim の宿と同じ関数)。
    (when (is views None)
      (<- observed tuple (observe-envs settings waiting pending failed))
      (:= waiting (get observed 0))
      (:= pending (get observed 1))
      (:= failed (get observed 2))
      (:= views (get observed 3)))
    (<- disk EnvDisk (disk-view settings held))
    (<- capacity str (env-capacity disk.free settings.min-free-bytes))
    (resume (env-report (or views #()) capacity)))
  (SweepEnvs [pinned]
    ;; 固定の集合を持ち替え、空きが下限を切っていれば掃除する(下限を切っている間は SWEEP-EVERY-MS ごと・固定が変わればすぐ)。
    (val changed (!= pinned held))
    (:= held pinned)
    (<- disk EnvDisk (disk-view settings held))
    (<- now-ms int (now-epoch-ms))
    (when (not (or (>= disk.free disk.floor) (and (not changed) (< (- now-ms swept-ms) SWEEP-EVERY-MS) (> swept-ms 0))))
      (:= swept-ms now-ms)
      ;; 固定には走っている準備(pending と waiting)も足す(判断の側の観測より新しいので)。
      (val busy (| held (frozenset pending) (frozenset waiting)))
      (<- infos tuple (root-infos settings))
      (<- chosen (sweep-choice infos busy disk.free disk.floor))
      (for [key chosen]
        (<- root str (env-root settings key))
        (<- (slog (.format "worker: 掃除 — 固定されていない root {} を消す(空き {} byte < 下限 {} byte)" (cut key (len ENV-KEY-PREFIX) None)
                           disk.free disk.floor)))
        (<- (RemoveTree root)))
      (<- (sweep-leftovers settings now-ms))
      ;; まだ下限を切っていれば uv の cache を prune する(venv の中の file は hardlink なので残る)。prune は uv の cache の lock を取るので、
      ;; 待つと root の準備の uv run が終わるまで worker のループ(heartbeat)が止まる — 別の process として起こして待たない。前の prune が
      ;; 走っている間と、root の準備が走っている間と、前の prune から PRUNE-EVERY-MS の間は起こさない(準備と lock を競わない・
      ;; 他の物が使う node の disk では prune で下限に戻らないので、拍ごとに起こし続けない)。
      (when (is-not pruning None)
        (<- polled (PollProcess pruning))
        (when (not (isinstance polled ProcessRunning)) (:= pruning None)))
      (<- after EnvDisk (disk-view settings held))
      (when (and (< after.free disk.floor) (is pruning None) (not pending) (not waiting)
                 (or (= pruned-ms 0) (>= (- now-ms pruned-ms) PRUNE-EVERY-MS)))
        (:= pruned-ms now-ms)
        (<- prune (StartProcess :argv #(settings.uv "cache" "prune") :env-mode EnvMode.EXTEND
                                :env #((EnvEntry :name "UV_CACHE_DIR" :value (+ settings.state "/uv-cache"))) :process-group True))
        (when (not (isinstance prune ProcessNotStarted))
          (:= pruning prune.pid))))
    (resume None)))
