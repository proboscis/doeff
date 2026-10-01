;;; worker の coordinator への口(handlers.hy の前の口を置き換えた・#2427)— 拍ごとの ReadDesired に heartbeat の返事の宣言で答え、
;;; PublishStatus を次の heartbeat の状態の報告にする。heartbeat・Program の取り寄せ(/programs/<sha>)・名指しの待ち(/watch)は宛先の部品
;;; (shared/protocol/coordinator_route)の上の汎用の HttpRequest、task の印と結果の file は汎用の file の効果で出す。本物の I/O は入口が積む
;;; http-production-handler と os-file-handler。
;;;
;;; 拍から拍へ持ち越す値(最後の宣言・途絶の数え・状態の報告・待ちの版)は入れ物 LinkState に置く(宛先の RouteCell と同じ形 — 組み立てが
;;; 作って handler の引数に渡し、検は同じ入れ物を外から見る)。振る舞いは前の口と同じ:
;;;   * heartbeat を送る拍は beat_policy.heartbeat-due(待ちの口を使えている間は、宣言が変わった・状態の報告が変わった・送る間隔を過ぎた時だけ)。
;;;   * 返事の job と task を JobSpec に読み(worker/protocol/declared)、task の印 <id>.program を残し、返事から外れた task の結果の file を消し、
;;;     cache に無い詰めた Program を取り寄せる(中身の sha256 がキーと合わない物は書かない)。
;;;   * 届かなければ desired-when-unreachable(途絶が fence を越えたら lease を持たない job と task を止める)。
;;;   * 名指しの待ち(#1933)は背景の task(Spawn の daemon)で送り続け、「変わった」と答えたら次の拍で heartbeat を送らせる。
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import json)
(import os)
(import pathlib [Path])
(import doeff_core_effects [slog])
(import doeff_core_effects.file_effects [FileFailed PathKind ReadText WriteText MakeDirectory ListDirectory RemoveTree file-done])
(import doeff_core_effects.http_effects [HttpResponse HttpFailed])
(import doeff_core_effects.scheduler [Spawn])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.remote_model [program-sha])
(import doeff_cluster.shared.intent.runtime_env_model [current-platform])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions RoutedReply routed-request answer-json])
(import doeff_cluster.worker.core.beat_policy [WatchKind WatchReading beat-interval-ms heartbeat-due watch-reading reply-revision
                                               WATCH-RETRY-SECONDS WAKE-HOLD-SECONDS])
(import doeff_cluster.shared.intent.protocol [WATCH-MAX-SECONDS])
(import doeff_cluster.worker.core.heartbeat_rules [warm-env-of-row finished-task-id desired-when-unreachable])
(import doeff_cluster.worker.core.launch [program-file program-file-text])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs DesiredUnreadable ReadDesired PublishStatus])
(import doeff_cluster.worker.protocol.declared [declared-job-spec task-spec])
(import doeff_cluster.worker.protocol.heartbeat [env-heartbeat-part heartbeat-body status-report])


(defclass WatchCell []
  "名指しの待ちの背景の task と拍が分ける値(#1933 — beat_policy)。after = 次の待ちの版(前の heartbeat の返事の版)・confirmed = 待ちが
   1 度答えた(口を確かめた)・unsupported = 待つ口が無い(404)・woken = 待ちが「変わった」と答えた印・beats = 届いた heartbeat の数
   (待ちが「変わった」の後、heartbeat が版を進めたかを見る)・running = 背景の task が走っている・closing = 止めの合図・failure = 背景の
   task が止まった理由・told = 最後に出した 1 行の鍵。scheduler の task は 1 つの thread で交互に走るので錠は要らない。"
  (defn #^ None __init__ [self]
    (setv self.after None self.confirmed False self.unsupported False self.woken False self.beats 0 self.running False
          self.closing False self.failure "" self.told None)))


