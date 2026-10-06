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
;;;   * 周期の頭で、最後の成功から fence を越えていれば heartbeat を待たずに止める(desired-after-silence — 処理が止まって戻った最初の周期・#2806)。
;;;   * 名指しの待ち(#1933)は背景の task(Spawn の daemon)で送り続け、「変わった」と答えたら次の拍で heartbeat を送らせる。
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import json)
(import dataclasses [replace])
(import os)
(import pathlib [Path])
(import doeff_core_effects [slog])
(import doeff_core_effects.file_effects [FileFailed PathKind ReadText WriteText MakeDirectory ListDirectory RemoveTree file-done])
(import doeff_core_effects.process_effects [EnvEntry ReadEnvironment])
(import doeff_core_effects.http_effects [HttpResponse HttpFailed])
(import doeff_core_effects.scheduler [Spawn CreatePromise CompletePromise Promise])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.remote_rules [program-sha])
(import doeff_cluster.shared.core.native_wheel [current-platform])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions RoutedReply routed-request answer-json])
(import doeff_cluster.worker.core.beat_policy [WatchKind WatchReading beat-interval-ms heartbeat-due watch-reading reply-revision
                                               WATCH-RETRY-SECONDS WAKE-HOLD-SECONDS])
(import doeff_cluster.shared.intent.protocol [WATCH-MAX-SECONDS ClusterTiming])
(import doeff_cluster.worker.core.heartbeat_rules [warm-env-of-row finished-task-id desired-when-unreachable desired-after-silence])
(import doeff_cluster.worker.core.launch [program-file-text spec-program-name])
(import doeff_cluster.worker.core.heartbeat_rules [keep-marks-held])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs DesiredUnreadable ReadDesired PublishStatus BootMarks])
(import doeff_cluster.worker.core.boot_timing [boot-line])
(import doeff_cluster.worker.protocol.declared [DeclaredReply declared-reply-of-json declared-job-specs task-specs])
(import doeff_cluster.worker.protocol.heartbeat [env-heartbeat-part heartbeat-body status-report])


(defclass WatchCell []
  "名指しの待ちの背景の task と拍が分ける値(#1933 — beat_policy)。after = 次の待ちの版(前の heartbeat の返事の版)・confirmed = 待ちが
   1 度答えた(口を確かめた)・unsupported = 待つ口が無い(404)・woken = 待ちが「変わった」と答えた印・beats = 届いた heartbeat の数
   (待ちが「変わった」の後、heartbeat が版を進めたかを見る)・running = 背景の task が走っている・closing = 止めの合図・failure = 背景の
   task が止まった理由・told = 最後に出した 1 行の鍵・bell = 拍の間の眠りを起こす呼び鈴(#2692 — 待ちが「変わった」と答えた時に鳴らして
   手放し、次の宣言の読みが新しく掛ける。鳴るまでは拍をまたいで同じ物を渡す)。scheduler の task は 1 つの thread で交互に走るので錠は
   要らない。"
  (defn #^ None __init__ [self]
    (setv self.after None self.confirmed False self.unsupported False self.woken False self.beats 0 self.running False
          self.closing False self.failure "" self.told None self.bell None)))


