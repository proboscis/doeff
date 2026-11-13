;;; worker の実行環境(runtime env)の root の言い換え(handlers.hy の EnvStore を置き換えた・#2467)— PrepareEnv・SweepEnvs と観測
;;; ObserveEnvs・ObserveEnvDisk・heartbeat の名乗り EnvReport を、汎用の子 process の効果(StartProcess・PollProcess・StopProcess)と
;;; file system の効果(StatPath・ReadText・WriteText・ListDirectory・StatTree・RenamePath・MakeDirectory・RemoveTree・ReadDiskUsage・MeasureTree)
;;; へ言い換える。I/O を持たない — 本物は外側の subprocess-handler と os-file-handler。
;;;
;;; 振る舞いは前の EnvStore と同じ:
;;;   * root を env のキーごとに準備する。準備は worker 自身の code の env_handlers を別の process として起こし(worker のループは待たない)、
;;;     完成マーカーの在る root だけを READY として観測する。root は分け合う dir(settings.shared — 無ければ state)の roots/<キー> の最終の
;;;     path に作る(venv が絶対 path を持つので rename しない)。同じ機体に同じ名の worker の 2 つの世代が並ぶ時、新しい世代は前の世代の
;;;     準備した root を準備し直さずに使う(AL-D2g)。マーカーの無い root は、準備の process がキーごとの錠の中で脇へ退けて作り直す
;;;     (もう片方の世代が準備している root を退けない — env_translation の prepared-once)。準備は同時に max-parallel 本まで・同じキーは 1 本。
;;;   * 先読み(warm): 温める表の root は job の準備より後に起こし、同時の枠の 1 つを job に残す(起こす順と数は env_rules.launch-order)。
;;;     期限は env_upkeep.prepare-overdue(先読みも job の準備も、進みの印が動かない長さだけ)。期限を判じる前に答えの file を読み、
;;;     完成を書いた準備(終わりの処理の途中)は止めない — 次の観測で終わりを読む。
;;;   * 掃除(sweep): roots の合計が上限(settings.roots-cap-bytes)を越えた時か、共有の disk の空きが最低 settings.min-free-bytes を
;;;     割った時に、固定されていない root を古い順に消す(選びは env_upkeep.sweep-choice — project ごとの新しい 2 つは残す・#3732・#4051。
;;;     候補を全部消しても空きが戻らない時は準備を disk-full で断る)・uv の cache を prune(待たない)・
;;;     7 日使われない wheel と、root の宣言が指さず 7 日使われない repo の bare mirror(url の錠を待たずに取れた物だけ)を消す。
;;;     消すのは worker が作った dir だけ。数えは前の数えと完成の時刻・使った時刻が同じ root を歩かずに
;;;     大きさを使い回す(env_upkeep の kept-bytes)。数え(root ごとの MeasureTree)と消し(木の RemoveTree)はループの外の
;;;     task で走らせ、ループは待たない(#3715 — 2026-10-06 05:24〜05:36 に root 25 個の数えと消しがループの中で 11 分走り、heartbeat が途絶えて
;;;     worker が自分で job を止めた)。task の中でも同期の effect は scheduler の thread を占めるので、木を相手にする effect(MeasureTree・
;;;     RemoveTree・bytecode の保存先の様子つきの歩き StatTree)は本番の offloaded-tree-handler が thread で待つ(
;;;     保存先を WalkTree と file ごとの StatPath で歩いた掃除が node-h で scheduler を 10〜19 秒止めた)。
;;;     選びは数えの答えが届いた拍で、その時の固定(宣言の root・走り中の process・準備中・温める表)で行い、
;;;     選んだ root はその拍で . で始まる脇の名へ退けてから消す(消している間は選びにも完成品の観測にも出ない)。走っている掃除は同時に 1 つ。
;;;     固定は世代ごとに分け合う dir の root-pins/ へ file で知らせ合い、掃除はまだ走っている他の世代(状態の dir が在る)の固定も消さない
;;;     (AL-D2g)。
;;; 記録(待ち・準備中・失敗・固定の集合・掃除と prune の時刻・最後の観測)は handler の session の値で持つ。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass replace])
(import hashlib)
(import json)
(import posixpath)
(import re)
(import doeff_core_effects [slog])
(import doeff_core_effects [Spawn CreatePromise CompletePromise FailPromise Promise])
(import doeff_core_effects [Compute Computed ComputeFailed])
(import doeff_time [WaitWithin])
(import doeff_core_effects [PathKind FileFailed StatPath ReadText WriteText ListDirectory StatTree RenamePath MakeDirectory RemoveTree ReadDiskUsage MeasureTree
                           TryLock LockHeld LockBusy ReleaseLock]
        doeff_core_effects._file_effects [file-done])
(import doeff_core_effects [EnvEntry EnvMode StartProcess PollProcess StopProcess ProcessNotStarted ProcessRunning ProcessExited AwaitProcessExit RunProcess ProcessOutcome])
(import doeff_cluster._shared.core.clock [now-epoch-ms])
(import doeff_cluster._shared.core.native_wheel [wheels-root])
(import doeff_cluster._shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster._shared.intent.runtime_env_model [EnvFailure RuntimeEnv RuntimeEnvInvalid])
(import doeff_cluster._shared.core.runtime_env_rules [runtime-env-of-json dependency-key])
(import doeff_cluster._worker.intent.worker_model [CodeState CodeView EnvDisk PrepareEnv SweepEnvs EnvReport WakeSet WorkerWakes])
(import doeff_cluster._worker.core.worker_due [wakes-with prepare-stop-due sweep-interval-due])
(import doeff_cluster._shared.core.due_policy [earliest-due])
(import doeff_cluster._shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster._worker.protocol.observations [ObserveEnvs ObserveEnvDisk])
(import doeff_cluster._worker.core.worker_rules [ENV-KEY-PREFIX])
(import doeff_cluster._worker.core.launch [entry-place-of-marker])
(import doeff_cluster._worker.core.job_cgroup [prepare-group-dir grouped-argv closing-argv placed-argv])
(import doeff_cluster._worker.core.env_upkeep [RootInfo RootsTally PrepareLimits sweep-candidates sweep-choice sweep-wanted sweep-due roots-bytes kept-bytes
                                              prepare-overdue env-capacity WHEEL-UNUSED-SECONDS CODE-STORE-UNUSED-SECONDS MIRROR-UNUSED-SECONDS unused-mirror-names MemoryUse LayerMemoryUse MemoryUnread WarmRoom
                                              LayerSample memory-use-of layer-memory-use-of unreclaimable-of layer-sample->json sampled-layer root-estimate build-memory-estimate reclaimable-bytes warm-refusal
                                              DEFAULT-BUILD-MEMORY-BYTES layer-need])
(import doeff_cluster._worker.core.env_rules [ReadyAnswer PrepareRoom launch-order prepare-request prepare-argv answer-of-text prepare-outcome
                                             overdue-failure root-project declared-project oom-kills-of memory-killed-failure])
(import doeff_cluster._worker.core.worker_due [due-after])
(import doeff_cluster._worker.protocol.heartbeat [env-report])
(import doeff_cluster._worker.protocol.env_translation [digest16 listed-url])