(defclass LinkState []
  "coordinator への口が拍から拍へ持ち越す値の入れ物(頭の註)。組み立て(main・検)が作り、handler coordinator-link と背景の待ちが書き換える。
   name = worker の名・provides / exclusive = 提供する能力・専用の能力の名・capacity = 同時の job の上限・fence-ms = 途絶で止める長さ
   (返事の timing が上書きする)・task-dir = task の印と結果の file の置き場・versions = 名乗る版・tools = 名乗る道具(名 → 版)・
   handles-envs = 実行環境の job を扱うか(真なら root の名乗りを heartbeat に載せ、温める表を受ける)・node = k8s の node の名・
   watch = 名指しの待ちを使うか(本番の入口が真にする)・boot = この process の世代・boot-at = 起動時刻(epoch ms)・started-ms = 最後の連絡と
   みなす初めの時刻(一度も届かない worker は fence の後に何も動かさない)。"
  (defn #^ None __init__ [self #^ str name #^ tuple provides #^ int capacity #^ int fence-ms #^ str task-dir #^ str boot #^ int boot-at
                          #^ int started-ms * #^ (| dict None) [versions None] #^ (| dict None) [tools None] #^ bool [handles-envs False]
                          #^ tuple [exclusive #()] #^ str [node ""] #^ bool [watch False]]
    (setv self.name name self.provides provides self.exclusive exclusive self.node node self.capacity capacity self.tools (or tools {})
          self.handles-envs handles-envs self.env-report None
          self.fence-ms fence-ms self.statuses []
          self.task-dir task-dir self.versions (or versions {})
          ;; 詰めた Program の cache(/programs/<sha> から取る — 子 process の言い換えと同じ state dir の programs・改訂 1 の F)。
          self.program-dir (os.path.join (os.path.dirname (os.path.abspath task-dir)) "programs")
          self.last-ok-ms started-ms
          ;; 最後に受け取った job と task の宣言(途絶の間も動かす書き手と切り離した task を選ぶ — worker_policy.kept-when-cut-off)。
          self.last-jobs #() self.last-tasks #() self.last-warm #() self.warm-keys {}
          self.boot boot self.boot-at boot-at
          ;; 切り離した task の id → 置かれた時の返事の行。状態の報告に写して添え、状態を失った coordinator が走っている task を引き取れる
          ;; ようにする(cluster_policy.adopt-running-detached・2026-09-27)。
          self.task-echo {}
          ;; 最後に出した heartbeat の結果(None = まだ出していない・"" = 名乗れた・それ以外 = 名乗れない理由)。
          self.told None
          ;; heartbeat の切り離し(#1933): last-desired = 前の heartbeat の返事の desired(届かなかったら None — 次の拍で必ず送る)・
          ;; sent-statuses = 前に届けた状態の報告・beat-interval-ms = 送る間隔。
          self.watch-enabled watch self.watch (WatchCell)
          self.last-desired None self.sent-statuses None self.beat-interval-ms (beat-interval-ms None {}))))



(deff watch-params [#^ int after #^ str worker #^ str boot #^ bool confirmed]  ; defk にできない: この口と sim の宿が同じ問いを作る(worker/core/beat_policy から移した — 役 protocol・agora-redesign #2541)
  {:pre [(: after int) (: worker str) (: boot str) (: confirmed bool)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "名指しの待ちの問い(GET /watch の query)を作るため。まだ口を確かめていない最初の待ちは 0 秒(すぐ答える — 待つ口の有無を確かめ、
   確かめるまで毎拍の heartbeat を続ける)、その後は上限まで待つ。"
  {"after" (str after) "timeoutSeconds" (str (if confirmed WATCH-MAX-SECONDS 0.0)) "worker" worker "boot" boot})

(defk told-once [state outcome line]
  {:pre [(: state LinkState) (: outcome str) (: line str)] :post [(: % None)]}
  "heartbeat の結果の変わり目(初めて名乗れた・名乗れない理由が変わった・戻った)だけを 1 行出すため。以前は断りも途絶も黙っていて、13 回目の
   本番の切り替えでは coordinator が heartbeat を 400 で断り続けたのに、worker の log は 32 分何も出さなかった(2026-09-29・#1005)。"
  (when (!= outcome state.told)
    (setv state.told outcome)
    (<- (slog line)))
  None)


(defk watch-told-once [watch key line]
  {:pre [(: watch WatchCell) (: key str) (: line str)] :post [(: % None)]}
  "待ちの結果の変わり目だけを 1 行出すため(同じ失敗を拍ごとに繰り返さない)。"
  (when (!= key watch.told)
    (setv watch.told key)
    (<- (slog line)))
  None)


(defk accepted-tasks [state tasks]
  {:pre [(: state LinkState) (: tasks list)] :post [(: % tuple)]}
  "heartbeat の返事の task の行 → 1 度だけ走らせる job。task ごとに、その Program の置き場のキーを印の file <id>.program に残し
   (返事から外れた task の Program の cache を後で消すため — fetched-programs)、返事から外れた task の結果の file を消す(この worker が
   書いた物だけ)。切り離した task は返事の行をそのまま写しとして持つ(状態の報告に添える)。"
  (<- (file-done (MakeDirectory state.task-dir)))
  (val ids (sfor t tasks (get t "id")))
  (for [task tasks]
    (val mark (os.path.join state.task-dir (+ (get task "id") ".program")))
    ;; 同じ id の印が別の sha を指していれば書き直す(印は cache の掃除にだけ使う — 子へ渡す file は返事の行の sha で決まる)。
    (<- marked (ReadText mark))
    (when (!= (if (isinstance marked str) marked None) (get task "program"))
      (<- (file-done (WriteText mark (get task "program") :replace True)))))
  ;; .blob = 詰めた Program を行に持っていた版の worker が書いた file(置き場 /programs の前)— 残っていれば一緒に消す。
  (<- entries (ListDirectory state.task-dir))
  (when (not (isinstance entries FileFailed))
    (for [entry entries]
      (val parts (os.path.splitext entry.name))
      (when (and (in (get parts 1) #(".blob" ".result")) (not-in (get parts 0) ids))
        (<- (RemoveTree (os.path.join state.task-dir entry.name))))))
  (setv state.task-echo (dfor task tasks :if (.get task "detached") (get task "id") (dict task)))
  (tuple (gfor task tasks (task-spec task (Path state.task-dir)))))


(defk fetched-program [cell options program-dir sha]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: program-dir str) (: sha str)] :post [(: % None)]}
  "詰めた Program 1 つを coordinator の /programs/<sha> から取り、子の入口が読む形の cache の file({\"blob\" \"versions\"})に置くため。中身の
   sha256 がキーと合わない物・取れない物は書かずに 1 行出す(次の拍で試し直す — 子は file が無いので起動の時に理由つきで落ちる)。"
  (<- reply RoutedReply (routed-request cell.route "GET" (+ "/programs/" sha) options None None))
  (setv cell.route reply.route)
  (var problem None)
  (var body None)
  (try
    (<- read (answer-json reply.answer))
    (:= body read)
    (except [error Exception]
      (:= problem (repr error))))
  (when (and (is problem None) (!= (program-sha (get body "blob")) sha))
    (:= problem (+ "中身の sha256 がキーと合わない: " sha)))
  (if (is-not problem None)
      (<- (slog (.format "worker: Program {} を取れない: {}" sha problem)))
      (<- (file-done (WriteText (str (program-file (Path program-dir) sha))
                                (program-file-text (get body "blob") (.get body "versions" {})) :replace True))))
  None)


(defk fetched-programs [state cell options specs]
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions) (: specs tuple)] :post [(: % None)]}
  "宣言の job と task のうち、cache に無い詰めた Program を取り寄せ(改訂 1 の F — service と task で同じ仕組み)、返事から外れた task の
   Program の cache を、今の job と task のどれも参照していなければ消すため(task ごとの印 <id>.program から引く — service の job の
   Program は消さない)。"
  (<- (file-done (MakeDirectory state.program-dir)))
  (val wanted (sfor s specs :if s.program s.program))
  (for [sha (sorted wanted)]
    (val path (str (program-file (Path state.program-dir) sha)))
    (<- found (ReadText path))
    (when (isinstance found FileFailed)
      (<- (fetched-program cell options state.program-dir sha))))
  (val current (sfor s specs :if (.startswith s.name "task/") (cut s.name 5 None)))
  (<- entries (ListDirectory state.task-dir))
  (when (not (isinstance entries FileFailed))
    (for [entry entries]
      (val parts (os.path.splitext entry.name))
      (when (and (= (get parts 1) ".program") (not-in (get parts 0) current))
        (val mark (os.path.join state.task-dir entry.name))
        (<- text (ReadText mark))
        (val marked-sha (if (isinstance text str) (.strip text) ""))
        (when (and marked-sha (not-in marked-sha wanted))
          (<- (RemoveTree (str (program-file (Path state.program-dir) marked-sha)))))
        (<- (RemoveTree mark)))))
  None)