(defclass LinkState []
  "coordinator への口が拍から拍へ持ち越す値の入れ物(頭の註)。組み立て(main・検)が作り、handler coordinator-link と背景の待ちが書き換える。
   name = worker の名・provides / exclusive = 提供する能力・専用の能力の名・capacity = 同時の job の上限・task-reserve = capacity のうち
   task のために空けておく数(heartbeat で必ず名乗る — 常駐の job はこの分に置かれない)・fence-ms = 途絶で止める長さ
   (返事の timing が上書きする)・task-dir = task の印と結果の file の置き場・versions = 名乗る版・tools = 名乗る道具(名 → 版)・
   handles-envs = 実行環境の job を扱うか(真なら root の名乗りを heartbeat に載せ、温める表を受ける)・node = k8s の node の名・
   watch = 名指しの待ちを使うか(本番の入口が真にする)・boot = この process の世代・boot-at = 起動時刻(epoch ms)・started-ms = 最後の連絡と
   みなす初めの時刻(一度も届かない worker は fence の後に何も動かさない)・boot-marks = 起動の内訳の刻(最初の heartbeat の答えの後に
   1 行で出す — None = 出さない・#3676)。"
  (defn #^ None __init__ [self #^ str name #^ tuple provides #^ int capacity #^ int task-reserve #^ int fence-ms #^ str task-dir #^ str boot
                          #^ int boot-at #^ int started-ms * #^ (| dict None) [versions None] #^ (| dict None) [tools None]
                          #^ bool [handles-envs False] #^ tuple [exclusive #()] #^ str [node ""] #^ bool [watch False]
                          #^ (| BootMarks None) [boot-marks None]]
    (setv self.name name self.provides provides self.exclusive exclusive self.node node self.capacity capacity
          self.task-reserve task-reserve self.tools (or tools {})
          self.handles-envs handles-envs self.env-report None
          self.fence-ms fence-ms self.statuses []
          ;; 途絶しても動かし続けてよい印の在る job を止めるまでの長い方の柵(#2804 — 返事の timing の keep_fence_ms が上書きする。
          ;; 欄の無い返事 = 古い coordinator は印も付けないので、この既定が使われる事は無い)。
          self.keep-fence-ms (. (ClusterTiming) keep-fence-ms)
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
          self.last-desired None self.sent-statuses None self.beat-interval-ms (beat-interval-ms None {})
          ;; 止まり始め(#2819): stopping = 拍の Program が渡した止まり・sent-stopping = 前に届けた heartbeat に載せた止まり(違えば
          ;; 送る間隔を待たずに送る — 状態の報告の違いと同じ扱い)。
          self.stopping False self.sent-stopping False
          ;; 起動の内訳の刻(#3676 — 本番の入口が渡す・None = 出さない)と、その 1 行を出したか(最初の heartbeat の答えの後に 1 度だけ)。
          self.boot-marks boot-marks self.boot-told False)))



(deff watch-params [#^ int after #^ str worker #^ str boot #^ bool confirmed]  ; defk にできない: この口と sim の宿が同じ問いを作る(worker/core/beat_policy から移した — 役 protocol)
  {:pre [(: after int) (: worker str) (: boot str) (: confirmed bool)] :post [(: % dict)] :tags {:context "worker" :role "protocol"}}
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
  {:pre [(: state LinkState) (: tasks list)] :post [(: % tuple)] :tags {:context "worker" :role "protocol" :reads "json"}}
  "heartbeat の返事の task の行 → 1 度だけ走らせる job。task ごとに、その Program の cache の file(Program のキーと task の行の版で
   決まる — program-dir からの相対 path・launch.spec-program-name・#3762)を印の file <id>.program に残し
   (返事から外れた task の Program の cache を後で消すため — fetched-programs)、返事から外れた task の結果の file を消す(この worker が
   書いた物だけ)。切り離した task は返事の行をそのまま写しとして持つ(状態の報告に添える)。"
  (<- (file-done (MakeDirectory state.task-dir)))
  (val ids (sfor t tasks (get t "id")))
  ;; 印の file は読んだ job(task-specs — 版は task の行の versions)から書く(返事の行の読みを 2 か所に置かない)。
  (<- specs tuple (task-specs tasks (Path state.task-dir)))
  (for [spec specs]
    (val mark (os.path.join state.task-dir (+ (cut spec.name 5 None) ".program")))
    (<- cached str (spec-program-name spec))
    ;; 同じ id の印が別の file を指していれば書き直す(印は cache の掃除にだけ使う — 子へ渡す file は返事の行の sha と版で決まる)。
    (<- marked (ReadText mark))
    (when (!= (if (isinstance marked str) marked None) cached)
      (<- (file-done (WriteText mark cached :replace True)))))
  ;; .blob = 詰めた Program を行に持っていた版の worker が書いた file(置き場 /programs の前)— 残っていれば一緒に消す。
  (<- entries (ListDirectory state.task-dir))
  (when (not (isinstance entries FileFailed))
    (for [entry entries]
      (val parts (os.path.splitext entry.name))
      (when (and (in (get parts 1) #(".blob" ".result")) (not-in (get parts 0) ids))
        (<- (RemoveTree (os.path.join state.task-dir entry.name))))))
  (setv state.task-echo (dfor task tasks :if (.get task "detached") (get task "id") (dict task)))
  specs)


(defk fetched-program [cell options path sha versions]
  {:pre [(: cell RouteCell) (: options RouteOptions) (: path str) (: sha str) (: versions (| tuple None))] :post [(: % None)]
   :tags {:context "worker" :role "protocol" :reads "json"}}
  "詰めた Program 1 つを coordinator の /programs/<sha> から取り、子の入口が読む形の cache の file path({\"blob\" \"versions\"})に置くため。
   versions = 子の入口が比べる送り手の版: task は task の行の版(名の順の #(名 版) の組 — #3762)・None は答えの versions(service —
   coordinator の Program の行の版)。中身の
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
      (do (<- (file-done (MakeDirectory (os.path.dirname path))))
          (<- (file-done (WriteText path (program-file-text (get body "blob")
                                                            (match versions
                                                              None (.get body "versions" {})
                                                              pairs (dict pairs)))
                                    :replace True)))))
  None)


(defk fetched-programs [state cell options specs]
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions) (: specs tuple)] :post [(: % None)]}
  "宣言の job と task のうち、cache に無い詰めた Program を取り寄せ(改訂 1 の F — service と task で同じ仕組み)、返事から外れた task の
   Program の cache を、今の job と task のどれも参照していなければ消すため(task ごとの印 <id>.program から引く — service の job の
   Program は消さない)。cache の file は service が Program のキーごと・task が (Program のキー・task の行の版) ごと(#3762 — 同じ sha
   でも版の違う task は別の file を読む・launch.spec-program-name)。"
  (<- (file-done (MakeDirectory state.program-dir)))
  (var wanted #())
  (for [s specs :if s.program]
    (<- name str (spec-program-name s))
    (when (not-in name wanted)
      (:= wanted (+ wanted #(name)))
      (val path (os.path.join state.program-dir name))
      (<- found (ReadText path))
      (when (isinstance found FileFailed)
        (<- (fetched-program cell options path s.program s.versions)))))
  (val current (sfor s specs :if (.startswith s.name "task/") (cut s.name 5 None)))
  (<- entries (ListDirectory state.task-dir))
  (when (not (isinstance entries FileFailed))
    (for [entry entries]
      (val parts (os.path.splitext entry.name))
      (when (and (= (get parts 1) ".program") (not-in (get parts 0) current))
        (val mark (os.path.join state.task-dir entry.name))
        (<- text (ReadText mark))
        (val marked (if (isinstance text str) (.strip text) ""))
        (when (and marked (not-in marked wanted))
          (<- (RemoveTree (os.path.join state.program-dir marked))))
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
   書くため(mtime = 最後に届いた時刻 — probe は中身が ready で新しい時だけ Ready)。環境変数は ReadEnvironment で読む(在る名の分だけ
   答えが返る — 本物 = 入口の subprocess-handler が os.environ から・sim = 台本の process の handler。protocol の層は os.environ に触らない・#3014)。"
  (<- found (get tuple #(EnvEntry ...)) (ReadEnvironment #("DOEFF_WORKER_READY_FILE")))
  (when found
    (<- (file-done (WriteText (. (get found 0) value) (if draining "draining\n" "ready\n") :replace True))))
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


(defk bell-rung [watch]
  {:pre [(: watch WatchCell)] :post [(: % None)]}
  "待ちが「変わった」と答えた時に、拍の間の眠りを起こす呼び鈴を鳴らして手放すため(#2692 — 眠っている拍が tick-seconds を待たずに
   起きて heartbeat を送る)。1 回の眠りの間に変化が何度来ても鳴るのは掛かっていた 1 つだけ(次の宣言の読みが新しく掛ける)。"
  (val bell watch.bell)
  (when (is-not bell None)
    (setv watch.bell None)
    (<- (CompletePromise bell True)))
  None)


(defk armed-bell [state]
  {:pre [(: state LinkState)] :post [(: % (| Promise None))]}
  "拍の間の眠りを宣言の変化で起こす呼び鈴を返すため(#2692)。待ちの口を使えていない間は None(拍ごとに heartbeat を送るので起こしは
   要らない)。まだ鳴っていない呼び鈴が在ればそれを渡し(拍ごとに作らない)、無ければ新しく掛ける。"
  (<- watching bool (watching? state))
  (val watch state.watch)
  (cond
    (not watching) None
    (is-not watch.bell None) watch.bell
    True (do (<- bell Promise (CreatePromise))
             (setv watch.bell bell)
             bell)))


(defk with-bell [read bell]
  {:pre [(: read (| DesiredJobs DesiredUnreadable)) (: bell (| Promise None))] :post [(: % (| DesiredJobs DesiredUnreadable))]}
  "宣言の読みに拍の間の眠りを起こす呼び鈴を添えるため(読めた宣言だけ — 読めない時は拍の上限まで眠る)。この口と sim の宿が同じ添え方を
   使う(#2692)。"
  (match #(read bell)
    #((DesiredJobs) (Promise)) (replace read :changed bell.future)
    _ read))


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
                      (<- (bell-rung watch))
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
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions)] :post [(: % (| DesiredJobs DesiredUnreadable))]
   :tags {:context "worker" :role "protocol" :spells "json" :reads "json"}}
  "heartbeat を 1 回送り、返事の job・task・温める表を desired にするため。届かなければ desired-when-unreachable(fence の判断)。"
  (val sending state.statuses)
  (val stopping state.stopping)
  ;; 送る前に起こしの印を下ろす(送った後に来た変化の印を消さない)。
  (setv state.watch.woken False)
  (val endpoint (get cell.route.urls cell.route.active))
  ;; 今持っている印 = 最後に届いた返事の job の印(#2804 — coordinator はこれで印の約束を外す)。
  (<- kept tuple (keep-marks-held state.last-jobs))
  (<- base dict (heartbeat-body :name state.name :provides state.provides :exclusive state.exclusive :node state.node
                                :capacity state.capacity :task-reserve state.task-reserve :versions state.versions :statuses sending
                                :endpoint endpoint :boot state.boot :boot-at state.boot-at :tools state.tools :kept kept
                                :stopping stopping))
  (val body (| base
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
    ;; 返事の宣言の部分(job の行と draining)を JSON の境界で 1 度だけ解く(#3684)— ready の file と、返事の job の版を据え置く印
    ;; (declared-job-specs)が同じ draining を読む。形の違う返事(draining の無い返事など)は DeclaredReplyMalformed で落ち、下の except が
    ;; 「名乗れない」の 1 行にして「読めない」を返す(拍は前の宣言のまま動かし、ready の file は書かない — 読めない返事で版を入れ替えない)。
    (<- declared DeclaredReply (declared-reply-of-json answered))
    (<- (ready-file-written declared.draining))
    ;; 自己停止の時間は coordinator の ClusterTiming が持つ(移し替えの時間と組で決まる)。受け取った値に合わせる。
    (val timing (.get answered "timing"))
    (when (and timing (in "fence_ms" timing))
      (setv state.fence-ms (int (get timing "fence_ms"))))
    ;; 印の在る job の長い方の柵(#2804)も同じく受け取る。
    (when (and timing (in "keep_fence_ms" timing))
      (setv state.keep-fence-ms (int (get timing "keep_fence_ms"))))
    (<- jobs tuple (declared-job-specs declared))
    (setv state.last-jobs jobs)
    (<- tasks tuple (accepted-tasks state (.get answered "tasks" [])))
    (setv state.last-tasks tasks)
    (<- (fetched-programs state cell options (+ state.last-jobs state.last-tasks)))
    (<- warm tuple (accepted-warm state (.get answered "warm" [])))
    (setv state.last-warm warm)
    ;; 返事を読み終えてから出す(返事の読みが毎回落ちる時に、名乗れた・名乗れないの 2 行を拍ごとに繰り返さない)。
    (<- (told-once state "" (.format "worker: coordinator {} に名乗りました" endpoint)))
    ;; 起動の内訳の 1 行(#3676): 最初に返事を読み終えた拍で 1 度だけ — Pod の起動 → boot.sh → exec → import → この答え。
    (when (and (is-not state.boot-marks None) (not state.boot-told))
      (setv state.boot-told True)
      (<- line str (boot-line state.boot-marks now-ms))
      (<- (slog line)))
    ;; 次の拍の判断の材料(#1933): 届けた状態の報告・送る間隔・待ちの after(返事の版 — 無ければ旧い coordinator)。
    (setv state.sent-statuses sending
          state.sent-stopping stopping
          state.beat-interval-ms (beat-interval-ms timing state.task-echo)
          state.last-desired (DesiredJobs (+ state.last-jobs state.last-tasks) :warm state.last-warm))
    (setv state.watch.after (reply-revision answered))
    (setv state.watch.beats (+ state.watch.beats 1))
    (:= desired state.last-desired)
    (except [error Exception]
      (<- (told-once state (repr error) (+ "worker: coordinator に名乗れない: " (repr error))))
      ;; 届かない間は毎拍送り直す(前の desired を使い続けない — fence の判断を毎拍する)。
      (setv state.last-desired None)
      (:= desired (desired-when-unreachable (- now-ms state.last-ok-ms) state.fence-ms state.keep-fence-ms
                                            (+ state.last-jobs state.last-tasks) state.last-warm (repr error)))))
  desired)


(defk polled [state cell options watch-cell]
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions) (: watch-cell RouteCell)]
   :post [(: % (| DesiredJobs DesiredUnreadable))]}
  "拍ごとの ReadDesired に答えるため: heartbeat を送る拍(beat_policy.heartbeat-due)なら送り、それ以外は前の返事の desired を返す。
   返事に版を持つ coordinator へは、名指しの待ちの背景の task を 1 度だけ起こす(待ちを使う口だけ)。"
  (<- now-ms int (now-epoch-ms))
  ;; 自己停止を周期ごとに時間で判じる(#2806): 処理が止まって heartbeat を送れなかった worker は、戻った最初の周期で最後の成功から fence を
  ;; 越えていれば、この周期は heartbeat を送らず印の無い job と task を止めた宣言を返す(返事を待つ間に動かし続けない)。最後の宣言を捨てる
  ;; ので、次の周期は heartbeat を送る — 届けば返事で戻し、届かなければ desired-when-unreachable が判じる。
  (val silenced (desired-after-silence (- now-ms state.last-ok-ms) state.fence-ms state.keep-fence-ms (is-not state.last-desired None)
                                       (+ state.last-jobs state.last-tasks) state.last-warm))
  (when (is-not silenced None)
    (setv state.last-desired None)
    (return silenced))
  (<- watching bool (watching? state))
  (var desired state.last-desired)
  ;; 止まり始めは状態の報告の違いと同じく、送る間隔を待たずに名乗る(#2819)。
  (when (heartbeat-due watching (is-not state.last-desired None) state.watch.woken
                       (or (!= state.statuses state.sent-statuses) (!= state.stopping state.sent-stopping))
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
  {:pre [(: state LinkState) (: statuses tuple)] :post [(: % list)] :tags {:context "worker" :role "protocol" :spells "json"}}
  "状態の報告を作るため。終わった task には結果の file の中身(無ければ None = 結果なし)を、切り離した task には置かれた時の返事の行を
   添える(形は status-report — sim の宿と同じ関数)。"
  (val results {})
  (for [s statuses]
    (val task-id (finished-task-id s))
    (when task-id
      (<- text (ReadText (os.path.join state.task-dir (+ task-id ".result"))))
      (setv (get results task-id) (if (isinstance text str) text None))))
  (<- rows list (status-report statuses state.task-echo results))
  rows)


(defhandler coordinator-link [#^ LinkState state #^ RouteCell cell #^ RouteOptions options #^ RouteCell watch-cell]
  ;; 引数に残す理由: 拍から拍へ持ち越す値(state)と宛先の状態(cell・待ちの watch-cell)は組み立てが作る入れ物・送り方は worker の process の値。
  (ReadDesired [env-report stopping]
    ;; heartbeat に載せる root の名乗りは、拍の Program が root の言い換えに問うて欄で渡す(#2467・#2427)。止まり始めも同じく欄で
    ;; 渡し、heartbeat で名乗る(#2819)。
    (when state.handles-envs
      (setv state.env-report env-report))
    (setv state.stopping stopping)
    ;; 拍の間の眠りを起こす呼び鈴は heartbeat の前に掛ける(送っている間に来た変化も鳴らす — #2692)。
    (<- bell (| Promise None) (armed-bell state))
    (<- desired (polled state cell options watch-cell))
    (<- belled (| DesiredJobs DesiredUnreadable) (with-bell desired bell))
    (resume belled))
  (PublishStatus [statuses note]
    ;; 状態は次の heartbeat で送る。file にも書くので、同じ効果を外側の status-file へ回す。
    (<- rows list (status-rows state statuses))
    (setv state.statuses rows)
    (reperform effect)))
