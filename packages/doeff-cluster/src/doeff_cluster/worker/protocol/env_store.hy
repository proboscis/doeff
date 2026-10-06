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
;;;     期限は env_upkeep.prepare-overdue(先読みも job の準備も、進みの印が動かない長さだけ)。期限を判じる前に答えの file を読み、
;;;     完成を書いた準備(終わりの処理の途中)は止めない — 次の観測で終わりを読む。
;;;   * 掃除(sweep): roots の合計が上限(settings.roots-cap-bytes)を越えたら、固定されていない root を消す(選びは env_upkeep.sweep-choice・
;;;     #3732 — 共有の disk の空きでは消さない。空きが最低 settings.min-free-bytes を割った時は準備を disk-full で断る)・uv の cache を prune(待たない)・
;;;     7 日使われない wheel を消す。消すのは worker が作った dir だけ。数え(root ごとの MeasureTree)と消し(木の RemoveTree)はループの外の
;;;     task で走らせ、ループは待たない(#3715 — 2026-10-06 05:24〜05:36 に root 25 個の数えと消しがループの中で 11 分走り、heartbeat が途絶えて
;;;     worker が自分で job を止めた)。選びは数えの答えが届いた拍で、その時の固定(宣言の root・走り中の process・準備中・温める表)で行い、
;;;     選んだ root はその拍で . で始まる脇の名へ退けてから消す(消している間は選びにも完成品の観測にも出ない)。走っている掃除は同時に 1 つ。
;;; 記録(待ち・準備中・失敗・固定の集合・掃除と prune の時刻・最後の観測)は handler の session の値で持つ。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass replace])
(import json)
(import re)
(import doeff_core_effects [slog])
(import doeff_core_effects.scheduler [Spawn CreatePromise CompletePromise FailPromise Promise])
(import doeff_time [WaitWithin])
(import doeff_core_effects.file_effects [PathKind FileFailed StatPath ReadText WriteText ListDirectory RenamePath MakeDirectory RemoveTree
                                         ReadDiskUsage MeasureTree file-done])
(import doeff_core_effects.process_effects [EnvEntry EnvMode StartProcess PollProcess StopProcess ProcessNotStarted ProcessRunning
                                            ProcessExited])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.native_wheel [wheels-root])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailure])
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView EnvDisk PrepareEnv SweepEnvs EnvReport])
(import doeff_cluster.worker.protocol.observations [ObserveEnvs ObserveEnvDisk])
(import doeff_cluster.worker.core.worker_rules [ENV-KEY-PREFIX])
(import doeff_cluster.worker.core.env_upkeep [RootInfo RootsTally PrepareLimits sweep-candidates sweep-choice sweep-wanted sweep-due roots-bytes
                                              prepare-overdue env-capacity WHEEL-UNUSED-SECONDS])
(import doeff_cluster.worker.core.env_rules [ReadyAnswer launch-order prepare-request prepare-argv answer-of-text prepare-outcome
                                             overdue-failure root-project])
(import doeff_cluster.worker.protocol.heartbeat [env-report])