(defk accepted-warm [state rows]
  {:pre [(: state LinkState) (: rows list)] :post [(: % tuple)]}
  "heartbeat の返事の温める表の行 → この worker の root のキーの WarmEnv(キーは行ごとに 1 度だけ計算する — 計算は warm-env-of-row、
   sim の宿と同じ関数)。"
  (if (not state.handles-envs)
      #()
      (do (val out [])
          (for [row rows]
            (val text (json.dumps (get row "runtimeEnv") :sort-keys True :ensure-ascii False))
            (when (not-in text state.warm-keys)
              (setv (get state.warm-keys text) (warm-env-of-row row (current-platform))))
            (.append out (get state.warm-keys text)))
          (tuple out))))


(defk ready-file-written [draining]
  {:pre [(: draining bool)] :post [(: % None)]}
  "readinessProbe が sh で読む file(DOEFF_WORKER_READY_FILE — 無ければ書かない)へ、heartbeat が届いた拍ごとに「ready」か「draining」を
   書くため(mtime = 最後に届いた時刻 — probe は中身が ready で新しい時だけ Ready)。"
  (val path (os.environ.get "DOEFF_WORKER_READY_FILE"))
  (when path
    (<- (file-done (WriteText path (if draining "draining\n" "ready\n") :replace True))))
  None)