(val ENV-TOOL "doeff_cluster._worker.entry.env_tool")   ; 準備の process の入口(worker 自身の環境の module — root の路は worker に足さない)
(val ROOT-NAME-PATTERN (re.compile r"[0-9a-f]{24}"))
(val PRUNE-EVERY-MS 1800000)    ; uv の cache の prune を起こし直す間隔の下限(node の disk を他の物が使うと掃除では下限に戻らず、拍ごとに起き続けるため)
;; 掃除の頭の行の名(#3713 — 名 + 欄の形: free-bytes = 共有の disk の空き・roots-bytes = roots の合計(hardlink を重ねて数える)・
;; cap-bytes = roots の合計の上限・pinned = 固定の数・candidates = 消してよい root の数・chosen = 選んだ数 — #3732・walked = この数えで
;; 木を歩いた root の数 — 前の数えから大きさを使い回した root は数えない)。
(val SWEEP-LOG "worker: 掃除の選び")
;; 掃除の終わりの行の名(#3715 — 名 + 欄の形: removed = 脇へ退けて消した root の数・leftovers = 消した脇の dir と古い wheel の数・
;; took-ms = 数えの始めから消しの終わりまでの ms)。
(val SWEEP-DONE-LOG "worker: 掃除の終わり")
;; 使われない repo の bare mirror を消した行の名(名 + 欄の形: mirror = mirrors/ の下の dir の名・unused-ms = 最後に使ってからの ms)。
(val MIRROR-SWEPT-LOG "worker: 使われない mirror を消した")
;; 鍵の表を読めず、mirror の名を決められないので mirror を消さなかった行の名(path = 鍵の表の file)。
(val MIRRORS-UNKEYED-LOG "worker: 鍵の表を読めない — mirror を消さない")
;; 消すと選んだ root を退ける脇の名の印(.<名>.swept.<時刻> — . で始まる名は完成品としても掃除の候補としても読まれない。worker が
;; 消しの途中で止まって残った脇の dir は、次の掃除の残りの片づけが消す)。
(val SWEPT-MARK ".swept.")
;; 先の組みを始めずに断った行の名(#3748 — #3713 の形 = 名 + 欄: key = root のキー・kind = 断りの種類〔EnvFailureKind の綴り〕・
;; free-bytes・min-free-bytes・root-bytes = root 1 つの見積もり・roots-bytes = roots の合計〔まだ数えていなければ -1〕・reclaimable-bytes・
;; cap-bytes・memory-current・memory-max〔測れなければ -1〕・peak-bytes = 組みの山の見積もり)。
(val WARM-REFUSED-LOG "worker: 先の組みを断った")
;; 固定を世代の間で知らせ合う file の dir の名(分け合う dir の下 — AL-D2g)。file 1 つが世代 1 つ: {"state" 世代の状態の dir "keys" 固定の
;; キーの列}。掃除は、状態の dir がまだ在る他の世代の固定も消さない。
(val PINS-DIR "root-pins")
;; 固定の file を書けなかった行の名(欄: path・detail — 掃除は止めない)。
(val PINS-UNWRITTEN-LOG "worker: 固定の file を書けない")
;; 先の組みの前に memory を測れなかった行の名(#3748 — 欄: key・reason = 訳)。断らずに組み、heartbeat の memoryUnmeasured に root のキーを載せる。
(val MEMORY-UNMEASURED-LOG "worker: 先の組みの memory を測れない")
;; 終わった準備の子の cgroup を消せなかった行の名(欄: key・group = 子の dir・detail = 止めの script の stderr)。
(val GROUP-UNCLOSED-LOG "worker: 準備の子の cgroup を消せない")
;; 準備の子の止めと消しを待つ秒の上限(止めの script は SIGKILL の後の退きを 5 秒まで待つ)。
(val CLOSE-TIMEOUT-SECONDS 10.0)
;; 準備の層の memory の上限で殺された準備を、失敗にせず待ちへ戻した行の名(欄: key・oom-kills = 準備の子の memory.events の oom_kill —
;; 層が空いてから 1 本だけで起こし直す)。
(val MEMORY-KILLED-LOG "worker: 準備が層の memory の上限で殺された — 層が空いてから 1 本で起こし直す")
;; 準備の層の空きが足りずに待っている準備が在る間、層を読み直す間隔(ms — 層の空きは worker の外の物〔keeper の版の準備〕の終わりでも
;; 戻り、その終わりは worker を起こさない)。
(val ROOM-RECHECK-MS 5000)


(defrecord EnvSettings
  "実行環境の root の置き場と準備の設定(worker の組み立ての入口 main が作る): state = worker の state の dir(準備の要求の置き場)・
   hy-command = 準備の process を起こす hy・platform = この worker の platform(準備の頼みに書く)・code-prepare = 焼く道具の file・
   repo-keys = 鍵の表の JSON の file(URL → deploy key — 表に無い URL は鍵なしで clone)・repo-access = 鍵の表を組み直す起動の script
   (準備の前ごとに ROLE=access で撃ち、表の file の今の値から JSON と git / ssh の設定を組み直す — 空 = 組み直さない)・
   uv = uv の命令・uv-cache = 準備の uv の子の
   cache の dir(UV_CACHE_DIR・prune もこの dir — 値は main の --uv-cache・起動の script が DOEFF_UV_CACHE_DIR を渡す・#3858)・
   roots-cap-bytes = roots の合計の上限(越えた時は固定されていない root を消す — #3732)・min-free-bytes = 共有の disk の空きの最低
   (割った時は固定されていない root を古い順に消し — #4051、戻るまでは準備を disk-full で断り、heartbeat で exhausted を名乗る)・limits = 準備の期限・max-parallel = 同時の準備の
   上限・tool = 準備の process の入口・cgroup-dir = worker の container の cgroup(v2)の dir(先の組みの前に memory.current と memory.max を
   読む — #3748)・code-store = source の中身で引く bytecode の保存先の dir(掃除が 7 日使われない entry を消す・None = 保存先を使わない
   設定 — 値は main の --code-store・起動の script が DOEFF_HY_CODE_STORE を渡す・#3858)・shared = 世代をまたいで分け合う dir(root の
   置き場 roots・固定の file・Python の置き場・Rust の wheel の保存先・mirror とその錠・wheel と root の錠 — 値は main の --shared-dir・
   None = state と同じ dir。同じ機体に同じ名の worker の 2 つの世代が並ぶ時、準備の要求は世代ごとの state に置き、root と重い cache を
   分け合う・AL-D2・AL-D2g)・prepare-procs = 準備の process を置く兄弟の cgroup(準備の層)の cgroup.procs(None = 置き直さない — 外側の
   job-cgroup-placer が jobs の層へ置く)。準備の層は機体の配備が準備のために取り置く memory の上限で、worker は走っている準備の分を
   jobs の上限から引く(worker/core/eviction_rules の prepare-reserve)。jobs の層で焼くと、動く job と memory を分け合い、焼く子が層の
   上限で殺されて準備が落ちた(2026-11-02 の node-d — jobs 896 MiB のうち動く job が約 450 MiB)。2 つの量の値は worker の
   起動の引数(main.hy の --env-roots-cap・--env-min-free — 既定は boot.sh)。prepare-layer-dir = 準備の process が置かれる cgroup の層の dir
   (先の組みの入口はこの層の memory で判じる — core/env_upkeep の warm-refusal。None = 層が無い機体・container の読みで判じる)・
   service-procs = 常駐の job(service)の root の準備(PrepareEnv の service)を置く service の兄弟の cgroup の cgroup.procs(準備の層が無い
   機体だけ使う・None = 置き直さない)。jobs の層は task の CPU の枠(機体の配備の cpu.max と cpu.idle)の下に在り、service の版の切り替えの
   準備(約 1 分の CPU)が task と枠を分け合うと 6 分を越えた。準備の process の CPU の優先度は今までどおり下げる(lowered-priority-argv —
   service の兄弟の中でも、動いている service に必ず譲る)。"
  (#^ str state)
  (#^ str hy-command)
  (#^ str platform)
  (#^ str code-prepare)
  (#^ int roots-cap-bytes)
  (#^ str uv-cache)
  (setv #^ str repo-keys "")
  (setv #^ str repo-access "")
  (setv #^ str uv "uv")
  (setv #^ int min-free-bytes 0)
  (setv #^ PrepareLimits limits (PrepareLimits))
  (setv #^ int max-parallel 2)
  (setv #^ str tool ENV-TOOL)
  (setv #^ str cgroup-dir "/sys/fs/cgroup")
  (setv #^ (| str None) code-store None)
  (setv #^ (| str None) shared None)
  (setv #^ (| str None) prepare-procs None)
  (setv #^ (| str None) prepare-layer-dir None)
  (setv #^ (| str None) service-procs None))


(defrecord PendingEnv
  "走っている準備 1 本の記録: pid = 準備の子・started-ms = 起こした時刻・result = 答えの file・progress = 進みの印の file・
   warm = 先読みの準備か(job がその root を求めたら job の準備へ上げる)・memory = 層の読みを準備の process へ渡す file・sample = 組みの
   置かれる層の回収できない使いの、起こす前の読みと準備の間の最大(層の無い・読めない機体は None — sample-layers)・group = 準備の層の
   下のこの準備の子の dir(層が無い機体は None — core/job_cgroup の「準備ごとの子」)・runtime-env と compile-jobs = 頼み(層の memory の
   上限で殺された時に待ちへ戻す)・need = 起こした時に層の空きから取り置いた見込みの山(byte — 層で絞らない worker は 0)・alone = 1 本
   だけで起こした準備か(層の memory の上限で殺されて起こし直した物)・service = 常駐の job の root の準備か(PrepareEnv の service)。"
  (#^ int pid)
  (#^ int started-ms)
  (#^ str result)
  (#^ str progress)
  (#^ bool warm)
  (setv #^ str memory "")
  (setv #^ (| LayerSample None) sample None)
  (setv #^ (| str None) group None)
  (setv #^ str runtime-env "")
  (setv #^ (| int None) compile-jobs None)
  (setv #^ int need 0)
  (setv #^ bool alone False)
  (setv #^ bool service False))


(defrecord WaitingEnv
  "起こすのを待っている準備 1 本の頼み(待ちの記録の値 — キー → この record): runtime-env = 宣言の JSON の文字列・compile-jobs = bytecode を
   焼く道具の並べる数(None = 道具の既定 — PrepareEnv と同じ・2026-10-08)・warm = 先読みの準備か・alone = 1 本だけで起こし直す準備か
   (準備の層の memory の上限で殺された準備 — env_rules の launch-order)・service = 常駐の job の root の準備か(PrepareEnv の service)。"
  (#^ str runtime-env)
  (#^ (| int None) compile-jobs)
  (#^ bool warm)
  (setv #^ bool alone False)
  (setv #^ bool service False))


(defk roots-dir [settings]
  {:pre [(: settings EnvSettings)] :post [(: % str)]}
  "root の置き場の dir を返すため — 分け合う dir(無ければ state)の roots。同じ機体に同じ名の worker の 2 つの世代が並ぶ時、新しい世代は
   前の世代の準備した root をそのまま使う(AL-D2g — 世代ごとの state に置くと、入れ替えのたびに全 root を準備し直した)。"
  (+ (or settings.shared settings.state) "/roots"))


(defk env-root [settings key]
  {:pre [(: settings EnvSettings) (: key str)] :post [(: % str)]}
  "env のキー(env-<キー>)の root の path を返すため。"
  (<- roots str (roots-dir settings))
  (+ roots "/" (cut key (len ENV-KEY-PREFIX) None)))


(defk read-marker [root]
  {:pre [(: root str)] :post [(: % (| dict None))]}
  "root の完成マーカーの中身を返すため(無い・読めなければ None)。"
  (<- text (ReadText (+ root "/" ENV-MARKER)))
  (if (isinstance text str)
      (try (json.loads text) (except [ValueError] None))
      None))


(defk root-entry-place [root]
  {:pre [(: root str)] :post [(: % (| str None))]}
  "root へ送る子の入口と待ちの子の名を選ぶため、root の完成の印から子の入口の置き場を読む(起こす宿・入口の
   確かめ・待ちの子の宿が読む。印の中身の読みは launch の entry-place-of-marker)。"
  (<- text (ReadText (+ root "/" ENV-MARKER)))
  (<- place (| str None) (entry-place-of-marker (if (isinstance text str) text None)))
  place)


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
  "root の置き場の直下の dir の名(. で始まる名を除く・名の順)を返すため。"
  (<- roots str (roots-dir settings))
  (<- entries (ListDirectory roots))
  (if (isinstance entries FileFailed)
      #()
      (tuple (gfor e entries :if (and (= e.kind PathKind.DIRECTORY) (not (.startswith e.name "."))) e.name))))


(defk known-roots [settings]
  {:pre [(: settings EnvSettings)] :post [(: % tuple)] :tags {:context "worker" :role "protocol" :spells "json"}}
  "完成した root の列(木を写す元の候補)を、頼みの JSON の形 {\"env\" 宣言 \"root\" path \"platform\" マーカーの platform \"madeMs\" 完成の時刻
   \"hyVersion\" マーカーの Hy の compiler の版} で返すため。写し元は repo ごとに、同じ repo を持つ root のうち完成の時刻が最も新しい物
   (#3677)。完成の時刻 = 完成マーカーの mtime(掃除の root-infos と同じ読み)。マーカーを読めても時刻を読めない root(読む間に掃除で
   消えた)は完成した root に数えない。Hy の版は報告の値で(#3706)、完成した root に数えるかには使わない — 欄の無い前の印の root は null で
   列に入る。"
  (<- names tuple (root-dirs settings))
  (<- roots str (roots-dir settings))
  (var known #())
  (for [name names]
    (val root (+ roots "/" name))
    (<- marker (read-marker root))
    (<- made (| int None) (modified-ms (+ root "/" ENV-MARKER)))
    (when (and (is-not marker None) (is-not made None))
      (:= known (+ known #({"env" (get marker "env") "root" root "platform" (.get marker "platform") "madeMs" made
                            "hyVersion" (.get marker "hyVersion")})))))
  known)


;; 鍵の表を組み直す子の上限の秒(表の行ごとに printf を数回撃つだけ — 届かなければ前の表のまま準備する)。
(val REPO-ACCESS-TIMEOUT 30.0)


(defk refreshed-repo-access [settings]
  {:pre [(: settings EnvSettings)] :post [(: % None)]}
  "準備の process を起こす前に、鍵の表をその時の値で組み直すため(起動し直さずに表の変更を読む)。表の file
   (WORKER_REPOS_FILE)の今の値から、起動の script の ROLE=access が鍵の表の JSON と git / ssh の設定を別名に書いてから置き換える。
   組み直せない(鍵の file が無い・形の違う url・時間切れ)時は、置き場の file は前の値のまま残るので、断りを log に 1 行書いて前の表で
   準備する(準備は止めない)。repo-access が空の worker(表を env だけで受ける)は何もしない。"
  (when (not settings.repo-access)
    (return None))
  (<- outcome ProcessOutcome (RunProcess :argv #("sh" settings.repo-access) :env #((EnvEntry :name "ROLE" :value "access"))
                                         :env-mode EnvMode.EXTEND :timeout REPO-ACCESS-TIMEOUT))
  (when (!= outcome.exit-code 0)
    (<- (slog "worker: 鍵の表を組み直せない — 前の表のまま準備する" :exit-code outcome.exit-code
              :detail (cut (.strip (or outcome.stderr outcome.start-error)) -300 None))))
  None)


(defk launch-prepare [settings key runtime-env compile-jobs warm [need 0] [alone False] [service False]]
  {:pre [(: settings EnvSettings) (: key str) (: runtime-env str) (: compile-jobs (| int None)) (: warm bool) (: need int) (: alone bool)
         (: service bool)]
   :post [(: % PendingEnv)]}
  "root の準備の process を 1 本起こして記録を返すため。マーカーの無い root(途中で止まった準備)は、準備の process がキーごとの錠を
   取ってから脇へ退ける(AL-D2g — root の置き場を分け合う他の世代がそのキーを準備している間に、ここで退けるとその準備を壊す)。
   compile-jobs = 焼く道具の並べる数(頼みの JSON の compileJobs に書く)・need = 層の空きから取り置く見込みの山・alone = 1 本だけで
   起こす準備か(記録に残す — PendingEnv)・service = 常駐の job の root の準備か(準備の層が無ければ service の兄弟の cgroup に置く)。"
  (<- root str (env-root settings key))
  (<- now-ms int (now-epoch-ms))
  (val requests (+ settings.state "/env-requests"))
  (<- (file-done (MakeDirectory requests)))
  (val declared (json.loads runtime-env))
  (val request (+ requests "/" key ".json"))
  (val result (+ requests "/" key ".result.json"))
  (val progress (+ requests "/" key ".progress"))
  (val log (+ requests "/" key ".log"))
  (val memory (+ requests "/" key ".memory"))
  (<- (RemoveTree result))     ; 無い file の断りは捨てる
  (<- (RemoveTree progress))
  (<- (RemoveTree memory))
  ;; 組みの山の元(memory.peak の無い kernel — sample-layers): 起こす前の層の回収できない使い。
  (<- base (| int None) (layer-held settings))
  (<- known tuple (known-roots settings))
  (<- body dict (prepare-request declared (cut key (len ENV-KEY-PREFIX) None) settings.platform root known settings.min-free-bytes
                                 compile-jobs))
  (<- (file-done (WriteText request (json.dumps body :ensure-ascii False))))
  (<- (refreshed-repo-access settings))
  (<- argv tuple (prepare-argv settings.hy-command settings.tool request result settings.state (or settings.shared settings.state)
                               settings.uv-cache settings.repo-keys
                               settings.code-prepare settings.uv progress settings.platform memory))
  ;; 準備の層が在れば、準備の process(と、それが起こす git・uv・焼く道具の子)を層の下のこの準備の子へ置く(EnvSettings の prepare-procs の
  ;; 註・core/job_cgroup の頭の註の「準備ごとの子」— OOM kill は木を丸ごと止め、終わりに closed-group が残りを止める)。
  (var placed argv)
  (var group None)
  (when (is-not settings.prepare-procs None)
    (<- dir str (prepare-group-dir settings.prepare-procs settings.state key))
    (<- wrapped tuple (grouped-argv dir argv))
    (:= group dir)
    (:= placed wrapped))
  ;; 準備の層が無い機体の service の root の準備は、service の兄弟の cgroup に置く(EnvSettings の service-procs の註 — 外側の
  ;; job-cgroup-placer は包んだ argv を包み直さない)。
  (when (and (is settings.prepare-procs None) service (is-not settings.service-procs None))
    (<- moved tuple (placed-argv settings.service-procs argv))
    (:= placed moved))
  ;; 子は worker の環境を継ぐ(env = None)。出力は標準出力と標準エラーを同じ log の末尾へ。
  (<- started (StartProcess :argv placed :stdout-path log :stderr-path log))
  (when (isinstance started ProcessNotStarted)
    (raise (OSError started.detail)))
  (PendingEnv :pid started.pid :started-ms now-ms :result result :progress progress :warm warm :memory memory :group group
              :sample (if (is base None) None (LayerSample :base base :peak base))
              :runtime-env runtime-env :compile-jobs compile-jobs :need need :alone alone :service service))


(defk group-oom-kills [group]
  {:pre [(: group (| str None))] :post [(: % int)]}
  "準備の子 group(None = 準備の層が無い)の memory.events の oom_kill を読むため(子を消す前に読む — 子の下の process が memory の上限で
   殺された数。読めなければ 0)。"
  (when (is group None)
    (return 0))
  (<- text (ReadText (+ group "/memory.events")))
  (<- kills int (oom-kills-of (if (isinstance text str) text "")))
  kills)


(defk closed-group [key group]
  {:pre [(: key str) (: group (| str None))] :post [(: % None)]}
  "準備の頭が終わった(か止めた)準備 key の子 group(None = 準備の層が無い — 子を作っていない)の残りの process を止め、空になった子を
   消すため(core/job_cgroup の CLOSE-SCRIPT — 準備の単位より長く生きる process を残さない)。消せなければ log に名指す(次に同じキーを
   起こす時に GROUP-SCRIPT が止めて消し直す)。"
  (when (is group None)
    (return None))
  (<- argv tuple (closing-argv group))
  (<- closed ProcessOutcome (RunProcess :argv argv :timeout CLOSE-TIMEOUT-SECONDS))
  (when (or (not closed.started) (!= closed.exit-code 0))
    (<- (slog GROUP-UNCLOSED-LOG :level "warning" :key key :group group
              :detail (cut (.strip (or closed.stderr closed.start-error "")) -300 None))))
  None)


(defk layer-held [settings]
  {:pre [(: settings EnvSettings)] :post [(: % (| int None))]}
  "組みの置かれる cgroup の層(prepare-layer-dir)の回収できない使い(byte — unreclaimable-of)を読むため。層の無い機体・file が読めない・
   中身が数でない時は None。"
  (when (is settings.prepare-layer-dir None)
    (return None))
  (<- current (ReadText (+ settings.prepare-layer-dir "/memory.current")))
  (<- stat (ReadText (+ settings.prepare-layer-dir "/memory.stat")))
  (when (not (and (isinstance current str) (isinstance stat str) (.isdigit (.strip current))))
    (return None))
  (try
    (<- held int (unreclaimable-of (int (.strip current)) stat))
    (except [ValueError]
      (return None)))
  held)


(defk sample-layers [settings pending]
  {:pre [(: settings EnvSettings) (: pending dict)] :post [(: % dict)]}
  "走っている準備の組みの山を、memory.peak の無い kernel(5.19 より前 — node-d〜g の 5.15)でも測るため: 拍ごとに組みの置かれる層の
   回収できない使いを 1 度読み、準備ごとの最大を上げ、上がった準備の memory の file(layer-sample->json)を置き換えで書く。準備の
   process は完成の印を書く前にこの file を読み、memory.peak を読めない時の組みの山(最大 − 起こす前の読み)にする(env_prepare の
   build-memory-of)。層は同時に走る準備と待ちの子で分け合うので、山はその間の層の上がり(組みが足した量の上の端)。答え = 記録を
   上げた準備中の表。"
  (when (not pending)
    (return pending))
  (<- now (| int None) (layer-held settings))
  (var running pending)
  (for [#(key p) (.items pending)]
    (<- raised (| LayerSample None) (sampled-layer p.sample now))
    (when (and (is-not raised None) (!= raised p.sample))
      (<- body dict (layer-sample->json raised))
      ;; 書けなくても準備は止めない — 準備の process が前の中身か無い file を読み、測れなかった(None)側になる。
      (<- (WriteText p.memory (json.dumps body) :replace True))
      (:= running (| running {key (replace p :sample raised)}))))
  running)


(defk progressed-ms [pending]
  {:pre [(: pending PendingEnv)] :post [(: % int)]}
  "準備の最後の進み(進みの印の時刻 — 準備の process は処理ステージの頭と、bytecode の処理ステージの中の repo の木ごとに印を触る・
   印が無ければ起こした時刻)を返すため。"
  (<- seen (StatPath pending.progress))
  (if (or (isinstance seen FileFailed) (!= seen.kind PathKind.FILE))
      pending.started-ms
      (max pending.started-ms (int (* 1000 seen.modified)))))


(defk read-text-or-empty [path]
  {:pre [(: path str)] :post [(: % str)]}
  "止めた準備の進みの印と log の中身を失敗の文へ載せるため読む(無い・読めない file は空 — 理由を足すだけで、読めなくても止めは進める)。"
  (<- read (ReadText path))
  (if (isinstance read str) read ""))


(defk ready-views [settings busy]
  {:pre [(: settings EnvSettings) (: busy frozenset)] :post [(: % tuple)]}
  "完成マーカーの在る root の観測(READY)を返すため(busy = 準備中か失敗の記録の在るキー — 除く)。"
  (<- names tuple (root-dirs settings))
  (<- roots str (roots-dir settings))
  (var views #())
  (for [name names]
    (val key (+ ENV-KEY-PREFIX name))
    (when (not-in key busy)
      (val root (+ roots "/" name))
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


(defk ready-dependencies [settings views known]
  {:pre [(: settings EnvSettings) (: views (| tuple None)) (: known dict)] :post [(: % dict)]}
  "最後の観測の完成した root の依存のキー(dependency-key — heartbeat の readyDeps で名乗り、coordinator が温める表の行の依存の部分の揃いを
   照らすため)を、root のキー(env-<キー>)→ 依存のキーの dict で返す。known = 前の拍までに読んだ dict(完成した root の印は変わらないので、
   新しく完成した root の印だけを読む)。印を読めない・宣言の形が違う root は入れない(次の拍で読み直す)。"
  (var out {})
  (<- ready frozenset (ready-keys views))
  (for [revision (sorted ready)]
    (if (in revision known)
        (:= out (| out {revision (get known revision)}))
        (do (<- root str (env-root settings revision))
            (<- marker (| dict None) (read-marker root))
            (when (and (is-not marker None) (isinstance (.get marker "env") dict))
              (<- declared (| RuntimeEnv None) (declared-env-or-none (get marker "env")))
              (when (is-not declared None)
                (<- dependency str (dependency-key declared settings.platform))
                (:= out (| out {revision dependency})))))))
  out)


(defk declared-env-or-none [value]
  {:pre [(: value dict)] :post [(: % (| RuntimeEnv None))]}
  "完成の印の宣言の JSON を宣言に戻すため(形の違う印は None — 依存のキーを名乗らないだけで、root の観測は止めない)。"
  (try
    (<- env RuntimeEnv (runtime-env-of-json value))
    env
    (except [RuntimeEnvInvalid] None)))


(defk modified-ms [path]
  {:pre [(: path str)] :post [(: % (| int None))]}
  "path の mtime(epoch ミリ秒)を返すため(無ければ None)。"
  (<- seen (StatPath path))
  (if (or (isinstance seen FileFailed) (= seen.kind PathKind.MISSING)) None (int (* 1000 seen.modified))))


(defrecord MeasuredRoots
  "掃除の数え(ループの外の task)の答え: infos = root ごとの観測(大きさは MeasureTree で数えた byte か、前の数えから使い回した byte)・
   walked = この数えで木を歩いた(MeasureTree を呼んだ)root の数(掃除の頭の行の欄)。"
  (#^ (get tuple #(RootInfo ...)) infos)
  (#^ int walked))


(defk root-infos [settings known]
  {:pre [(: settings EnvSettings) (: known tuple)] :post [(: % MeasuredRoots)]}
  "掃除の候補(roots の直下の dir)を返すため。worker が作った root = キーの形の名で完成マーカーを持つ dir。大きさは、前の数え(known —
   RootInfo の列)と完成の時刻・最後に使った時刻が同じ root は使い回し(env_upkeep の kept-bytes)、それ以外の root だけ木を歩いて数える
   (全 root を掃除のたびに歩くと node の共有の disk の枠を取り合い、worker の本体の thread が block 層で待った)。"
  (<- names tuple (root-dirs settings))
  (<- roots str (roots-dir settings))
  (var infos #())
  (var walked 0)
  (for [name names]
    (val root (+ roots "/" name))
    (val key (+ ENV-KEY-PREFIX name))
    (<- marker (read-marker root))
    (val owned (and (is-not marker None) (bool (ROOT-NAME-PATTERN.fullmatch name))))
    (var made 0)
    (var size 0)
    (var project "")
    (var peak None)
    (when owned
      (<- marker-ms (modified-ms (+ root "/" ENV-MARKER)))
      (:= made (or marker-ms 0)))
    (<- used-ms (modified-ms (+ root "/.last-used")))
    (val used (if (is-not used-ms None) used-ms made))
    (when owned
      (<- kept (| int None) (kept-bytes known key made used))
      (if (is-not kept None)
          (:= size kept)
          (do (<- measured (MeasureTree root))
              (:= size (if (isinstance measured int) measured 0))
              (:= walked (+ walked 1))))
      (<- named str (root-project marker))
      (:= project named)
      ;; 組みの山の memory(印の buildMemoryBytes — 先の組みの前の memory の見積もり・#3748)。欄の無い前の印・null は None。
      (val raw (.get marker "buildMemoryBytes"))
      (:= peak (match raw
                 (bool) None
                 (int) raw
                 _ None)))
    (:= infos (+ infos #((RootInfo :key key :project project :made-ms made :last-used-ms used :bytes size
                                   :owned owned :build-memory-bytes peak)))))
  (MeasuredRoots :infos infos :walked walked))


(defrecord RemovedRoots
  "掃除の消し(ループの外の task)の答え: paths = 消した dir(脇へ退けた root・途中で止まった準備の残り・7 日使われない wheel・使われない
   mirror)。"
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


(defrecord PinRecord
  "他の世代の固定の file 1 つの読み: path = file・state = その世代の状態の dir・keys = 固定のキー・alive = 状態の dir が在るか(無ければ
   その世代は終わり、起動の script が dir を消した — 固定に数えず、掃除の残りの片づけが file を消す)。"
  (#^ str path)
  (#^ str state)
  (#^ (get frozenset str) keys)
  (#^ bool alive))


(defk pins-dir [settings]
  {:pre [(: settings EnvSettings)] :post [(: % str)]}
  "固定を世代の間で知らせ合う file の dir を返すため(root の置き場と同じ分け合う dir の下)。"
  (+ (or settings.shared settings.state) "/" PINS-DIR))


(defk own-pin-file [settings]
  {:pre [(: settings EnvSettings)] :post [(: % str)]}
  "この世代の固定の file の path を返すため(名 = 状態の dir の sha256 の頭 16 字 — 世代ごとに違い、同じ世代の起き直しでは同じ)。"
  (<- place str (pins-dir settings))
  (+ place "/" (cut (.hexdigest (hashlib.sha256 (.encode settings.state))) 0 16) ".json"))


(defk wrote-pins [settings pinned]
  {:pre [(: settings EnvSettings) (: pinned frozenset)] :post [(: % None)] :tags {:context "worker" :role "protocol" :spells "json"}}
  "この世代の固定の集合を固定の file へ置き換えで書くため(root の置き場を分け合う他の世代の掃除が読む — AL-D2g)。書けなければ 1 行
   (PINS-UNWRITTEN-LOG)出して続ける(掃除は止めない)。"
  (<- place str (pins-dir settings))
  (<- (MakeDirectory place))
  (<- path str (own-pin-file settings))
  (<- written (WriteText path (json.dumps {"state" settings.state "keys" (sorted pinned)}) :replace True))
  (when (isinstance written FileFailed)
    (<- (slog PINS-UNWRITTEN-LOG :path path :detail written.detail)))
  None)


(defk pin-of-text [text]
  {:pre [(: text str)] :post [(: % (| tuple None))] :tags {:context "worker" :role "protocol" :reads "json"}}
  "固定の file の中身を #(状態の dir 固定のキーの frozenset) にするため(形の読めない中身は None)。"
  (val data (try (json.loads text) (except [ValueError] None)))
  (match data
    {"state" (str) "keys" (list)} #((get data "state") (frozenset (gfor k (get data "keys") :if (isinstance k str) k)))
    _ None))


(defk pin-records [settings]
  {:pre [(: settings EnvSettings)] :post [(: % tuple)]}
  "他の世代の固定の file を読むため(自分の file・読めない file は除く)。答え = PinRecord の列。"
  (<- place str (pins-dir settings))
  (<- own str (own-pin-file settings))
  (<- entries (ListDirectory place))
  (var records #())
  (when (not (isinstance entries FileFailed))
    (for [entry entries]
      (val path (+ place "/" entry.name))
      (when (and (= entry.kind PathKind.FILE) (.endswith entry.name ".json") (!= path own))
        (<- text (ReadText path))
        (when (isinstance text str)
          (<- read (| tuple None) (pin-of-text text))
          (when (is-not read None)
            (val state (get read 0))
            (val keys (get read 1))
            (<- seen (StatPath state))
            (val alive (and (not (isinstance seen FileFailed)) (!= seen.kind PathKind.MISSING)))
            (:= records (+ records #((PinRecord :path path :state state :keys keys :alive alive)))))))))
  records)


(defk others-pins [settings]
  {:pre [(: settings EnvSettings)] :post [(: % frozenset)]}
  "まだ走っている他の世代(状態の dir が在る)の固定のキーの集合を返すため(AL-D2g — 掃除が他の世代の使う root を退けない)。"
  (<- records tuple (pin-records settings))
  (frozenset (gfor r records :if r.alive k r.keys k)))


(defrecord PruneState
  "uv の cache の prune の記録: pid = 走っている prune の子(None = 走っていない)・started-ms = 最後に起こした時刻(0 = まだ起こしていない)。"
  (setv #^ (| int None) pid None)
  (setv #^ int started-ms 0))


(defk sweep-leftovers [settings now-ms]
  {:pre [(: settings EnvSettings) (: now-ms int)] :post [(: % tuple)]}
  "脇の dir — 消すと選んで退けた root(.<名>.swept.<時刻>)と途中で止まった準備の残り(.<名>.broken.<時刻>)— と、7 日使われない native の
   wheel(build の口の保存先 — 口が使うたびに dir の中の印の file を置き換えて dir の時刻を進める)と、7 日使われない bytecode の
   保存先の entry(使うたびに entry の時刻を進める — doeff-hy の code_store・書きかけで残った一時の file も同じ)と中が全部 7 日動いて
   いない保存先の dir(unused-store-names)と、終わった世代の固定の
   file(AL-D2g)と、root の宣言が指さず 7 日使われない repo の bare mirror(unused-mirrors)を消し、消した path の列を返すため。"
  (<- place str (roots-dir settings))
  (<- roots (ListDirectory place))
  (val aside (if (isinstance roots FileFailed)
                 #()
                 (tuple (gfor entry roots :if (and (.startswith entry.name ".") (or (in ".broken." entry.name) (in SWEPT-MARK entry.name)))
                              (+ place "/" entry.name)))))
  (<- wheels (ListDirectory (wheels-root (or settings.shared settings.state))))
  (var stale #())
  (when (not (isinstance wheels FileFailed))
    (for [entry wheels]
      (when (= entry.kind PathKind.DIRECTORY)
        (val path (+ (wheels-root (or settings.shared settings.state)) "/" entry.name))
        (<- at (modified-ms path))
        (when (and (is-not at None) (> (- now-ms at) (* 1000 WHEEL-UNUSED-SECONDS)))
          (:= stale (+ stale #(path)))))))
  (<- unused tuple (unused-store-entries settings.code-store now-ms))
  ;; 終わった世代(状態の dir が無い)の固定の file(AL-D2g)。
  (<- pins tuple (pin-records settings))
  (val ended (tuple (gfor r pins :if (not r.alive) r.path)))
  (val doomed (+ aside stale unused ended))
  (for [target doomed]
    (<- (RemoveTree target)))
  ;; root の宣言が指さず 7 日使われない repo の bare mirror(mirror ごとに url の錠の中で消す — unused-mirrors)。
  (<- mirrors tuple (unused-mirrors settings pins now-ms))
  (+ doomed mirrors))


(defk mirrors-dir [settings]
  {:pre [(: settings EnvSettings)] :post [(: % str)]}
  "repo の bare mirror の置き場の dir を返すため(分け合う dir — 無ければ state — の mirrors。名は env_translation の EnsureMirror と同じ)。"
  (+ (or settings.shared settings.state) "/mirrors"))


(defk read-repo-keys [settings]
  {:pre [(: settings EnvSettings)] :post [(: % (| dict None))] :tags {:context "worker" :role "protocol" :reads "json"}}
  "mirror の名を EnsureMirror と同じく決めるため、鍵の表(settings.repo-keys の JSON — URL → deploy key)を読む。設定が空なら空の表・
   読めない・形の違う表は None(mirror の名を決められない — 呼び手は mirror を消さない)。"
  (when (not settings.repo-keys)
    (return {}))
  (<- text (ReadText settings.repo-keys))
  (val data (if (isinstance text str) (try (json.loads text) (except [ValueError] None)) None))
  (if (isinstance data dict) data None))


(defk declared-urls [declared]
  {:pre [(: declared dict)] :post [(: % tuple)] :tags {:context "worker" :role "protocol" :reads "json"}}
  "宣言の JSON(完成マーカーと準備の要求の env の欄)から repo の url の列を読むため(形の違う欄は飛ばす)。"
  (match declared
    {"repos" (list)} (tuple (gfor r (get declared "repos") :if (and (isinstance r dict) (isinstance (.get r "url") str)) (get r "url")))
    _ #()))


(defk mirror-names-of [url keys]
  {:pre [(: url str) (: keys dict)] :post [(: % tuple)]}
  "宣言の url 1 つが指しうる mirror の名の頭(digest16)を返すため: 宣言の綴りの名と、鍵の表の綴り(listed-url — EnsureMirror が mirror の
   名に使う綴り)の名。"
  (<- spelled str (digest16 url))
  (<- source (| str None) (listed-url url keys))
  (if (is source None)
      #(spelled)
      (do (<- listed str (digest16 source))
          #(spelled listed))))


(defk recent-requests [state now-ms]
  {:pre [(: state str) (: now-ms int)] :post [(: % tuple)] :tags {:context "worker" :role "protocol" :reads "json"}}
  "世代 1 つ(state = その世代の状態の dir)の準備の要求の file(env-requests/<キー>.json)のうち、MIRROR-UNUSED-SECONDS の内に書いた物の
   宣言(env の欄)の列を返すため(準備中の root の宣言 — 完成マーカーはまだ無い。準備は停滞の期限で止まるので、それより古い要求は
   準備中でない)。読めない file は飛ばす。"
  (val place (+ state "/env-requests"))
  (<- entries (ListDirectory place))
  (var declared #())
  (when (not (isinstance entries FileFailed))
    (for [entry entries]
      (val path (+ place "/" entry.name))
      (when (and (= entry.kind PathKind.FILE) (.endswith entry.name ".json") (not (.endswith entry.name ".result.json")))
        (<- at (| int None) (modified-ms path))
        (when (and (is-not at None) (<= (- now-ms at) (* 1000 MIRROR-UNUSED-SECONDS)))
          (<- text (ReadText path))
          (val data (if (isinstance text str) (try (json.loads text) (except [ValueError] None)) None))
          (when (isinstance data dict)
            (:= declared (+ declared #((.get data "env")))))))))
  declared)


(defk referenced-mirrors [settings pins keys now-ms]
  {:pre [(: settings EnvSettings) (: pins tuple) (: keys dict) (: now-ms int)] :post [(: % frozenset)]}
  "root の宣言が指す mirror の名の頭(digest16)の集合を返すため: 置き場の root(完成マーカーの宣言)と、この世代とまだ走っている他の
   世代(pins — PinRecord の列)の新しい準備の要求の宣言(recent-requests)の repo の url から。"
  (<- names tuple (root-dirs settings))
  (<- roots str (roots-dir settings))
  (var declared #())
  (for [name names]
    (<- marker (read-marker (+ roots "/" name)))
    (when (isinstance marker dict)
      (:= declared (+ declared #((.get marker "env"))))))
  (for [state (+ #(settings.state) (tuple (gfor r pins :if r.alive r.state)))]
    (<- requested tuple (recent-requests state now-ms))
    (:= declared (+ declared requested)))
  (var referenced (frozenset))
  (for [env (gfor d declared :if (isinstance d dict) d)]
    (<- urls tuple (declared-urls env))
    (for [url urls]
      (<- spelled tuple (mirror-names-of url keys))
      (:= referenced (| referenced (frozenset spelled)))))
  referenced)


(defk unused-mirrors [settings pins now-ms]
  {:pre [(: settings EnvSettings) (: pins tuple) (: now-ms int)] :post [(: % tuple)]}
  "repo の bare mirror の置き場の持ち主として、root の宣言が指さず MIRROR-UNUSED-SECONDS 使われない mirror を消し、消した path の列を
   返すため(選びは env_upkeep の unused-mirror-names・消しは mirror ごとに url の錠の中で — removed-mirror)。前は mirror を消す持ち主が
   居らず、宣言から外した repo の mirror が worker の volume に期限なく残った。鍵の表を読めない時は名を決められないので何も消さない。"
  (<- keys (| dict None) (read-repo-keys settings))
  (when (is keys None)
    (<- (slog MIRRORS-UNKEYED-LOG :path settings.repo-keys))
    (return #()))
  (<- place str (mirrors-dir settings))
  (<- listed (ListDirectory place))
  (when (isinstance listed FileFailed)
    (return #()))
  (var entries #())
  (for [entry listed]
    (when (= entry.kind PathKind.DIRECTORY)
      (<- at (| int None) (modified-ms (+ place "/" entry.name)))
      (when (is-not at None)
        (:= entries (+ entries #(#(entry.name at)))))))
  (<- referenced frozenset (referenced-mirrors settings pins keys now-ms))
  (<- chosen tuple (unused-mirror-names (tuple entries) referenced now-ms))
  (var removed #())
  (for [name chosen]
    (<- gone bool (removed-mirror settings name now-ms))
    (when gone
      (:= removed (+ removed #((+ place "/" name))))))
  removed)


(defk removed-mirror [settings name now-ms]
  {:pre [(: settings EnvSettings) (: name str) (: now-ms int)] :post [(: % bool)]}
  "選んだ mirror 1 つを、その url の錠(locks/mirror-<digest16> — EnsureMirror と同じ錠)を待たずに取れた時だけ、錠の中で時刻を読み直して
   まだ古ければ消すため。錠を取られている mirror(準備の clone・fetch の最中)は残して次の掃除へ回す — 待つと掃除の task が scheduler の
   thread を塞ぐ。読み直すのは、選んだ後に EnsureMirror が錠の中で使った印を進めた mirror を消さないため。答え = 消したか。"
  (val shared (or settings.shared settings.state))
  (<- place str (mirrors-dir settings))
  (val path (+ place "/" name))
  (<- got (| LockHeld LockBusy FileFailed) (TryLock (posixpath.join shared "locks" (+ "mirror-" (cut name 0 16)))))
  (match got
    (LockHeld) (try
                 (<- at (| int None) (modified-ms path))
                 (<- still tuple (unused-mirror-names (if (is at None) #() #(#(name at))) (frozenset) now-ms))
                 (when (not still)
                   (return False))
                 (<- gone (| None FileFailed) (RemoveTree path))
                 (when (isinstance gone FileFailed)
                   (return False))
                 (<- (slog MIRROR-SWEPT-LOG :level "info" :mirror name :unused-ms (- now-ms at)))
                 (return True)
                 (finally (<- (ReleaseLock got))))
    _ False))


(defk unused-store-entries [store now-ms]
  {:pre [(: store (| str None)) (: now-ms int)] :post [(: % tuple)]}
  "bytecode の保存先(store — None = 使わない設定)のうち、掃除が消す物(unused-store-names)の path の列を求めるため(保存先の dir が
   無ければ空)。保存先は file ごとの時刻まで StatTree の 1 つの呼びで読む — 本番は offloaded-tree-handler が thread で待つので、file が
   10 万を越える保存先を歩く間も調整ループ(tick と heartbeat)は回る(前は WalkTree と file ごとの StatPath を
   scheduler の thread で歩き、node-h で 10〜19 秒止めた)。歩きの答えからの選び(unused-store-names)も Compute で回す — 本番は
   thread-pool-compute-handler が thread で待つ(前は scheduler の thread で回し、50 万の entry の選びが数秒かかって、掃除の終わり際に
   調整ループを止めた — 2026-11-02 の node-h で掃除の終わりのたびに生存の合図の thread が拍の代わりに heartbeat を送った)。選びが落ちたら
   その例外を上げる(掃除の task の失敗として答えへ渡る)。"
  (if (is store None)
      #()
      (do (<- listed (| tuple FileFailed) (StatTree store))
          (match listed
            (FileFailed) #()
            entries (do (<- chosen (| Computed ComputeFailed) (Compute (unused-store-names entries now-ms)))
                        (when (isinstance chosen ComputeFailed)
                          (raise chosen.error))
                        (tuple (gfor name chosen.value (posixpath.join store name))))))))


(defk unused-store-names [entries now-ms]
  {:pre [(: entries tuple) (: now-ms int)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "bytecode の保存先の歩きの答え(StatTree — 保存先の dir からの相対の名・種類・時刻)から、掃除が消す物の相対の名の列(名の順)を求める
   ため: CODE-STORE-UNUSED-SECONDS より長く時刻の進んでいない file と、自分も下の物も全部その長さ動いていない dir(dir ごと消す — その下の
   file は名指さない)。source の code の entry は鍵ごとの dir の下に macro の環境ごとに並ぶ(doeff-hy の code_store)
   ので、file だけを消すと空の鍵の dir が版ごとの source の数だけ残り続ける。使った entry の在る dir と、作ったばかりの空の dir(書き手が
   entry を置く前)は残す。"
  (val limit-ms (* 1000 CODE-STORE-UNUSED-SECONDS))
  ;; 動いた物(file の使った印・dir の中の置き換え)と、その上の dir の全部 — 消さない。
  (val fresh (frozenset (gfor entry entries
                              :if (<= (- now-ms (int (* 1000 entry.modified))) limit-ms)
                              :setv parts (.split entry.name "/")
                              depth (range 1 (+ (len parts) 1))
                              (.join "/" (cut parts 0 depth)))))
  (val idle-dirs (frozenset (gfor entry entries :if (and (= entry.kind PathKind.DIRECTORY) (not-in entry.name fresh)) entry.name)))
  ;; 動いていない dir の下の物は、いちばん上の動いていない dir ごと消す(下を 1 つずつ名指さない)。
  (tuple (sorted (gfor entry entries
                       :if (and (not-in entry.name fresh)
                                (in entry.kind #(PathKind.FILE PathKind.DIRECTORY))
                                (not-in (posixpath.dirname entry.name) idle-dirs))
                       entry.name))))


(defk measuring-roots [settings known done]
  {:pre [(: settings EnvSettings) (: known tuple) (: done Promise)] :post [(: % None)]}
  "掃除の数え(root ごとの MeasureTree — root 1 つが数千の file の木)をループの外の task で行い、答えを done へ渡すため(#3715)。
   known = 前の数えの root ごとの観測(変わっていない root は歩かない — root-infos)。
   失敗も done へ渡す(渡さないと掃除が走り続けていると読まれ、次の掃除が起きない — 失敗はループが答えを読んだ拍で上がる)。"
  (try
    (<- measured MeasuredRoots (root-infos settings known))
    (<- (CompletePromise done measured))
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


(defk start-measuring [settings ready known now-ms]
  {:pre [(: settings EnvSettings) (: ready frozenset) (: known tuple) (: now-ms int)] :post [(: % SweepMeasuring)]}
  "掃除の数えをループの外の task として起こし、走っている数えの記録を返すため(ループは待たない — 答えは後の拍で sweep-answer が読む)。
   ready = この拍の完成した root のキーの集合・known = 前の数えの root ごとの観測(まだ数えていなければ空)。"
  (<- done Promise (CreatePromise))
  (<- (Spawn (measuring-roots settings known done) :daemon True))
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
  (<- roots str (roots-dir settings))
  (<- moved (RenamePath root (.format "{}/.{}{}{}" roots (cut key (len ENV-KEY-PREFIX) None) SWEPT-MARK now-ms)))
  (not (isinstance moved FileFailed)))


(defk start-removing [settings measured busy free started-ms]
  {:pre [(: settings EnvSettings) (: measured MeasuredRoots) (: busy frozenset) (: free int) (: started-ms int)] :post [(: % SweepRemoving)]}
  "数えの答えが届いた時に、その時の固定(busy — 数えの間に固定になった root も入る・root の置き場を分け合う他の世代の固定 — AL-D2g)と、roots の合計の上限・共有の disk の空き free と
   その最低で消す root を選び(#4051)、脇へ退け、消しをループの外の task として起こすため。答え = 走っている消しの記録。"
  (<- total int (roots-bytes measured.infos))
  (val cap settings.roots-cap-bytes)
  (val min-free settings.min-free-bytes)
  ;; root の置き場を分け合う他の世代の固定も消さない(AL-D2g)。
  (<- others frozenset (others-pins settings))
  (val held (| busy others))
  (<- candidates tuple (sweep-candidates measured.infos held))
  (<- chosen tuple (sweep-choice measured.infos held cap free min-free))
  ;; 掃除の選びの 1 行(#3713 — 何も選ばなかった回も出す): 空き・空きの最低・roots の合計・上限・固定の数・候補の数・選んだ数・木を歩いた
  ;; root の数。
  (<- (slog SWEEP-LOG :level "info" :free-bytes free :min-free-bytes min-free :roots-bytes total :cap-bytes cap :pinned (len held)
            :candidates (len candidates) :chosen (len chosen) :walked measured.walked))
  (<- now-ms int (now-epoch-ms))
  (var aside #())
  (for [key chosen]
    (<- (slog (.format "worker: 掃除 — 固定されていない root {} を消す(roots の合計 {} byte・上限 {} byte・空き {} byte・最低 {} byte)"
                       (cut key (len ENV-KEY-PREFIX) None) total cap free min-free)))
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
                                    :env #((EnvEntry :name "UV_CACHE_DIR" :value settings.uv-cache)) :process-group True))
          (PruneState :pid (if (isinstance started ProcessNotStarted) None started.pid) :started-ms now-ms))
      (PruneState :pid pid :started-ms prune.started-ms)))


(defk read-memory [settings]
  {:pre [(: settings EnvSettings)] :post [(: % (| MemoryUse LayerMemoryUse MemoryUnread))]}
  "先の組みの前に memory を読むため: 組みの置かれる cgroup の層(prepare-layer-dir)が読めればその層の読み、読めなければ worker の container の
   cgroup(v2)の memory.current と memory.max(#3748)。読めない file は None として渡す(cgroup v1 の path・file が無い機体は MemoryUnread)。"
  (when (is-not settings.prepare-layer-dir None)
    (<- layer-current (ReadText (+ settings.prepare-layer-dir "/memory.current")))
    (<- layer-limit (ReadText (+ settings.prepare-layer-dir "/memory.max")))
    (<- layer-stat (ReadText (+ settings.prepare-layer-dir "/memory.stat")))
    (<- layer (| LayerMemoryUse MemoryUnread)
        (layer-memory-use-of (if (isinstance layer-current str) layer-current None) (if (isinstance layer-limit str) layer-limit None)
                             (if (isinstance layer-stat str) layer-stat None)))
    (when (isinstance layer LayerMemoryUse)
      (return layer)))
  (<- current (ReadText (+ settings.cgroup-dir "/memory.current")))
  (<- limit (ReadText (+ settings.cgroup-dir "/memory.max")))
  (<- memory (| MemoryUse MemoryUnread) (memory-use-of (if (isinstance current str) current None) (if (isinstance limit str) limit None)))
  memory)


(defrecord WarmAdmission
  "先の組みの入口の判じの答え: refused = 断る時の失敗(始めてよければ None)・unmeasured = memory を測れなかった訳(測れたら None —
   heartbeat の memoryUnmeasured に root のキーを載せる)。"
  (#^ (| EnvFailure None) refused)
  (#^ (| str None) unmeasured))


(defk warm-admission [settings key runtime-env infos tally busy]
  {:pre [(: settings EnvSettings) (: key str) (: runtime-env str) (: infos tuple) (: tally (| RootsTally None)) (: busy frozenset)]
   :post [(: % WarmAdmission)]}
  "先の組み(PrepareEnv :warm)を始める前に、共有の disk の空き・roots の合計・memory を読んで断るかを決めるため(判じは env_upkeep の
   warm-refusal 1 つ — #3748)。見積もりは最後の掃除の数え(infos — root ごとの大きさと印の組みの山)から同じ project の物を読む(ループの中で
   木を歩かない — #3715)。断る時は 1 行(WARM-REFUSED-LOG)、memory を測れない時は理由の 1 行(MEMORY-UNMEASURED-LOG)を出す。
   busy = 固定の集合(掃除で空けられない root)。"
  (<- project str (declared-project (json.loads runtime-env)))
  (<- free int (disk-free settings))
  (<- root-bytes int (root-estimate infos project))
  (<- peak int (build-memory-estimate infos project))
  (<- reclaimable int (reclaimable-bytes infos busy))
  (<- memory (| MemoryUse LayerMemoryUse MemoryUnread) (read-memory settings))
  (val roots (match tally
               None None
               (RootsTally :bytes total) total))
  (val room (WarmRoom :free free :min-free settings.min-free-bytes :root-bytes root-bytes :roots roots :reclaimable reclaimable
                      :cap settings.roots-cap-bytes :memory memory :peak peak))
  (<- refused (| EnvFailure None) (warm-refusal room))
  ;; 行の欄の memory の読み(測れなければ -1)と、測れなかった訳。
  (val memory-current (match memory (MemoryUse :current current) current (LayerMemoryUse :unreclaimable held) held _ -1))
  (val memory-max (match memory (MemoryUse :limit limit) limit (LayerMemoryUse :limit limit) limit _ -1))
  (val unmeasured (match memory (MemoryUnread :reason reason) reason _ None))
  (match refused
    (EnvFailure :kind kind)
      (<- (slog WARM-REFUSED-LOG :level "info" :key (cut key (len ENV-KEY-PREFIX) None) :kind kind.value :free-bytes free
                :min-free-bytes settings.min-free-bytes :root-bytes root-bytes :roots-bytes (if (is roots None) -1 roots)
                :reclaimable-bytes reclaimable :cap-bytes settings.roots-cap-bytes :memory-current memory-current
                :memory-max memory-max :peak-bytes peak))
    None None)
  (when (and (is refused None) (is-not unmeasured None))
    (<- (slog MEMORY-UNMEASURED-LOG :level "info" :key (cut key (len ENV-KEY-PREFIX) None) :reason unmeasured)))
  (WarmAdmission :refused refused :unmeasured unmeasured))


(defk prepare-room [settings waiting pending infos]
  {:pre [(: settings EnvSettings) (: waiting dict) (: pending dict) (: infos tuple)] :post [(: % (| PrepareRoom None))]}
  "準備を層の下に置く worker(prepare-procs の在る — 準備の層を宣言した機体)の、準備の層の空きを読むため(env_rules の launch-order が
   起こす数をこの空きで絞る)。層で絞らない worker は None。空き = 層の memory.max − 層の回収できない使い − 走っている準備の取り置き
   (need)の和: 起こしたばかりの準備はまだ層を使っていないので、取り置きで数える(走っている準備の今の使いと二重に数える側に倒す —
   層を越えて並べるより、待たせる方を選ぶ)。待ちのキーの見込みの山 = 同じ project の前の組みの山の実測(infos — 最後の掃除の数えの
   印の buildMemoryBytes)、無ければ DEFAULT-BUILD-MEMORY-BYTES。どちらも層の上限の 4 分の 3 で切る(env_upkeep の layer-need — 先の組みの
   入口と同じ切り)。層を読めなければ空きは None(走っている準備が無い時だけ 1 本)。"
  (when (or (is settings.prepare-procs None) (is settings.prepare-layer-dir None))
    (return None))
  (<- current (ReadText (+ settings.prepare-layer-dir "/memory.current")))
  (<- limit (ReadText (+ settings.prepare-layer-dir "/memory.max")))
  (<- stat (ReadText (+ settings.prepare-layer-dir "/memory.stat")))
  (<- layer (| LayerMemoryUse MemoryUnread)
      (layer-memory-use-of (if (isinstance current str) current None) (if (isinstance limit str) limit None)
                           (if (isinstance stat str) stat None)))
  (val cap (match layer (LayerMemoryUse :limit limit) limit _ None))
  (<- default-need int (layer-need DEFAULT-BUILD-MEMORY-BYTES cap))
  (var needs {})
  (for [#(key entry) (.items waiting)]
    (<- project str (declared-project (json.loads entry.runtime-env)))
    (<- peak int (build-memory-estimate infos project))
    (<- need int (layer-need peak cap))
    (:= needs (| needs {key need})))
  (val reserved (sum (gfor p (.values pending) p.need)))
  (PrepareRoom :free (match layer (LayerMemoryUse :unreclaimable held :limit limit) (- limit held reserved) _ None)
               :default-need default-need :needs needs
               :alone (frozenset (gfor #(k w) (.items waiting) :if w.alone k))
               :running-alone (any (gfor p (.values pending) p.alone))))


(defk launch-waiting [settings waiting pending infos]
  {:pre [(: settings EnvSettings) (: waiting dict) (: pending dict) (: infos tuple)] :post [(: % tuple)]}
  "待っている準備のうち起こせる物を起こし、#(残りの待ち 準備中) を返すため(起こす順と数は env_rules.launch-order — 準備の層を持つ
   worker は層の空き prepare-room でも絞る・infos = 最後の掃除の数えの root ごとの観測〔見込みの山の元〕)。"
  (<- room (| PrepareRoom None) (prepare-room settings waiting pending infos))
  (<- order tuple (launch-order (tuple (gfor #(k w) (.items waiting) #(k w.warm))) (len pending)
                                (len (lfor p (.values pending) :if p.warm p)) settings.max-parallel room))
  (var left waiting)
  (var running pending)
  (for [key order]
    (val entry (get left key))
    (val need (if (is room None) 0 (.get room.needs key room.default-need)))
    (:= left (dfor #(k v) (.items left) :if (!= k key) k v))
    (<- started PendingEnv (launch-prepare settings key entry.runtime-env entry.compile-jobs entry.warm need entry.alone
                                                   :service entry.service))
    (:= running (| running {key started})))
  #(left running))


(defk observe-envs [settings waiting pending failed refused infos]
  {:pre [(: settings EnvSettings) (: waiting dict) (: pending dict) (: failed dict) (: refused dict) (: infos tuple)] :post [(: % tuple)]}
  "準備の子の終わりと期限を見て記録を進め、待ちを起こし、root の観測を返すため(refused = 先の組みの断りの記録 — 読むだけ・infos = 最後の
   掃除の数えの root ごとの観測 — 準備の層の見込みの山の元)。答え = #(待ち 準備中 失敗 観測の tuple)。
   準備の子(準備の層の下 — group)の oom_kill が増えて終わった準備(完成を書いていない)は、層を分け合った他の準備との取り合いで殺された
   ので、失敗にせず 1 本だけで起こし直す待ち(alone)へ戻す(2026-11-09 の node-f — 2 本が 1 本分の層で互いを殺し、env-incompatible と
   記録して起こし直し続けた)。1 本だけで起こしてなお殺された準備は、層に入らない準備として memory-killed の失敗にする。"
  (<- now-ms int (now-epoch-ms))
  (<- sampled dict (sample-layers settings pending))
  (var running sampled)
  (var failures failed)
  (var queued waiting)
  (for [#(key p) (.items sampled)]
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
                  (<- (closed-group key p.group))
                  (:= running (dfor #(k v) (.items running) :if (!= k key) k v))
                  (val log-path (+ settings.state "/env-requests/" key ".log"))
                  (<- stage (read-text-or-empty p.progress))
                  (<- log-tail (read-text-or-empty log-path))
                  (<- failure (overdue-failure p.warm settings.limits stage log-path log-tail))
                  (<- (slog "worker: 準備を止めた(進みが止まった)" :key key :kind (str failure.kind) :detail failure.detail))
                  (:= failures (| failures {key #(failure now-ms)})))))
        (do (:= running (dfor #(k v) (.items running) :if (!= k key) k v))
            ;; 子を消す前に、子の下の process が memory の上限で殺された数を読む(完成を書いた準備は見ない)。
            (<- kills int (group-oom-kills (if (isinstance answer ReadyAnswer) None p.group)))
            ;; 頭が終わった準備の木の残り(頭の外で生きる子孫)を止める — 成功・失敗とも。
            (<- (closed-group key p.group))
            ;; 答えを書かずに終わった準備は、prepare-outcome が log の在処を添えた失敗にする。
            ;; 立てた覚えの無い子(ProcessNotChild)は終わりの番号が分からない — -1 で名指す。
            (val code (if (isinstance polled ProcessExited) polled.exit-code -1))
            (val exit-log (+ settings.state "/env-requests/" key ".log"))
            (<- outcome (| EnvFailure None) (prepare-outcome answer code exit-log))
            (cond
              (and (> kills 0) (not p.alone))
                (do (<- (slog MEMORY-KILLED-LOG :level "warning" :key key :oom-kills kills))
                    (:= queued (| queued {key (WaitingEnv :runtime-env p.runtime-env :compile-jobs p.compile-jobs :warm p.warm
                                                          :alone True :service p.service)})))
              (> kills 0)
                (do (<- failure EnvFailure (memory-killed-failure kills code exit-log))
                    (<- (slog "worker: 準備が失敗した" :key key :kind (str failure.kind) :detail failure.detail))
                    (:= failures (| failures {key #(failure now-ms)})))
              (is-not outcome None)
                (do (<- (slog "worker: 準備が失敗した" :key key :kind (str outcome.kind) :detail outcome.detail))
                    (:= failures (| failures {key #(outcome now-ms)})))))))
  (<- launched tuple (launch-waiting settings queued running infos))
  (val left (get launched 0))
  (val now-running (get launched 1))
  (<- ready tuple (ready-views settings (frozenset (+ (list now-running) (list failures) (list refused)))))
  (val views (tuple (+ (lfor key (+ (list now-running) (list left)) (CodeView key CodeState.PREPARING))
                       (lfor #(key #(failure failed-ms)) (.items failures)
                             (CodeView key CodeState.FAILED :detail failure.detail :failed-ms failed-ms :failure failure))
                       (lfor #(key #(refusal refused-ms)) (.items refused)
                             (CodeView key CodeState.REFUSED :detail refusal.detail :failed-ms refused-ms :failure refusal))
                       (list ready))))
  #(left now-running failures views))


(defhandler env-host [#^ EnvSettings settings]
  ;; 引数に残す理由: root の置き場と準備の道具は worker の process ごとの設定(main が引数から作る)。
  ;; 記録: 待ち(キー → WaitingEnv — 頼まれた順)・準備中(キー → PendingEnv)・失敗(キー → #(EnvFailure 時刻))・
  ;; 先の組みの断り(キー → #(EnvFailure 時刻) — 組みを始めていないので失敗と別に持つ)・固定の集合 pinned-roots(最後に受けた SweepEnvs の固定 — 判断の側の statuses の held と別の物)・最後の掃除の終わりの時刻・
  ;; 走っている掃除(数えか消し — 同時に 1 つ)・最後の数えの結び tally(RootsTally — まだ数えていなければ None・#3732)・prune の記録・
  ;; 最後の観測(heartbeat の名乗りが読む)・最後に名乗った disk の条件(None = まだ名乗っていない — env-capacity の戻りの幅が読む)。
  (session var waiting {})
  (session var pending {})
  (session var failed {})
  (session var refused {})
  (session var pinned-roots (frozenset))
  (session var swept-ms 0)
  (session var sweeping None)
  (session var tally None)
  (session var prune (PruneState))
  (session var views None)
  (session var disk-named None)
  ;; 最後の掃除の数えの root ごとの観測(RootInfo の列 — 先の組みの見積もりと、次の数えの大きさの使い回しが読む・#3748)
  ;; と、先の組みを memory を測らずに始めた root の
  ;; キーの集合(heartbeat の memoryUnmeasured — 測って始め直した root は外す)。
  (session var measured-infos #())
  (session var unmeasured (frozenset))
  ;; 完成した root の依存のキー(root のキー → 依存のキー — heartbeat の readyDeps・印を読み直さないため最後の名乗りの値を持つ)。
  (session var dependencies {})
  (PrepareEnv [key runtime-env compile-jobs warm service]
    ;; job の頼み(warm = False)は、同じ root の先読みが走っていれば job の準備へ上げ(同時の枠の数え方が job の物になる — 期限は
    ;; 先読みと同じ停滞の長さ・走っている焼きの並べる数は変えない)、待っていれば job の頼み(焼きの並べる数も)に置き換える。先の組み(warm)は
    ;; 始める前に空き・roots の上限・memory で断るかを判じ(warm-admission — #3748)、断れば断りの記録に種類つきで置く(heartbeat の env-failed
    ;; → 温める表の WarmFailure)。job の準備は断らない。断りの記録は完成の印の有無によらず頼みのたびに消す(他の世代が組んだ root を断りで隠さない)。
    (val wanted (WaitingEnv :runtime-env runtime-env :compile-jobs compile-jobs :warm warm :service service))
    (cond
      (in key pending)
        (when (and (. (get pending key) warm) (not warm))
          (:= pending (| pending {key (replace (get pending key) :warm False)})))
      (in key waiting)
        (when (not warm)
          (:= waiting (| waiting {key (replace wanted :alone (. (get waiting key) alone))})))
      True
        (do (:= refused (dfor #(k v) (.items refused) :if (!= k key) k v))
            (<- root str (env-root settings key))
            (<- marker (read-marker root))
            (when (is marker None)
              (:= failed (dfor #(k v) (.items failed) :if (!= k key) k v))
              (var admission (WarmAdmission :refused None :unmeasured None))
              (when warm
                (<- judged WarmAdmission
                    (warm-admission settings key runtime-env measured-infos tally (| pinned-roots (frozenset pending) (frozenset waiting))))
                (:= admission judged))
              (:= unmeasured (if (is admission.unmeasured None) (- unmeasured #{key}) (| unmeasured #{key})))
              (match admission.refused
                (EnvFailure)
                  (do (<- now-ms int (now-epoch-ms))
                      (:= refused (| refused {key #(admission.refused now-ms)})))
                None
                  (do (<- launched tuple (launch-waiting settings (| waiting {key wanted}) pending measured-infos))
                      (:= waiting (get launched 0))
                      (:= pending (get launched 1)))))))
    (resume None))
  (ObserveEnvs []
    (<- observed tuple (observe-envs settings waiting pending failed refused measured-infos))
    (:= waiting (get observed 0))
    (:= pending (get observed 1))
    (:= failed (get observed 2))
    (:= views (get observed 3))
    (resume views))
  (ObserveEnvDisk []
    ;; 掃除の係が拍を求めているか(sweep-wanted)は最後の観測の完成した root の集合と最後の数えの結びで判じる(#3732)。
    (<- free int (disk-free settings))
    (<- ready frozenset (ready-keys views))
    (<- wanted bool (sweep-wanted (is-not sweeping None) tally ready settings.roots-cap-bytes (< free settings.min-free-bytes)))
    (resume (EnvDisk :free free :sweep-wanted wanted :pinned pinned-roots)))
  (EnvReport []
    ;; heartbeat で名乗る root の姿(coordinator の置き先と温める表の読みが使う): 最後の観測(まだ無ければ今観測する)と disk の条件
    ;; (形は env-report — sim の宿と同じ関数)。
    (when (is views None)
      (<- observed tuple (observe-envs settings waiting pending failed refused measured-infos))
      (:= waiting (get observed 0))
      (:= pending (get observed 1))
      (:= failed (get observed 2))
      (:= views (get observed 3)))
    (<- free int (disk-free settings))
    (<- capacity str (env-capacity free settings.min-free-bytes disk-named))
    (:= disk-named capacity)
    (<- read dict (ready-dependencies settings views dependencies))
    (:= dependencies read)
    (resume (env-report (or views #()) capacity unmeasured (frozenset (.values dependencies)))))
  (SweepEnvs [pinned]
    ;; 固定の集合を持ち替え、掃除を 1 歩進める(#3715 — 数えと消しはループの外の task・ループは待たない・走っている掃除は同時に 1 つ):
    ;;   走っていない → 始める時(sweep-due — まだ数えていない・完成した root の集合が変わった・空きの状態が変わったか割ったまま固定が
    ;;                  変わった・上限を越えたまま)なら数えを起こす
    ;;   数えている   → 答えが届いていれば、数えの結び(tally)を持ち替え、この時の固定と上限と空きで選び、選んだ root を脇へ退けて消しを起こす
    ;;   消している   → 終わっていれば終わりの 1 行を出し、共有の disk の空きが最低を割っていれば uv の cache の prune を起こす
    ;; 初期値の空との比べで「変わった」と判じる最初の SweepEnvs は、worker が最初の宣言を読んだ拍の物(読む前は判断の側
    ;; policy.sweep-actions が撃たない — #3731)。ここで二重に止めない。
    (val changed (!= pinned pinned-roots))
    (:= pinned-roots pinned)
    ;; 固定が変われば、root の置き場を分け合う他の世代の掃除が読む file へ書く(AL-D2g)。
    (when changed
      (<- (wrote-pins settings pinned)))
    (<- now-ms int (now-epoch-ms))
    (match sweeping
      None
        (do (<- ready frozenset (ready-keys views))
            (<- free int (disk-free settings))
            (<- due bool (sweep-due tally ready settings.roots-cap-bytes (< free settings.min-free-bytes) changed now-ms swept-ms))
            (when due
              (<- measuring SweepMeasuring (start-measuring settings ready measured-infos now-ms))
              (:= sweeping measuring)))
      (SweepMeasuring)
        (do (<- measured (| MeasuredRoots None) (sweep-answer sweeping))
            (when (is-not measured None)
              (<- total int (roots-bytes measured.infos))
              ;; 空きは答えを受けた時に 1 度読み、結び(空きの状態 — 次の起こしの判断が比べる)と選びに同じ値を使う(#4051)。
              (<- free int (disk-free settings))
              (:= tally (RootsTally :ready sweeping.ready :bytes total :below-min-free (< free settings.min-free-bytes)))
              (:= measured-infos measured.infos)
              ;; 固定には走っている準備(pending と waiting)も足す(判断の側の観測より新しいので)。数えの間に固定になった root も入る。
              (<- removing SweepRemoving (start-removing settings measured (| pinned-roots (frozenset pending) (frozenset waiting)) free
                                                        sweeping.started-ms))
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
    (resume None))
  (WorkerWakes [began]
    ;; 周の間の待ちを起こす物(#3871 の単位 4): 準備の子と走っている prune の子の終わり(exits)・走っている掃除の task の終わりの Future
    ;; (bells)・準備の停滞の止めの期限と roots の数え直しの間隔(due)を、外側の答えに足す。期限は tick の頭の時刻 began を基準に組み立てる
    ;; (準備の停滞と掃除の判断は tick の中で読んだ時刻で判定したので、began より後の期限はまだ判定していない — #4332)。
    (<- outer WakeSet effect)
    (var dues #())
    (for [p (.values pending)]
      (<- progressed int (progressed-ms p))
      (<- stop-due (| DueAt DueNever) (prepare-stop-due began progressed settings.limits))
      (:= dues (+ dues #(stop-due))))
    (<- interval-due (| DueAt DueNever) (sweep-interval-due began tally settings.roots-cap-bytes swept-ms))
    ;; 準備の層の空きを待つ準備が在れば、ROOM-RECHECK-MS の後に層を読み直す(空きは worker の起こさない物の終わりでも戻る)。
    (<- room-due (| DueAt DueNever)
        (due-after began (if (and waiting (is-not settings.prepare-procs None)) #((+ began ROOM-RECHECK-MS)) #())))
    (<- due (| DueAt DueNow DueNever) (earliest-due (+ dues #(interval-due room-due))))
    (val bells (if (is sweeping None) #() #(sweeping.done.future)))
    (val exits (+ (tuple (gfor p (.values pending) (AwaitProcessExit p.pid)))
                  (if (is prune.pid None) #() #((AwaitProcessExit prune.pid)))))
    (<- merged WakeSet (wakes-with outer due bells exits))
    (resume merged)))