(val ENV-TOOL "doeff_cluster.worker.entry.env_tool")   ; 準備の process の入口(worker 自身の環境の module — root の路は worker に足さない)
(val ROOT-NAME-PATTERN (re.compile r"[0-9a-f]{24}"))
(val PRUNE-EVERY-MS 1800000)    ; uv の cache の prune を起こし直す間隔の下限(node の disk を他の物が使うと掃除では下限に戻らず、拍ごとに起き続けるため)
;; 掃除の頭の行の名(#3713 — 名 + 欄の形: free-bytes = 共有の disk の空き・roots-bytes = roots の合計(hardlink を重ねて数える)・
;; cap-bytes = roots の合計の上限・pinned = 固定の数・candidates = 消してよい root の数・chosen = 選んだ数 — #3732)。
(val SWEEP-LOG "worker: 掃除の選び")
;; 掃除の終わりの行の名(#3715 — 名 + 欄の形: removed = 脇へ退けて消した root の数・leftovers = 消した脇の dir と古い wheel の数・
;; took-ms = 数えの始めから消しの終わりまでの ms)。
(val SWEEP-DONE-LOG "worker: 掃除の終わり")
;; 消すと選んだ root を退ける脇の名の印(.<名>.swept.<時刻> — . で始まる名は完成品としても掃除の候補としても読まれない。worker が
;; 消しの途中で止まって残った脇の dir は、次の掃除の残りの片づけが消す)。
(val SWEPT-MARK ".swept.")


(defrecord EnvSettings
  "実行環境の root の置き場と準備の設定(worker の組み立ての入口 main が作る): state = worker の state の dir(root は state/roots の下)・
   hy-command = 準備の process を起こす hy・platform = この worker の platform(準備の頼みに書く)・code-prepare = 焼く道具の file・
   repo-keys = 鍵の表の JSON の file(URL → deploy key — 表に無い URL は鍵なしで clone)・uv = uv の命令・
   roots-cap-bytes = roots の合計の上限(越えた時だけ固定されていない root を消す — #3732)・min-free-bytes = 共有の disk の空きの最低
   (割った時は root を消さずに準備を disk-full で断り、heartbeat で exhausted を名乗る)・limits = 準備の期限・max-parallel = 同時の準備の
   上限・tool = 準備の process の入口。2 つの量の値は worker の起動の引数(main.hy の --env-roots-cap・--env-min-free — 既定は boot.sh)。"
  (#^ str state)
  (#^ str hy-command)
  (#^ str platform)
  (#^ str code-prepare)
  (#^ int roots-cap-bytes)
  (setv #^ str repo-keys "")
  (setv #^ str uv "uv")
  (setv #^ int min-free-bytes 0)
  (setv #^ PrepareLimits limits (PrepareLimits))
  (setv #^ int max-parallel 2)
  (setv #^ str tool ENV-TOOL))


(defrecord PendingEnv
  "走っている準備 1 本の記録: pid = 準備の子・started-ms = 起こした時刻・result = 答えの file・progress = 進みの印の file・
   warm = 先読みの準備か(job がその root を求めたら job の準備へ上げる)。"
  (#^ int pid)
  (#^ int started-ms)
  (#^ str result)
  (#^ str progress)
  (#^ bool warm))


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


(defk read-answer [path]
  {:pre [(: path str)] :post [(: % (| EnvFailure ReadyAnswer None))]}
  "準備の process が書いた答えの file を読むため(中身の読みは env_rules の answer-of-text)。file が無い・読めない(FileFailed)=
   答えをまだ書いていない(None)。準備の process は答えを別名に書いてから置き換えるので、書きかけは読まない。"
  (<- text (ReadText path))
  (match text
    (str) (do (<- answer (| EnvFailure ReadyAnswer) (answer-of-text text))
              answer)
    _ None))


(defk root-dirs [settings]
  {:pre [(: settings EnvSettings)] :post [(: % tuple)]}
  "state/roots の直下の dir の名(. で始まる名を除く・名の順)を返すため。"
  (<- entries (ListDirectory (+ settings.state "/roots")))
  (if (isinstance entries FileFailed)
      #()
      (tuple (gfor e entries :if (and (= e.kind PathKind.DIRECTORY) (not (.startswith e.name "."))) e.name))))


(defk known-roots [settings]
  {:pre [(: settings EnvSettings)] :post [(: % tuple)] :tags {:context "worker" :role "protocol" :spells "json"}}
  "完成した root の列(展開の複製と bytecode の引き継ぎの元)を、頼みの JSON の形 {\"env\" 宣言 \"root\" path \"madeMs\" 完成の時刻
   \"hyVersion\" マーカーの Hy の compiler の版} で返すため。完成の時刻 = 完成マーカーの mtime(掃除の root-infos と同じ読み — 引き継ぎ元を
   新しい物から選ぶ・#3515 の B)。マーカーを読めても時刻を読めない root(読む間に掃除で消えた)は完成した root に数えない。Hy の版は
   引き継ぎ元の候補の比べにだけ使い(#3706)、完成した root に数えるかには使わない — 欄の無い前の印の root は null で列に入る。"
  (<- names tuple (root-dirs settings))
  (var known #())
  (for [name names]
    (val root (+ settings.state "/roots/" name))
    (<- marker (read-marker root))
    (<- made (| int None) (modified-ms (+ root "/" ENV-MARKER)))
    (when (and (is-not marker None) (is-not made None))
      (:= known (+ known #({"env" (get marker "env") "root" root "madeMs" made "hyVersion" (.get marker "hyVersion")})))))
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
  (<- body dict (prepare-request declared (cut key (len ENV-KEY-PREFIX) None) settings.platform root known settings.min-free-bytes))
  (<- (file-done (WriteText request (json.dumps body :ensure-ascii False))))
  (<- argv tuple (prepare-argv settings.hy-command settings.tool request result settings.state settings.repo-keys settings.code-prepare
                               settings.uv progress))
  ;; 子は worker の環境を継ぐ(env = None)。出力は標準出力と標準エラーを同じ log の末尾へ。
  (<- started (StartProcess :argv argv :stdout-path log :stderr-path log))
  (when (isinstance started ProcessNotStarted)
    (raise (OSError started.detail)))
  (PendingEnv :pid started.pid :started-ms now-ms :result result :progress progress :warm warm))


(defk progressed-ms [pending]
  {:pre [(: pending PendingEnv)] :post [(: % int)]}
  "準備の最後の進み(進みの印の時刻 — 準備の process は処理ステージの頭と、bytecode の処理ステージの中の repo の木ごとに印を触る・
   印が無ければ起こした時刻)を返すため。"
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


(defk disk-free [settings]
  {:pre [(: settings EnvSettings)] :post [(: % int)]}
  "root の置き場の在る共有の disk の空き(byte)を返すため(heartbeat の名乗り・掃除の行・prune の判じが読む)。"
  (<- (file-done (MakeDirectory settings.state)))
  (<- usage (file-done (ReadDiskUsage settings.state)))
  usage.free)


(defk ready-keys [views]
  {:pre [(: views (| tuple None))] :post [(: % frozenset)]}
  "最後の観測(ObserveEnvs)の完成した root のキーの集合を返すため(まだ観測していなければ空)— 掃除の数えを起こすかの比べ(#3732)。"
  (frozenset (gfor view (or views #()) :if (= view.state CodeState.READY) view.revision)))


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


(defrecord MeasuredRoots
  "掃除の数え(ループの外の task)の答え: infos = root ごとの観測(大きさは MeasureTree で数えた byte)。"
  (#^ (get tuple #(RootInfo ...)) infos))


(defrecord RemovedRoots
  "掃除の消し(ループの外の task)の答え: paths = 消した dir(脇へ退けた root・途中で止まった準備の残り・7 日使われない wheel)。"
  (#^ (get tuple #(str ...)) paths))


(defrecord SweepMeasuring
  "走っている掃除の数え: done = 答え(MeasuredRoots)を受ける Promise・started-ms = 掃除を始めた時刻・ready = 数えを始めた拍の完成した
   root のキーの集合(数えの結び RootsTally に載せる — #3732)。"
  (#^ Promise done)
  (#^ int started-ms)
  (#^ (get frozenset str) ready))


(defrecord SweepRemoving
  "走っている掃除の消し: done = 答え(RemovedRoots)を受ける Promise・started-ms = 掃除を始めた時刻(数えの始め)・keys = 脇へ退けた root の
   キー(脇の名は . で始まるので、消している間は選びにも完成品の観測にも出ない)。"
  (#^ Promise done)
  (#^ int started-ms)
  (#^ (get tuple #(str ...)) keys))


(defrecord PruneState
  "uv の cache の prune の記録: pid = 走っている prune の子(None = 走っていない)・started-ms = 最後に起こした時刻(0 = まだ起こしていない)。"
  (setv #^ (| int None) pid None)
  (setv #^ int started-ms 0))


(defk sweep-leftovers [settings now-ms]
  {:pre [(: settings EnvSettings) (: now-ms int)] :post [(: % tuple)]}
  "脇の dir — 消すと選んで退けた root(.<名>.swept.<時刻>)と途中で止まった準備の残り(.<名>.broken.<時刻>)— と、7 日使われない native の
   wheel(使うたびに dir の中の印の file を置き換えて dir の時刻を進める — env_handlers の EnsureNativeWheel)を消し、消した path の列を
   返すため。"
  (<- roots (ListDirectory (+ settings.state "/roots")))
  (val aside (if (isinstance roots FileFailed)
                 #()
                 (tuple (gfor entry roots :if (and (.startswith entry.name ".") (or (in ".broken." entry.name) (in SWEPT-MARK entry.name)))
                              (+ settings.state "/roots/" entry.name)))))
  (<- wheels (ListDirectory (wheels-root settings.state)))
  (var stale #())
  (when (not (isinstance wheels FileFailed))
    (for [entry wheels]
      (when (= entry.kind PathKind.DIRECTORY)
        (val path (+ (wheels-root settings.state) "/" entry.name))
        (<- at (modified-ms path))
        (when (and (is-not at None) (> (- now-ms at) (* 1000 WHEEL-UNUSED-SECONDS)))
          (:= stale (+ stale #(path)))))))
  (val doomed (+ aside stale))
  (for [target doomed]
    (<- (RemoveTree target)))
  doomed)


(defk measuring-roots [settings done]
  {:pre [(: settings EnvSettings) (: done Promise)] :post [(: % None)]}
  "掃除の数え(root ごとの MeasureTree — root 1 つが数千の file の木)をループの外の task で行い、答えを done へ渡すため(#3715)。
   失敗も done へ渡す(渡さないと掃除が走り続けていると読まれ、次の掃除が起きない — 失敗はループが答えを読んだ拍で上がる)。"
  (try
    (<- infos tuple (root-infos settings))
    (<- (CompletePromise done (MeasuredRoots :infos infos)))
    (except [error Exception]
      (<- (FailPromise done error))))
  None)


(defk removing-leftovers [settings now-ms done]
  {:pre [(: settings EnvSettings) (: now-ms int) (: done Promise)] :post [(: % None)]}
  "掃除の消し(脇へ退けた root の木と残りの片づけ — sweep-leftovers)をループの外の task で行い、答えを done へ渡すため(#3715)。
   失敗も done へ渡す(measuring-roots と同じ訳)。"
  (try
    (<- removed tuple (sweep-leftovers settings now-ms))
    (<- (CompletePromise done (RemovedRoots :paths removed)))
    (except [error Exception]
      (<- (FailPromise done error))))
  None)


(defk start-measuring [settings ready now-ms]
  {:pre [(: settings EnvSettings) (: ready frozenset) (: now-ms int)] :post [(: % SweepMeasuring)]}
  "掃除の数えをループの外の task として起こし、走っている数えの記録を返すため(ループは待たない — 答えは後の拍で sweep-answer が読む)。
   ready = この拍の完成した root のキーの集合。"
  (<- done Promise (CreatePromise))
  (<- (Spawn (measuring-roots settings done) :daemon True))
  (SweepMeasuring :done done :started-ms now-ms :ready ready))


(defk sweep-answer [work]
  {:pre [(: work (| SweepMeasuring SweepRemoving))] :post [(: % (| MeasuredRoots RemovedRoots None))]}
  "走っている掃除の答えを待たずに読むため(まだなら None — 待つ秒 0 の WaitWithin)。task が渡した失敗はここで上がる。"
  (<- answer (WaitWithin work.done.future 0.0))
  answer)


(defk set-aside [settings key now-ms]
  {:pre [(: settings EnvSettings) (: key str) (: now-ms int)] :post [(: % bool)]}
  "消すと選んだ root を . で始まる脇の名へ退けるため(名の付け替え 1 回 — 木の消しはループの外の task)。答え = 退けたか(root が
   もう無ければ False)。"
  (<- root str (env-root settings key))
  (<- moved (RenamePath root (.format "{}/roots/.{}{}{}" settings.state (cut key (len ENV-KEY-PREFIX) None) SWEPT-MARK now-ms)))
  (not (isinstance moved FileFailed)))


(defk start-removing [settings measured busy started-ms]
  {:pre [(: settings EnvSettings) (: measured MeasuredRoots) (: busy frozenset) (: started-ms int)] :post [(: % SweepRemoving)]}
  "数えの答えが届いた拍で、その拍の固定(busy — 数えの間に固定になった root も入る)と roots の合計の上限で消す root を選び、脇へ退け、
   消しをループの外の task として起こすため。答え = 走っている消しの記録。"
  (<- free int (disk-free settings))
  (<- total int (roots-bytes measured.infos))
  (val cap settings.roots-cap-bytes)
  (<- candidates tuple (sweep-candidates measured.infos busy))
  (<- chosen tuple (sweep-choice measured.infos busy cap))
  ;; 掃除の選びの 1 行(#3713 — 何も選ばなかった回も出す): 空き・roots の合計・上限・固定の数・候補の数・選んだ数。
  (<- (slog SWEEP-LOG :level "info" :free-bytes free :roots-bytes total :cap-bytes cap :pinned (len busy) :candidates (len candidates)
            :chosen (len chosen)))
  (<- now-ms int (now-epoch-ms))
  (var aside #())
  (for [key chosen]
    (<- (slog (.format "worker: 掃除 — 固定されていない root {} を消す(roots の合計 {} byte > 上限 {} byte)" (cut key (len ENV-KEY-PREFIX) None)
                       total cap)))
    (<- moved bool (set-aside settings key now-ms))
    (when moved
      (:= aside (+ aside #(key)))))
  (<- done Promise (CreatePromise))
  (<- (Spawn (removing-leftovers settings now-ms done) :daemon True))
  (SweepRemoving :done done :started-ms started-ms :keys aside))


(defk pruned-after [settings prune preparing now-ms]
  {:pre [(: settings EnvSettings) (: prune PruneState) (: preparing bool) (: now-ms int)] :post [(: % PruneState)]}
  "消しの終わった拍で、共有の disk の空きが最低(min-free-bytes)を割っていれば uv の cache を prune するため(venv の中の file は
   hardlink なので残る — #3732 の前は掃除の下限で判じていた)。答え = 次の prune の
   記録。prune は uv の cache の lock を取るので、待つと root の準備の uv run が終わるまで worker のループ(heartbeat)が止まる — 別の
   process として起こして待たない。前の prune が走っている間と、root の準備が走っている間(preparing)と、前の prune から
   PRUNE-EVERY-MS の間は起こさない(準備と lock を競わない・他の物が使う node の disk では prune で下限に戻らないので、掃除ごとに
   起こし続けない)。"
  (var pid prune.pid)
  (when (is-not pid None)
    (<- polled (PollProcess pid))
    (when (not (isinstance polled ProcessRunning)) (:= pid None)))
  (<- free int (disk-free settings))
  (if (and (< free settings.min-free-bytes) (is pid None) (not preparing)
           (or (= prune.started-ms 0) (>= (- now-ms prune.started-ms) PRUNE-EVERY-MS)))
      (do (<- started (StartProcess :argv #(settings.uv "cache" "prune") :env-mode EnvMode.EXTEND
                                    :env #((EnvEntry :name "UV_CACHE_DIR" :value (+ settings.state "/uv-cache"))) :process-group True))
          (PruneState :pid (if (isinstance started ProcessNotStarted) None started.pid) :started-ms now-ms))
      (PruneState :pid pid :started-ms prune.started-ms)))


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
    ;; 答えの file は期限を判じる前に読む: 完成を書き終えて終わりの処理の途中の準備を、期限の拍で止めない(#3515 — 完成を書いた
    ;; 0.4 秒後に prepare-timeout にしていた)。答えの file が無い・読めない = 答えを書いていない(None)。
    (<- answer (| EnvFailure ReadyAnswer None) (read-answer p.result))
    (if (isinstance polled ProcessRunning)
        (match answer
          ;; 完成を書いた準備は止めない — 次の観測で終わり(ProcessExited)と答えを読む。
          (ReadyAnswer) None
          _ (do (<- progressed int (progressed-ms p))
                (<- overdue bool (prepare-overdue (/ progressed 1000.0) (/ now-ms 1000.0) settings.limits))
                (when overdue
                  (<- (StopProcess :pid p.pid :stop-grace 0.0))
                  (:= running (dfor #(k v) (.items running) :if (!= k key) k v))
                  (<- failure (overdue-failure p.warm settings.limits))
                  (:= failures (| failures {key #(failure now-ms)})))))
        (do (:= running (dfor #(k v) (.items running) :if (!= k key) k v))
            ;; 答えを書かずに終わった準備は、prepare-outcome が log の在処を添えた失敗にする。
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
  ;; 固定の集合 pinned-roots(最後に受けた SweepEnvs の固定 — 判断の側の statuses の held と別の物)・最後の掃除の終わりの時刻・
  ;; 走っている掃除(数えか消し — 同時に 1 つ)・最後の数えの結び tally(RootsTally — まだ数えていなければ None・#3732)・prune の記録・
  ;; 最後の観測(heartbeat の名乗りが読む)。
  (session var waiting {})
  (session var pending {})
  (session var failed {})
  (session var pinned-roots (frozenset))
  (session var swept-ms 0)
  (session var sweeping None)
  (session var tally None)
  (session var prune (PruneState))
  (session var views None)
  (PrepareEnv [key runtime-env warm]
    ;; job の頼み(warm = False)は、同じ root の先読みが走っていれば job の準備へ上げ(同時の枠の数え方が job の物になる — 期限は
    ;; 先読みと同じ停滞の長さ)、待っていれば先読みの印を下ろす。
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
    ;; 掃除の係が拍を求めているか(sweep-wanted)は最後の観測の完成した root の集合と最後の数えの結びで判じる(#3732)。
    (<- free int (disk-free settings))
    (<- ready frozenset (ready-keys views))
    (<- wanted bool (sweep-wanted (is-not sweeping None) tally ready settings.roots-cap-bytes))
    (resume (EnvDisk :free free :sweep-wanted wanted :pinned pinned-roots)))
  (EnvReport []
    ;; heartbeat で名乗る root の姿(coordinator の置き先と温める表の読みが使う): 最後の観測(まだ無ければ今観測する)と disk の条件
    ;; (形は env-report — sim の宿と同じ関数)。
    (when (is views None)
      (<- observed tuple (observe-envs settings waiting pending failed))
      (:= waiting (get observed 0))
      (:= pending (get observed 1))
      (:= failed (get observed 2))
      (:= views (get observed 3)))
    (<- free int (disk-free settings))
    (<- capacity str (env-capacity free settings.min-free-bytes))
    (resume (env-report (or views #()) capacity)))
  (SweepEnvs [pinned]
    ;; 固定の集合を持ち替え、掃除を 1 歩進める(#3715 — 数えと消しはループの外の task・ループは待たない・走っている掃除は同時に 1 つ):
    ;;   走っていない → 始める時(sweep-due — まだ数えていない・完成した root の集合が変わった・上限を越えたまま)なら数えを起こす
    ;;   数えている   → 答えが届いていれば、数えの結び(tally)を持ち替え、この拍の固定と上限で選び、選んだ root を脇へ退けて消しを起こす
    ;;   消している   → 終わっていれば終わりの 1 行を出し、共有の disk の空きが最低を割っていれば uv の cache の prune を起こす
    ;; 初期値の空との比べで「変わった」と判じる最初の SweepEnvs は、worker が最初の宣言を読んだ拍の物(読む前は判断の側
    ;; policy.sweep-actions が撃たない — #3731)。ここで二重に止めない。
    (val changed (!= pinned pinned-roots))
    (:= pinned-roots pinned)
    (<- now-ms int (now-epoch-ms))
    (match sweeping
      None
        (do (<- ready frozenset (ready-keys views))
            (<- due bool (sweep-due tally ready settings.roots-cap-bytes changed now-ms swept-ms))
            (when due
              (<- measuring SweepMeasuring (start-measuring settings ready now-ms))
              (:= sweeping measuring)))
      (SweepMeasuring)
        (do (<- measured (| MeasuredRoots None) (sweep-answer sweeping))
            (when (is-not measured None)
              (<- total int (roots-bytes measured.infos))
              (:= tally (RootsTally :ready sweeping.ready :bytes total))
              ;; 固定には走っている準備(pending と waiting)も足す(判断の側の観測より新しいので)。数えの間に固定になった root も入る。
              (<- removing SweepRemoving (start-removing settings measured (| pinned-roots (frozenset pending) (frozenset waiting)) sweeping.started-ms))
              (:= sweeping removing)))
      (SweepRemoving)
        (do (<- removed (| RemovedRoots None) (sweep-answer sweeping))
            (when (is-not removed None)
              (<- (slog SWEEP-DONE-LOG :level "info" :removed (len sweeping.keys) :leftovers (- (len removed.paths) (len sweeping.keys))
                        :took-ms (- now-ms sweeping.started-ms)))
              (<- next-prune PruneState (pruned-after settings prune (bool (or pending waiting)) now-ms))
              (:= prune next-prune)
              (:= swept-ms now-ms)
              (:= sweeping None))))
    (resume None)))