(defk watching? [state]
  {:pre [(: state LinkState)] :post [(: % bool)]}
  "待ちの口を使えているか(heartbeat を拍から切り離してよいか)を返すため: 待ちを使う口で、背景の task が走っていて、口を確かめ(最初の
   待ちが答えた)、404 でない。背景の task が思わぬ例外で止まっていれば、1 行出して毎拍の heartbeat に戻る。"
  (val watch state.watch)
  (when (and state.watch-enabled watch.failure (not watch.running) (not watch.unsupported) (not watch.closing))
    (<- (watch-told-once watch (+ "task-dead:" watch.failure)
                         (+ "worker: 待ちの task が止まっている — 拍ごとの heartbeat に戻ります: " watch.failure))))
  (bool (and state.watch-enabled watch.running watch.confirmed (not watch.unsupported))))


(defk watch-once [state cell options after]
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions) (: after int)] :post [(: % WatchReading)]}
  "名指しの待ちを 1 回送り、答えを読むため(届かない・読めない返事も WatchReading の FAILED に畳む — 背景の task を例外で落とさない)。"
  (<- reply RoutedReply (routed-request cell.route "GET" "/watch" options (watch-params after state.name state.boot state.watch.confirmed)
                                        None))
  (setv cell.route reply.route)
  (val answer reply.answer)
  (if (isinstance answer HttpResponse)
      (watch-reading answer.status (try (json.loads answer.text) (except [ValueError] answer.text)))
      (WatchReading :kind WatchKind.FAILED :detail (if (is answer None) "宛先が無い" (.format "{}: {}" answer.url answer.detail)))))


(defk woken-held [watch beats]
  {:pre [(: watch WatchCell) (: beats int)] :post [(: % None)]}
  "待ちが「変わった」と答えた後、heartbeat が届く(beats が進む)か WAKE-HOLD-SECONDS を過ぎるまで待つため(同じ版で待ち直して空回りしない)。"
  (var waited 0.0)
  (while (and (= watch.beats beats) (< waited WAKE-HOLD-SECONDS) (not watch.closing))
    (<- (Delay 0.05))
    (:= waited (+ waited 0.05)))
  None)


(defk watch-loop [state cell options]
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions)] :post [(: % None)]}
  "背景の task の本体: 前の heartbeat の版の後の変化を待ち、「変わった」なら拍に heartbeat を送らせ(woken)、その heartbeat が版を進めるまで
   待ってから次を待つ。404 なら口が無いと記して抜ける(拍ごとの heartbeat に戻る)。届かなければ間を置いて送り直す。思わぬ例外は理由を
   記して抜ける(拍が watching? で気づいて戻る — 黙って待ちを失わない)。"
  (val watch state.watch)
  (try
    (while (not watch.closing)
      (if (is watch.after None)
          (<- (Delay WATCH-RETRY-SECONDS))
          (do (val after watch.after)
              (<- reading WatchReading (watch-once state cell options after))
              (cond
                (= reading.kind WatchKind.UNSUPPORTED)
                  (do (setv watch.unsupported True)
                      (<- (watch-told-once watch "unsupported" "worker: coordinator に待ちの口が無い — 拍ごとの heartbeat を続けます"))
                      (setv watch.closing True))
                (= reading.kind WatchKind.FAILED)
                  (do (<- (watch-told-once watch (+ "failed:" reading.detail) (+ "worker: 待ちを送れない(送り直します): " reading.detail)))
                      (<- (Delay WATCH-RETRY-SECONDS)))
                (= reading.kind WatchKind.CHANGED)
                  (do (setv watch.confirmed True watch.woken True)
                      (<- (woken-held watch watch.beats)))
                True
                  (do (setv watch.confirmed True)
                      ;; その間に heartbeat が版を書き換えていれば、そちらを残す。
                      (when (= watch.after after)
                        (setv watch.after reading.revision)))))))
    (except [error Exception]
      (setv watch.failure (repr error))
      (<- (slog (+ "worker: 待ちの task が止まりました: " (repr error))))))
  (setv watch.running False)
  None)


(defk beat [state cell options]
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions)] :post [(: % (| DesiredJobs DesiredUnreadable))]}
  "heartbeat を 1 回送り、返事の job・task・温める表を desired にするため。届かなければ desired-when-unreachable(fence の判断)。"
  (val sending state.statuses)
  ;; 送る前に起こしの印を下ろす(送った後に来た変化の印を消さない)。
  (setv state.watch.woken False)
  (val endpoint (get cell.route.urls cell.route.active))
  (val body (| (heartbeat-body :name state.name :provides state.provides :exclusive state.exclusive :node state.node
                               :capacity state.capacity :versions state.versions :statuses sending
                               :endpoint endpoint :boot state.boot :boot-at state.boot-at :tools state.tools)
               (if (or (not state.handles-envs) (is state.env-report None))
                   {}
                   (env-heartbeat-part state.env-report (current-platform)))))
  (<- reply RoutedReply (routed-request cell.route "POST" "/heartbeat" options None body))
  (setv cell.route reply.route)
  (<- now-ms int (now-epoch-ms))
  (var desired None)
  (try
    (<- answered (answer-json reply.answer))
    (setv state.last-ok-ms now-ms)
    (<- (ready-file-written (bool (.get answered "draining" False))))
    ;; 自己停止の時間は coordinator の ClusterTiming が持つ(移し替えの時間と組で決まる)。受け取った値に合わせる。
    (val timing (.get answered "timing"))
    (when (and timing (in "fence_ms" timing))
      (setv state.fence-ms (int (get timing "fence_ms"))))
    (setv state.last-jobs (tuple (gfor job (get answered "jobs") (declared-job-spec job))))
    (<- tasks tuple (accepted-tasks state (.get answered "tasks" [])))
    (setv state.last-tasks tasks)
    (<- (fetched-programs state cell options (+ state.last-jobs state.last-tasks)))
    (<- warm tuple (accepted-warm state (.get answered "warm" [])))
    (setv state.last-warm warm)
    ;; 返事を読み終えてから出す(返事の読みが毎回落ちる時に、名乗れた・名乗れないの 2 行を拍ごとに繰り返さない)。
    (<- (told-once state "" (.format "worker: coordinator {} に名乗りました" endpoint)))
    ;; 次の拍の判断の材料(#1933): 届けた状態の報告・送る間隔・待ちの after(返事の版 — 無ければ旧い coordinator)。
    (setv state.sent-statuses sending
          state.beat-interval-ms (beat-interval-ms timing state.task-echo)
          state.last-desired (DesiredJobs (+ state.last-jobs state.last-tasks) :warm state.last-warm))
    (setv state.watch.after (reply-revision answered))
    (setv state.watch.beats (+ state.watch.beats 1))
    (:= desired state.last-desired)
    (except [error Exception]
      (<- (told-once state (repr error) (+ "worker: coordinator に名乗れない: " (repr error))))
      ;; 届かない間は毎拍送り直す(前の desired を使い続けない — fence の判断を毎拍する)。
      (setv state.last-desired None)
      (:= desired (desired-when-unreachable (- now-ms state.last-ok-ms) state.fence-ms
                                            (+ state.last-jobs state.last-tasks) state.last-warm (repr error)))))
  desired)


(defk polled [state cell options watch-cell]
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions) (: watch-cell RouteCell)]
   :post [(: % (| DesiredJobs DesiredUnreadable))]}
  "拍ごとの ReadDesired に答えるため: heartbeat を送る拍(beat_policy.heartbeat-due)なら送り、それ以外は前の返事の desired を返す。
   返事に版を持つ coordinator へは、名指しの待ちの背景の task を 1 度だけ起こす(待ちを使う口だけ)。"
  (<- now-ms int (now-epoch-ms))
  (<- watching bool (watching? state))
  (var desired state.last-desired)
  (when (heartbeat-due watching (is-not state.last-desired None) state.watch.woken (!= state.statuses state.sent-statuses)
                       (- now-ms state.last-ok-ms) state.beat-interval-ms)
    (<- beaten (beat state cell options))
    (:= desired beaten))
  (val watch state.watch)
  (when (and state.watch-enabled (not watch.running) (not watch.unsupported) (not watch.closing) (not watch.failure)
             (is-not watch.after None))
    (setv watch.running True)
    (<- (Spawn (watch-loop state watch-cell options) :daemon True)))
  desired)


(defk status-rows [state statuses]
  {:pre [(: state LinkState) (: statuses tuple)] :post [(: % list)]}
  "状態の報告を作るため。終わった task には結果の file の中身(無ければ None = 結果なし)を、切り離した task には置かれた時の返事の行を
   添える(形は status-report — sim の宿と同じ関数)。"
  (val results {})
  (for [s statuses]
    (val task-id (finished-task-id s))
    (when task-id
      (<- text (ReadText (os.path.join state.task-dir (+ task-id ".result"))))
      (setv (get results task-id) (if (isinstance text str) text None))))
  (status-report statuses state.task-echo results))


(defhandler coordinator-link [#^ LinkState state #^ RouteCell cell #^ RouteOptions options #^ RouteCell watch-cell]
  ;; 引数に残す理由: 拍から拍へ持ち越す値(state)と宛先の状態(cell・待ちの watch-cell)は組み立てが作る入れ物・送り方は worker の process の値。
  (ReadDesired [env-report]
    ;; heartbeat に載せる root の名乗りは、拍の Program が root の言い換えに問うて欄で渡す(#2467・#2427)。
    (when state.handles-envs
      (setv state.env-report env-report))
    (<- desired (polled state cell options watch-cell))
    (resume desired))
  (PublishStatus [statuses note]
    ;; 状態は次の heartbeat で送る。file にも書くので、同じ効果を外側の status-file へ回す。
    (<- rows list (status-rows state statuses))
    (setv state.statuses rows)
    (reperform effect)))
