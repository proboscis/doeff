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
;;;   * 沈黙の長さ(最後に届いた返事から・前の送りの始めから)は、壁の時計と単調な時計の両方で読んだ起点から heartbeat_rules.silent-ms の
;;;     1 か所で計る(2 つの差の長い方 — 機体の壁の時計が戻っても自己停止と送りが遅れない・card ki-069f4a5c07f7)。起きる期限(壁の時計の
;;;     DueAt)は、今の壁の時計から沈黙を引いた刻(今の壁の枠へ写した起点)から組み立てる。
;;;   * 周期の頭で、最後の成功から fence を越えていれば heartbeat を待たずに止める(desired-after-silence — 処理が止まって戻った最初の周期・#2806)。
;;;   * 名指しの待ち(#1933)は背景の task(Spawn の daemon)で送り続け、「変わった」と答えたら次の拍で heartbeat を送らせる。
;;;   * 拍の外の送り(card ki-e38dbfca7671)も背景の task(Spawn の daemon)で、前の送りから送信間隔が過ぎたら拍を待たずに送る — 拍が
;;;     lease より長くかかっても、送りの間が拍の長さに依らない。拍の送りと背景の送りは 1 つの送り口(sent-beat)を通り、1 本ずつ送る。
;;;   * 生存の合図(card ki-9b58446e2ad6): 背景の task は scheduler の上で回るので、拍が scheduler を塞ぐ間(同期の処理)や、scheduler の
;;;     外の完了(HTTP の asyncio の橋・file の thread)が返らない間は送れない(zeus 2026-10-24 05:42〜05:46 JST — 拍の 250 秒の間、
;;;     背景の task も外の完了を待ったまま、lease が切れて task を全部 lost にされた)。scheduler の外の OS の thread(foundation/lifeline
;;;     — 入口が起こす)が、拍の側の送りが送信間隔 × beat_policy.LIFELINE-SLACK-RATIO を過ぎても来ない時に、拍が最後に送った本文を
;;;     そのまま送り直す。返事は読まない(宣言の読みは拍の送りだけ — coordinator は同じ本文の送り直しで lease と生存だけを延ばし、
;;;     置いた task を次の返事でも送る)。thread の送りに届いた返事の刻は沈黙の計りに入る(拍が戻った最初の周期で、coordinator が
;;;     生きていると見ていた間を途絶と数えて自分の job を止めない)。分ける値は入れ物 LifeCell。
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import json)
(import dataclasses [replace])
(import os)
(import pathlib [Path])
(import doeff_core_effects [slog])
(import doeff_core_effects [FileFailed PathKind ReadText WriteText MakeDirectory ListDirectory RemoveTree]
        doeff_core_effects._file_effects [file-done])
(import doeff_core_effects [EnvEntry ReadEnvironment])
(import doeff_core_effects [HttpResponse HttpFailed])
(import doeff_core_effects [Spawn CreatePromise CompletePromise Promise Wait])
(import doeff_time [Delay GetMonotonic])
(import doeff_cluster.shared.core.clock [now-epoch-ms monotonic-ms-of datetime-of-epoch-ms])
(import doeff_cluster.shared.core.remote_rules [program-sha])
(import doeff_cluster.shared.core.native_wheel [current-platform])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions RoutedReply routed-request answer-json])
(import doeff_cluster.worker.core.beat_policy [WatchKind WatchReading beat-interval-ms heartbeat-due watch-reading reply-revision
                                               lifeline-due WATCH-RETRY-SECONDS WAKE-HOLD-SECONDS])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.worker.core.heartbeat_rules [warm-env-of-row finished-task-id desired-when-unreachable desired-after-silence
                                                    silent-ms])
(import doeff_cluster.worker.core.launch [program-file-text spec-program-name])
(import doeff_cluster.worker.core.heartbeat_rules [keep-marks-held])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs DesiredUnreadable HeartbeatSent ReadDesired PublishStatus BootMarks WakeSet
                                                 WorkerWakes])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.shared.core.due_policy [earliest-due])
(import doeff_cluster.worker.core.worker_due [beat-due fence-due wakes-with])
(import doeff_cluster.worker.core.boot_timing [boot-line])
(import doeff_cluster.worker.protocol.declared [DeclaredReply declared-reply-of-json declared-job-specs task-specs])
(import doeff_cluster.worker.protocol.heartbeat [env-heartbeat-part heartbeat-body status-report])
(import doeff_cluster.worker.core.account_token [TokenClaims TokenState token-claims token-state token-read-due])
(import doeff_cluster.worker.core.room_rules [RoomReading RoomState WorkerRoom room-of-text room-read-due room-line])

;; 前の heartbeat が届いていない時に、送り直しの周を起こすまでの ms(届かない coordinator の戻りを知る試し — 前の周期 0.5 秒と同じ速さ・
;; #3871 の単位 4)。
(val RESEND-AFTER-MS 500)
;; 生存の合図の thread の送りの事実を、拍の宣言の読みが受け取るまで持つ本数(card ki-9b58446e2ad6)。止まりの上限 30 分の間の送り
;; (4 秒ごと)を覆う。越えた古い送りは heartbeat の間の計り(#3850)から落ちるだけ。
(val LIFELINE-SENT-KEPT 512)


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


(defclass BeatCell []
  "heartbeat の送り口(sent-beat)と拍の外の背景の task が分ける値(card ki-e38dbfca7671)。in-flight = 今 答えを待っている送りの終わりの
   約束(None = 送っていない — 送りは 1 本ずつ。送っている間に来た求めは、この約束が満ちるのを待つ)・started-at = 最後に送りを始めた刻
   (epoch ms・None = まだ送っていない — 背景の task はここから送信間隔を数える)・started-mono-ms = 同じ刻の単調な時計の読み(ms —
   間隔は heartbeat_rules.silent-ms で数える)・unread = 拍の宣言の読みがまだ受け取っていない送りの
   事実(HeartbeatSent の列 — 送った順)・running = 背景の task が走っている・closing = 止めの合図・failure = 背景の task が止まった理由。
   scheduler の task は 1 つの thread で交互に走るので錠は要らない。"
  (defn #^ None __init__ [self]
    (setv self.in-flight None self.started-at None self.started-mono-ms None self.unread #() self.running False self.closing False
          self.failure "")))


(defclass LifeCell []
  "生存の合図の thread(scheduler の外の OS の thread — 頭の註・card ki-9b58446e2ad6)と coordinator への口が分ける値。
   post = 拍が最後に送った heartbeat の #(URL 本文の JSON の bytes)(None = まだ送っていない — thread は送らない)。拍が書き、thread が
   1 つの欄として読む(組を 1 度に入れ替える)・started-at / started-mono-ms = thread が最後に送りを始めた刻の対・last-ok-ms /
   last-ok-mono-ms = thread の送りに最後に返事(2xx)が届いた刻の対(初め 0 — 届いていない)・progress-at / progress-mono-ms = 拍が最後に
   宣言を読みに来た刻の対(拍の進みの印 — 越えて止まったら thread は送らない・beat_policy.LIFELINE-STALL-LIMIT-MS)・sent = thread の
   送りの #(通しの本数 直近の送りの事実の列)(HeartbeatSent — 直近 LIFELINE-SENT-KEPT 本。書くのは thread だけ)・read-total = 拍の
   宣言の読みが受け取り済みの通しの本数(書くのは拍だけ)。欄ごとに書き手が 1 つなので錠を持たない・
   failure = thread の最後の送りの失敗の理由(\"\" = 届いた)・told = 拍の口が最後に log に出した失敗の理由。
   刻の対は書き手が 1 つの setv で書き、読み手は錠を取らずに読む: 別の thread の書きの間に読むと、前後 2 回の送りの刻が混ざった対に
   なりうるが、沈黙の計り(heartbeat_rules.silent-ms — 2 つの差の長い方)は古い方の刻で計るので、沈黙を長く見る側(早く送る・早く
   止める側)にしか外れない。"
  (defn #^ None __init__ [self]
    (setv self.post None self.started-at None self.started-mono-ms None
          self.last-ok-ms 0 self.last-ok-mono-ms 0 self.progress-at None self.progress-mono-ms None self.sent #(0 #()) self.read-total 0
          self.failure ""
          self.told "")))


(defclass LinkState []
  "coordinator への口が拍から拍へ持ち越す値の入れ物(頭の註)。組み立て(main・検)が作り、handler coordinator-link と背景の待ちが書き換える。
   name = worker の名・provides / exclusive = 提供する能力・専用の能力の名・capacity = 同時の job の上限・task-reserve = capacity のうち
   task のために空けておく数(heartbeat で必ず名乗る — 常駐の job はこの分に置かれない)・fence-ms = 途絶で止める長さ
   (返事の timing が上書きする)・task-dir = task の印と結果の file の置き場・versions = 名乗る版・tools = 名乗る道具(名 → 版)・
   handles-envs = 実行環境の job を扱うか(真なら root の名乗りを heartbeat に載せ、温める表を受ける)・node = k8s の node の名・
   watch = 名指しの待ちを使うか(本番の入口が真にする)・beat-beside = 拍の外の背景の task でも heartbeat を送るか(既定 真 — card
   ki-e38dbfca7671。偽は操作ごとに run を分ける検の道具だけ: 背景の task は run をまたいで生きない)・boot = この process の世代・
   boot-at = 起動時刻(epoch ms)・started-ms = 最後の連絡とみなす初めの時刻(一度も届かない worker は fence の後に何も動かさない)・
   started-mono-ms = 同じ刻の単調な時計の読み(ms — 沈黙は壁の時計と単調な時計の両方で計る・heartbeat_rules.silent-ms)・
   boot-marks = 起動の内訳の刻(最初の heartbeat の答えの後に 1 行で出す — None = 出さない・#3676)・overlaps-elder = この世代は同じ名の
   前の世代がまだ走っている間に起きた(起動の script が渡す — heartbeat に載せ、coordinator は前の世代の lease を外さない・AL-D2e)・
   token-file = ServiceAccount の token の file(None = 読まない — 本番の入口が標準の path を渡す。読んだ状態を heartbeat の
   accountToken で名乗る・card ki-d31166f84ad0)・room-file = 枠の file(None = 読まない — 起動の引数の capacity・task-reserve を
   名乗り続ける。渡すと heartbeat の前に読み直し、自分の名の行の値を名乗る・card ki-6828eda880ce)・doeff-commit = この process の doeff の
   版(起動の script の WORKER_DOEFF_COMMIT — 空 = 名乗らない。heartbeat の doeffCommit・card ki-6e83e03d146d)。
   supplied = 最後に届いた返事の、この worker に置かれうる job の参照の綴り(None = まだ渡されていない — 値の写しの係 supplied_hold が
   読む・ADR-DOE-CLUSTER-004)。"
  (defn #^ None __init__ [self #^ str name #^ tuple provides #^ int capacity #^ int task-reserve #^ int fence-ms #^ str task-dir #^ str boot
                          #^ int boot-at #^ int started-ms #^ int started-mono-ms * #^ (| dict None) [versions None]
                          #^ (| dict None) [tools None]
                          #^ bool [handles-envs False] #^ tuple [exclusive #()] #^ str [node ""] #^ bool [watch False]
                          #^ bool [beat-beside True] #^ (| BootMarks None) [boot-marks None] #^ bool [overlaps-elder False]
                          #^ (| str None) [token-file None] #^ (| str None) [room-file None] #^ str [doeff-commit ""]]
    (setv self.name name self.provides provides self.exclusive exclusive self.node node self.capacity capacity
          self.task-reserve task-reserve self.tools (or tools {})
          self.handles-envs handles-envs self.env-report None
          self.fence-ms fence-ms self.statuses []
          ;; 途絶しても動かし続けてよい印の在る job を止めるまでの長い方の柵(#2804 — 返事の timing の keep_fence_ms が上書きする。
          ;; 欄の無い返事 = 古い coordinator は印も付けないので、この既定が使われる事は無い)。
          self.keep-fence-ms (. (ClusterTiming) keep-fence-ms)
          ;; 名指しの待ち(GET /watch)で待ってもらう上限(返事の timing の watch_max_ms が上書きする — 最初の返事の前は既定・#3865)。
          self.watch-max-ms (. (ClusterTiming) watch-max-ms)
          ;; coordinator が生きていると数える窓(返事の timing の lease_ms が上書きする — 最初の返事の前は既定)。送った heartbeat の事実
          ;; (HeartbeatSent)に載せ、拍の Program が heartbeat の間の遅れの閾にする(#3850)。
          self.lease-ms (. (ClusterTiming) lease-ms)
          self.task-dir task-dir self.versions (or versions {})
          ;; 詰めた Program の cache(/programs/<sha> から取る — 子 process の言い換えと同じ state dir の programs・改訂 1 の F)。
          self.program-dir (os.path.join (os.path.dirname (os.path.abspath task-dir)) "programs")
          ;; 最後に届いた返事の刻(壁の時計の epoch ms と、同じ刻の単調な時計の ms — 届くたびに 2 つを一緒に書く)。
          self.last-ok-ms started-ms self.last-ok-mono-ms started-mono-ms
          ;; 最後に受け取った job と task の宣言(途絶の間も動かす書き手と切り離した task を選ぶ — worker_policy.kept-when-cut-off)。
          self.last-jobs #() self.last-tasks #() self.last-warm #() self.warm-keys {}
          ;; この worker に置かれうる job の参照の綴り(返事の supplied — 欄の無い返事では前の値のまま。値の写しの係が読む)。
          self.supplied None
          self.boot boot self.boot-at boot-at self.overlaps-elder overlaps-elder self.doeff-commit doeff-commit
          ;; 切り離した task の id → 置かれた時の返事の行。状態の報告に写して添え、状態を失った coordinator が走っている task を引き取れる
          ;; ようにする(cluster_policy.adopt-running-detached・2026-09-27)。
          self.task-echo {}
          ;; 最後に出した heartbeat の結果(None = まだ出していない・"" = 名乗れた・それ以外 = 名乗れない理由)。
          self.told None
          ;; heartbeat の切り離し(#1933): last-desired = 前の heartbeat の返事の desired(届かなかったら None — 次の拍で必ず送る)・
          ;; sent-statuses = 前に届けた状態の報告・beat-interval-ms = 送る間隔。
          self.watch-enabled watch self.watch (WatchCell)
          self.last-desired None self.sent-statuses None self.beat-interval-ms (beat-interval-ms None {})
          ;; 拍の外の送り(card ki-e38dbfca7671): beat-beside-enabled = 背景の task で送るか・beating = 送り口と背景の task が分ける値。
          self.beat-beside-enabled beat-beside self.beating (BeatCell)
          ;; 生存の合図の thread と分ける値(card ki-9b58446e2ad6 — thread を起こすのは入口。起こさない組では書かれるだけで、読みは
          ;; 前と同じ答えになる)。
          self.life (LifeCell)
          ;; 止まり始め(#2819): stopping = 拍の Program が渡した止まり・sent-stopping = 前に届けた heartbeat に載せた止まり(違えば
          ;; 送る間隔を待たずに送る — 状態の報告の違いと同じ扱い)。
          self.stopping False self.sent-stopping False
          ;; 起動の内訳の刻(#3676 — 本番の入口が渡す・None = 出さない)と、その 1 行を出したか(最初の heartbeat の答えの後に 1 度だけ)。
          self.boot-marks boot-marks self.boot-told False
          ;; ServiceAccount の token の古さ(card ki-d31166f84ad0): token-file = 読む file(None = 読まない)・account-token = 最後に読んだ
          ;; 状態(None = まだ読んでいない・読めない file — heartbeat に載せない)・token-read-ms = 最後に読んだ刻。
          self.token-file token-file self.account-token None self.token-read-ms None
          ;; 枠の file(card ki-6828eda880ce): room-file = 読む file(None = 読まない)・room-state = 最後に読んだ状態(None = まだ読んで
          ;; いない・読まない worker — heartbeat に載せない)・room-reason = ok でない訳(同じ訳の 2 度目は log に出さない)・
          ;; room-read-ms = 最後に読んだ刻。pools = 枠の file から名乗る置ける組の印(heartbeat の provides に足す)・memory-mib = 名乗る
          ;; 常駐の job の memory の予算(None = 名乗らない — card ki-ced3c45980a5)。どちらも file を読めない間は前の値のまま。
          self.room-file room-file self.room-state None self.room-reason "" self.room-read-ms None
          self.pools #() self.memory-mib None)))



(deff watch-params [#^ int after #^ str worker #^ str boot #^ bool confirmed #^ int limit-ms]  ; defk にできない: この口と sim の宿が同じ問いを作る(worker/core/beat_policy から移した — 役 protocol)
  {:pre [(: after int) (: worker str) (: boot str) (: confirmed bool) (: limit-ms int)] :post [(: % dict)] :tags {:context "worker" :role "protocol"}}
  "名指しの待ちの問い(GET /watch の query)を作るため。まだ口を確かめていない最初の待ちは 0 秒(すぐ答える — 待つ口の有無を確かめ、
   確かめるまで毎拍の heartbeat を続ける)、その後は上限 limit-ms(coordinator の timing の watch-max-ms — heartbeat の返事で受け取る)まで待つ。"
  {"after" (str after) "timeoutSeconds" (str (if confirmed (/ limit-ms 1000.0) 0.0)) "worker" worker "boot" boot})

(defk link-silence [state now-ms]
  {:pre [(: state LinkState) (: now-ms int)] :post [(: % int)]}
  "最後に届いた返事からの沈黙の長さ(ms)を、今の壁の時計の読み now-ms と、ここで読む単調な時計とで計るため(計りは
   heartbeat_rules.silent-ms の 1 か所 — 途絶の柵・heartbeat を送る間隔・起きる期限がどれもこの値を使う)。"
  (<- mono float (GetMonotonic))
  (<- silent int (heard-silence state now-ms (monotonic-ms-of mono)))
  silent)


(defk heard-silence [state now-ms now-mono-ms]
  {:pre [(: state LinkState) (: now-ms int) (: now-mono-ms int)] :post [(: % int)]}
  "最後に届いた返事からの沈黙の長さ(ms)を、拍の側の送り(拍・拍の外の背景の task)の返事と、生存の合図の thread の送りの返事の新しい方
   から計るため(card ki-9b58446e2ad6 — thread の送りが届いている間は coordinator が worker を生きていると見ているので、拍が戻った
   最初の周期で途絶と数えて自分の job を止めない)。今の刻の対は呼び手が読む(2 つの時計の読みを足さない)。"
  (val life state.life)
  (<- by-tick int (silent-ms now-ms now-mono-ms state.last-ok-ms state.last-ok-mono-ms))
  (<- by-lifeline int (silent-ms now-ms now-mono-ms life.last-ok-ms life.last-ok-mono-ms))
  (min by-tick by-lifeline))


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
  {:pre [(: cell RouteCell) (: options RouteOptions) (: path str) (: sha str) (: versions tuple)] :post [(: % None)]
   :tags {:context "worker" :role "protocol" :reads "json"}}
  "詰めた Program 1 つを coordinator の /programs/<sha> から取り、子の入口が読む形の cache の file path({\"blob\" \"versions\"})に置くため。
   versions = 子の入口が比べる送り手の版(名の順の #(名 版) の組 — service は宣言の行の版・task は task の行の版。path はこの版で
   決まる — launch.spec-program-name)。答えの versions(coordinator の Program の行の版 — 同じ sha を後から置いた送り手の版に
   上書きされる)は使わない(#3762・card ki-172e63fed4c7)。中身の
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
          (<- (file-done (WriteText path (program-file-text (get body "blob") (dict versions)) :replace True)))))
  None)


(defk fetched-programs [state cell options specs]
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions) (: specs tuple)] :post [(: % None)]}
  "宣言の job と task のうち、cache に無い詰めた Program を取り寄せ(改訂 1 の F — service と task で同じ仕組み)、返事から外れた task の
   Program の cache を、今の job と task のどれも参照していなければ消すため(task ごとの印 <id>.program から引く — service の job の
   Program は消さない)。cache の file は (Program のキー・版) ごと — service は宣言の行の版・task は task の行の版(#3762・card
   ki-172e63fed4c7 — 同じ sha でも版が違えば別の file を取る・launch.spec-program-name)。"
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


(defk accepted-supplied [state field]
  {:pre [(: state LinkState) (: field (| list dict str int float bool None))] :post [(: % None)] :tags {:context "worker" :role "protocol" :reads "json"}}
  "heartbeat の返事の supplied(この worker に置かれうる job の参照の綴りの列 — ADR-DOE-CLUSTER-004)を入れ物に写すため。欄の無い返事
   (退いた世代への返事・古い coordinator)は前の値のまま。字の列でない欄は返事の形の誤りとして欄と値の型を挙げて落ちる(拍の except が
   「名乗れない」の 1 行にする — 黙って空に読まない: 空に読むと値の写しを全部消す)。"
  (when (is field None)
    (return None))
  (when (not (and (isinstance field list) (all (gfor item field (isinstance item str)))))
    (raise (ValueError (.format "heartbeat の返事の supplied が字の列でない: {}" (. (type field) __name__)))))
  (setv state.supplied (tuple field))
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


(defk ready-file-written []
  {:pre [] :post [(: % None)]}
  "readinessProbe が sh で読む file(DOEFF_WORKER_READY_FILE — 無ければ書かない)へ、heartbeat の返事が届いた拍ごとに「ready」を書くため
   (mtime = 最後に届いた時刻 — probe は中身が ready で新しい時だけ Ready)。drain 中の返事でも ready を書く: drain は coordinator の配りの状態で
   Pod の健康ではなく、drain は Pod の作り直しをまたいで残るので(#4177)、Ready に混ぜると「Ready を待つ」手順が drain の間ずっと待ち合う。
   環境変数は ReadEnvironment で読む(在る名の分だけ答えが返る — 本物 = 入口の subprocess-handler が os.environ から・sim = 台本の process の
   handler。protocol の層は os.environ に触らない・#3014)。"
  (<- found (get tuple #(EnvEntry ...)) (ReadEnvironment #("DOEFF_WORKER_READY_FILE")))
  (when found
    (<- (file-done (WriteText (. (get found 0) value) "ready\n" :replace True))))
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
  (<- reply RoutedReply (routed-request cell.route "GET" "/watch" options (watch-params after state.name state.boot state.watch.confirmed state.watch-max-ms)
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
  "待ちが「変わった」と答えた時に、拍の間の眠りを起こす呼び鈴を鳴らして手放すため(#2692 — 待っている周が期限を待たずに
   起きて heartbeat を送る)。1 回の眠りの間に変化が何度来ても鳴るのは掛かっていた 1 つだけ(次の宣言の読みが新しく掛ける)。"
  (val bell watch.bell)
  (when (is-not bell None)
    (setv watch.bell None)
    (<- (CompletePromise bell True)))
  None)


(defk armed-bell [state]
  {:pre [(: state LinkState)] :post [(: % (| Promise None))]}
  "拍の間の眠りを宣言の変化で起こす呼び鈴を返すため(#2692)。待ちの口を確かめる前から掛ける — 周の間は期限か呼び鈴でしか起きない
   (#3871 の単位 4)ので、起動の直後の最初の「変わった」をこの呼び鈴で受けないと、次の heartbeat の期限まで気づかない。待ちの口の無い
   coordinator では鳴らないだけ(heartbeat の期限で起きる)。待ちを使わない口(watch-enabled が偽)だけ None。まだ鳴っていない呼び鈴が
   在ればそれを渡し(拍ごとに作らない)、無ければ新しく掛ける。"
  (val watch state.watch)
  (cond
    (not state.watch-enabled) None
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
  "heartbeat を 1 回送り、返事の job・task・温める表を desired にするため。届かなければ desired-when-unreachable(fence の判断)。
   呼ぶのは送り口 sent-beat だけ(送りを 1 本ずつにし、送りの事実を溜める — card ki-e38dbfca7671)。"
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
                                :stopping stopping :overlaps-elder state.overlaps-elder :doeff-commit state.doeff-commit
                                :pools state.pools :memory-mib state.memory-mib))
  (val body (| base
               (if (or (not state.handles-envs) (is state.env-report None))
                   {}
                   (env-heartbeat-part state.env-report (current-platform)))
               ;; token の状態(card ki-d31166f84ad0)は読めた worker だけが名乗る(欄の無い本文は coordinator が ok と読む)。
               (if (is state.account-token None) {} {"accountToken" state.account-token.value})
               ;; 枠の file の状態(card ki-6828eda880ce)は file を読む worker だけが名乗る(欄の無い本文は coordinator が ok と読む)。
               (if (is state.room-state None) {} {"roomFile" state.room-state.value})))
  ;; 生存の合図の thread が送り直す宛先と本文(card ki-9b58446e2ad6 — 組を 1 つの欄で入れ替え、thread が混ざった組を読まない)。
  (setv state.life.post #((+ endpoint "/heartbeat") (.encode (json.dumps body :ensure-ascii False) "utf-8")))
  (<- reply RoutedReply (routed-request cell.route "POST" "/heartbeat" options None body))
  (setv cell.route reply.route)
  (<- now-ms int (now-epoch-ms))
  ;; 返事の刻は壁の時計と単調な時計の両方で読む(沈黙の計りの起点と今 — heartbeat_rules.silent-ms)。
  (<- now-mono float (GetMonotonic))
  (var desired None)
  (try
    (<- answered (answer-json reply.answer))
    (setv state.last-ok-ms now-ms state.last-ok-mono-ms (monotonic-ms-of now-mono))
    ;; 返事の宣言の部分(job の行と draining)を JSON の境界で 1 度だけ解く(#3684)— 返事の job の版を据え置く印(declared-job-specs)が
    ;; draining を読む。ready の file は解けた返事ごとに ready を書く(drain を見ない・#4177)。形の違う返事(draining の無い返事など)は
    ;; DeclaredReplyMalformed で落ち、下の except が「名乗れない」の 1 行にして「読めない」を返す(拍は前の宣言のまま動かし、ready の file は
    ;; 書かない — 読めない返事で版を入れ替えない)。
    (<- declared DeclaredReply (declared-reply-of-json answered))
    ;; job の行も ready の前に読む — 行の形の誤り(古い coordinator の返事の版の欄の無い Program の job など・card ki-01d1f8cb0391)も
    ;; 読めない返事で、ready を書かない。
    (<- jobs tuple (declared-job-specs declared))
    ;; 同じ名の前の世代がまだ動かしている job を、この世代がまだ受け取れない間(handoverPending — AL-D2k)は ready を書かない:
    ;; Pod が Ready になると Deployment が前の世代の Pod の削除を始め、Service がその Pod を振り先から外す。
    (when (not declared.handover-pending)
      (<- (ready-file-written)))
    ;; 自己停止の時間は coordinator の ClusterTiming が持つ(移し替えの時間と組で決まる)。受け取った値に合わせる。
    (val timing (.get answered "timing"))
    (when (and timing (in "fence_ms" timing))
      (setv state.fence-ms (int (get timing "fence_ms"))))
    ;; 印の在る job の長い方の柵(#2804)も同じく受け取る。
    (when (and timing (in "keep_fence_ms" timing))
      (setv state.keep-fence-ms (int (get timing "keep_fence_ms"))))
    ;; 名指しの待ちの上限も同じく受け取る(#3865 — coordinator の頭打ちと同じ値で問う)。
    (when (and timing (in "watch_max_ms" timing))
      (setv state.watch-max-ms (int (get timing "watch_max_ms"))))
    ;; 生存の窓も同じく受け取る(次に送る heartbeat の事実に載せる — #3850)。
    (when (and timing (in "lease_ms" timing))
      (setv state.lease-ms (int (get timing "lease_ms"))))
    (setv state.last-jobs jobs)
    (<- tasks tuple (accepted-tasks state (.get answered "tasks" [])))
    (setv state.last-tasks tasks)
    (<- (fetched-programs state cell options (+ state.last-jobs state.last-tasks)))
    (<- warm tuple (accepted-warm state (.get answered "warm" [])))
    (setv state.last-warm warm)
    (<- (accepted-supplied state (.get answered "supplied")))
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
          ;; 退いた世代の印(返事の superseded)は Pod の job の label の答え手が読む(card ki-a66bb71f4152)。
          state.last-desired (DesiredJobs (+ state.last-jobs state.last-tasks) :warm state.last-warm :superseded declared.superseded))
    (setv state.watch.after (reply-revision answered))
    (setv state.watch.beats (+ state.watch.beats 1))
    (:= desired state.last-desired)
    (except [error Exception]
      (<- (told-once state (repr error) (+ "worker: coordinator に名乗れない: " (repr error))))
      ;; 届かない間は毎拍送り直す(前の desired を使い続けない — fence の判断を毎拍する)。
      (setv state.last-desired None)
      (<- silent int (heard-silence state now-ms (monotonic-ms-of now-mono)))
      (:= desired (desired-when-unreachable silent state.fence-ms state.keep-fence-ms
                                            (+ state.last-jobs state.last-tasks) state.last-warm (repr error)))))
  desired)


(defk send-settled [state]
  {:pre [(: state LinkState)] :post [(: % None)]}
  "答えを待っている heartbeat の送りが在れば、その答えまで待つため(送りは 1 本ずつ — card ki-e38dbfca7671)。"
  (while (is-not state.beating.in-flight None)
    (<- (Wait (. state.beating.in-flight future))))
  None)


(defk sent-beat [state cell options]
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions)] :post [(: % (| DesiredJobs DesiredUnreadable))]}
  "heartbeat を 1 本送るため — 拍の宣言の読み(polled)と拍の外の背景の task(beat-beside-loop)が通る 1 つの送り口(card ki-e38dbfca7671)。
   送りは 1 本ずつ: 答えを待っている送りが在れば、その答えを待ってから送る。送りの事実(HeartbeatSent — 送りを始めた刻・名乗る名・この時に
   知っている生存の窓 = 返事が上書きする前の値)は、拍の宣言の読みが次に受け取るまで溜める(#3850 — 届かず返事が読めない送りも数える)。"
  (<- (send-settled state))
  (val beating state.beating)
  (<- now-ms int (now-epoch-ms))
  ;; 間の長さの計り用の単調な時計の読み(#3825 — at は遅れの行に書く壁の時計の刻・間は mono-ms の差で計る)。
  (<- mono float (GetMonotonic))
  (<- done Promise (CreatePromise))
  (setv beating.in-flight done
        beating.started-at now-ms
        beating.started-mono-ms (monotonic-ms-of mono)
        beating.unread (+ beating.unread #((HeartbeatSent :at now-ms :mono-ms (monotonic-ms-of mono) :worker state.name
                                                         :lease-ms state.lease-ms))))
  (var beaten None)
  (try
    (<- answered (| DesiredJobs DesiredUnreadable) (beat state cell options))
    (:= beaten answered)
    (except [error Exception]
      ;; 思わぬ例外でも送りの印を下ろし、待っている求めを置き去りにしない(例外はそのまま呼び手へ)。
      (setv beating.in-flight None)
      (<- (CompletePromise done False))
      (raise)))
  ;; 印を下ろしてから約束を満たす(起きた求めが、まだ送っていると読まない)。
  (setv beating.in-flight None)
  (<- (CompletePromise done True))
  beaten)


(defk beat-beside-loop [state cell options]
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions)] :post [(: % None)]}
  "拍の外の背景の task の本体(card ki-e38dbfca7671): 前の送りを始めてから送信間隔が過ぎたら、拍を待たずに heartbeat を送るため。拍が
   送れば、その送りから数え直す(送りは送り口 sent-beat の 1 本ずつ — 拍の送りと重ねない)。拍が HTTP や待ちで譲っている間は、拍が
   どれだけ長くかかっても送りの間は送信間隔と 1 往復の内に収まる(同期の処理で scheduler を塞ぐ間は回らない)。返事は拍と同じ入れ物に
   書く(次の拍の宣言の読みが使う)。思わぬ例外は理由を記して抜ける(拍の中の送りは続く — 黙って止まらない)。前の送りからの長さは
   沈黙と同じ計り(heartbeat_rules.silent-ms — 壁の時計が戻っても次の送りを戻った分だけ待たない・card ki-069f4a5c07f7)。"
  (val beating state.beating)
  (try
    (while (not beating.closing)
      (<- (send-settled state))
      (<- now-ms int (now-epoch-ms))
      (<- mono float (GetMonotonic))
      ;; 前の送りを始めてからの長さ(まだ送っていなければ、間隔が過ぎたのと同じ = 今すぐ送る)。
      (var since state.beat-interval-ms)
      (when (is-not beating.started-at None)
        (<- measured int (silent-ms now-ms (monotonic-ms-of mono) beating.started-at beating.started-mono-ms))
        (:= since measured))
      (if (< since state.beat-interval-ms)
          (<- (Delay (/ (- state.beat-interval-ms since) 1000.0)))
          (<- _beaten (| DesiredJobs DesiredUnreadable) (sent-beat state cell options))))
    (except [error Exception]
      (setv beating.failure (repr error))
      (<- (slog (+ "worker: 拍の外の heartbeat の task が止まりました — 拍の中の送りだけになります: " (repr error))))))
  (setv beating.running False)
  None)


(defk lifeline-turn [state]
  {:pre [(: state LinkState)] :post [(: % (| tuple None))]}
  "生存の合図の thread の 1 周で、heartbeat を送るかを決めるため(card ki-9b58446e2ad6 — thread の VM で走る。scheduler の効果は出さず、
   時計の読みだけ)。拍の側の送り(拍・拍の外の背景の task)と thread の送りの新しい方の始めから送信間隔 × LIFELINE-SLACK-RATIO が
   過ぎ、拍の進みが止まりの上限の内なら、送りの事実(HeartbeatSent)を溜め、送りの始めの刻を書いて、拍が最後に送った
   #(URL 本文の bytes) を返す。送らない周は None。拍がまだ 1 度も送っていない・宣言をまだ読みに来ていない間は送らない。"
  (val life state.life)
  (val beating state.beating)
  (val post life.post)
  (<- now-ms int (now-epoch-ms))
  (<- mono float (GetMonotonic))
  (val now-mono-ms (monotonic-ms-of mono))
  (if (or (is post None) (is life.progress-at None))
      None
      (do
        ;; 前の送りの始めからの長さ: 拍の側と thread の新しい方(まだ送っていない側は数えない)。
        (var since-sent None)
        (when (is-not beating.started-at None)
          (<- by-tick int (silent-ms now-ms now-mono-ms beating.started-at beating.started-mono-ms))
          (:= since-sent by-tick))
        (when (is-not life.started-at None)
          (<- by-lifeline int (silent-ms now-ms now-mono-ms life.started-at life.started-mono-ms))
          (:= since-sent (if (is since-sent None) by-lifeline (min since-sent by-lifeline))))
        (<- stalled int (silent-ms now-ms now-mono-ms life.progress-at life.progress-mono-ms))
        (if (lifeline-due True (if (is since-sent None) stalled since-sent) state.beat-interval-ms stalled)
            (do (setv life.started-at now-ms life.started-mono-ms now-mono-ms)
                ;; 組を 1 度だけ読み、1 度に入れ替える(書くのはこの thread だけ)。
                (val before life.sent)
                (val sent (HeartbeatSent :at now-ms :mono-ms now-mono-ms :worker state.name :lease-ms state.lease-ms))
                (setv life.sent #((+ (get before 0) 1) (cut (+ (get before 1) #(sent)) (- LIFELINE-SENT-KEPT) None)))
                post)
            None))))


(defk lifeline-settled [state status detail]
  {:pre [(: state LinkState) (: status (| int None)) (: detail str)] :post [(: % None)]}
  "生存の合図の thread の送り 1 本の答えを、拍の側が読む値に書くため(card ki-9b58446e2ad6 — thread の VM で走る)。status = 返事の
   HTTP の status(None = 届かなかった)・detail = 届かない・断られた理由。2xx なら返事の刻の対を書き(沈黙の計りに入る —
   heard-silence)、失敗の理由を空にする。それ以外は理由を書く(拍の口が変わり目を 1 行で出す — lifeline-told)。返事の本文は読まない
   (宣言の読みは拍の送りだけ — 頭の註)。"
  (val life state.life)
  (if (and (is-not status None) (<= 200 status 299))
      (do (<- now-ms int (now-epoch-ms))
          (<- mono float (GetMonotonic))
          (setv life.last-ok-ms now-ms life.last-ok-mono-ms (monotonic-ms-of mono))
          (setv life.failure ""))
      (setv life.failure (if (is status None) detail (.format "{}: {}" status detail))))
  None)


(defk progress-noted [state]
  {:pre [(: state LinkState)] :post [(: % None)]}
  "拍が宣言を読みに来た刻の対を、拍の進みの印として生存の合図の thread に見せるため(card ki-9b58446e2ad6 — 印が
   LIFELINE-STALL-LIMIT-MS を越えて古いと、thread は固まったループの代わりに送り続けない)。"
  (<- now-ms int (now-epoch-ms))
  (<- mono float (GetMonotonic))
  (setv state.life.progress-at now-ms state.life.progress-mono-ms (monotonic-ms-of mono))
  None)


(defk token-noted [state]
  {:pre [(: state LinkState)] :post [(: % None)]}
  "ServiceAccount の token の file を READ-EVERY-MS ごとに読み、heartbeat で名乗る状態(state.account-token)を更新し、状態が変わった
   時に 1 行出すため(card ki-d31166f84ad0 — kubelet が置き直しを取り逃した worker を、401 が続く前に log と見回りが知る)。file の
   読めない worker(Pod の外)は名乗らない。"
  (when (is-not state.token-file None)
    (<- now-ms int (now-epoch-ms))
    (<- due bool (token-read-due state.token-read-ms now-ms))
    (when due
      (setv state.token-read-ms now-ms)
      (<- text (ReadText state.token-file))
      (var claims None)
      (var named None)
      (when (isinstance text str)
        (<- read (| TokenClaims None) (token-claims text))
        (<- judged TokenState (token-state read now-ms))
        (:= claims read)
        (:= named judged))
      (when (!= named state.account-token)
        (<- line str (token-line state.account-token named claims now-ms (if (isinstance text FileFailed) text.detail "")))
        (<- (slog line)))
      (setv state.account-token named)))
  None)


(defk room-noted [state]
  {:pre [(: state LinkState)] :post [(: % None)]}
  "枠の file を READ-EVERY-MS ごとに読み直し、次の heartbeat で名乗る capacity・task-reserve・置ける組の印・memory の予算を file の
   自分の行の値にするため(card ki-6828eda880ce・ki-ced3c45980a5 — 枠と組を変えても Pod を作り直さない)。file を読めない・形が崩れている時は前に名乗った値のままにし、状態を
   heartbeat の roomFile で名乗る。値が変わった時・読めなくなった時(訳が変わった時)・戻った時に log へ 1 行出す。file を読まない
   worker(room-file が None)は何もしない。"
  (when (is-not state.room-file None)
    (<- now-ms int (now-epoch-ms))
    (<- due bool (room-read-due state.room-read-ms now-ms))
    (when due
      (setv state.room-read-ms now-ms)
      (<- text (ReadText state.room-file))
      (var reading (RoomReading :state RoomState.UNREADABLE :room None
                                :reason (if (isinstance text FileFailed) text.detail "")))
      (when (isinstance text str)
        (<- read RoomReading (room-of-text text state.name))
        (:= reading read))
      (val before (WorkerRoom :capacity state.capacity :task-reserve state.task-reserve :pools state.pools :memory-mib state.memory-mib))
      (val after (if (is reading.room None) before reading.room))
      (when (or (!= after before) (!= reading.state state.room-state) (!= reading.reason state.room-reason))
        (<- line str (room-line before after reading))
        (<- (slog line :level (if (= reading.state RoomState.OK) "info" "warning"))))
      (setv state.capacity after.capacity state.task-reserve after.task-reserve state.pools after.pools state.memory-mib after.memory-mib
            state.room-state reading.state state.room-reason reading.reason)))
  None)


(defk token-line [before after claims now-ms failure]
  {:pre [(: before (| TokenState None)) (: after (| TokenState None)) (: claims (| TokenClaims None)) (: now-ms int) (: failure str)]
   :post [(: % str)]}
  "token の状態の移りを、worker の log の 1 行にするため(発行からの分と期限までの分を添える — 読めない時は理由)。"
  (val said (fn [s] (if (is s None) "名乗らない" s.value)))
  (val detail (cond
                (is-not claims None)
                  (.format "発行から {} 分・期限まで {} 分" (// (- now-ms (* claims.issued-at 1000)) 60000)
                           (// (- (* claims.expires-at 1000) now-ms) 60000))
                failure (+ "読めない: " failure)
                True "JWT の形でない"))
  (.format "worker: ServiceAccount の token {} → {}({})" (said before) (said after) detail))


(defk lifeline-told [state sends]
  {:pre [(: state LinkState) (: sends tuple)] :post [(: % None)]}
  "生存の合図の thread の働きを拍の log に出すため(card ki-9b58446e2ad6): 前の読みの後に thread が送った本数(拍の側が送れなかった
   間の代わり — sends = 受け取った thread の送りの事実)を 1 行、thread の送りの失敗の理由の変わり目(失敗した・理由が変わった・
   戻った)を 1 行。thread は scheduler の外で走り、log の効果を出せないので、拍の口が代わりに出す。"
  (val life state.life)
  (when sends
    (<- (slog "worker: 生存の合図の thread が拍の代わりに heartbeat を送りました" :level "info" :count (len sends)
              :first (.isoformat (datetime-of-epoch-ms (. (get sends 0) at)))
              :last (.isoformat (datetime-of-epoch-ms (. (get sends -1) at))))))
  (val failure life.failure)
  (when (!= failure life.told)
    (setv life.told failure)
    (<- (slog (if failure
                  (+ "worker: 生存の合図の thread が heartbeat を届けられない: " failure)
                  "worker: 生存の合図の thread の heartbeat が届くようになりました"))))
  None)


(defk polled [state cell options watch-cell]
  {:pre [(: state LinkState) (: cell RouteCell) (: options RouteOptions) (: watch-cell RouteCell)]
   :post [(: % (| DesiredJobs DesiredUnreadable))]}
  "拍ごとの ReadDesired に答えるため: heartbeat を送る拍(beat_policy.heartbeat-due)なら送り、それ以外は前の返事の desired を返す。
   返事に版を持つ coordinator へは、名指しの待ちの背景の task を 1 度だけ起こす(待ちを使う口だけ)。拍の外の送りの背景の task も
   1 度だけ起こす(beat-beside-enabled の口だけ — card ki-e38dbfca7671)。"
  ;; 拍の外の送りが答えを待っている間に来た読みは、その答えを待ってから判じる(同じ間隔の送りを重ねない — card ki-e38dbfca7671)。
  (<- (send-settled state))
  (<- now-ms int (now-epoch-ms))
  ;; 自己停止を周期ごとに時間で判じる(#2806): 処理が止まって heartbeat を送れなかった worker は、戻った最初の周期で最後の成功から fence を
  ;; 越えていれば、この周期は heartbeat を送らず印の無い job と task を止めた宣言を返す(返事を待つ間に動かし続けない)。最後の宣言を捨てる
  ;; ので、次の周期は heartbeat を送る — 届けば返事で戻し、届かなければ desired-when-unreachable が判じる。
  ;; 沈黙は壁の時計と単調な時計の長い方(機体の suspend は壁の差が、壁の時計の戻りは単調な差が拾う — card ki-069f4a5c07f7)。
  (<- silent int (link-silence state now-ms))
  (val silenced (desired-after-silence silent state.fence-ms state.keep-fence-ms (is-not state.last-desired None)
                                       (+ state.last-jobs state.last-tasks) state.last-warm))
  (when (is-not silenced None)
    (setv state.last-desired None)
    ;; 自分で止める判断を 1 行で名指す(card ki-9b58446e2ad6 — 止めた job の行の訳 cut-off だけでは、拍の止まりの後の自己停止か
    ;; 届かない途絶かを読み分けられない)。
    (<- (slog "worker: coordinator からの返事が途絶の柵を越えて無い — 印の無い job と task を止めます" :level "warning"
              :silent-ms silent :fence-ms state.fence-ms))
    (return silenced))
  (<- watching bool (watching? state))
  (var desired state.last-desired)
  ;; 止まり始めは状態の報告の違いと同じく、送る間隔を待たずに名乗る(#2819)。
  (when (heartbeat-due watching (is-not state.last-desired None) state.watch.woken
                       (or (!= state.statuses state.sent-statuses) (!= state.stopping state.sent-stopping))
                       silent state.beat-interval-ms)
    ;; 送りの事実(#3850)は送り口が溜め、宣言の読みが渡す(拍の外の送りと同じ数え方)。
    (<- beaten (| DesiredJobs DesiredUnreadable) (sent-beat state cell options))
    (:= desired beaten))
  (val watch state.watch)
  (when (and state.watch-enabled (not watch.running) (not watch.unsupported) (not watch.closing) (not watch.failure)
             (is-not watch.after None))
    (setv watch.running True)
    (<- (Spawn (watch-loop state watch-cell options) :daemon True)))
  (val beating state.beating)
  (when (and state.beat-beside-enabled (not beating.running) (not beating.closing) (not beating.failure))
    (setv beating.running True)
    (<- (Spawn (beat-beside-loop state cell options) :daemon True)))
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
    ;; 拍の進みの印(生存の合図の thread が、固まったループの代わりに送り続けない — card ki-9b58446e2ad6)。
    (<- (progress-noted state))
    ;; ServiceAccount の token の古さを読み直す(READ-EVERY-MS ごと — 次の heartbeat が名乗る・card ki-d31166f84ad0)。
    (<- (token-noted state))
    ;; 枠の file を読み直す(READ-EVERY-MS ごと — 次の heartbeat が名乗る・card ki-6828eda880ce)。
    (<- (room-noted state))
    ;; 拍の間の眠りを起こす呼び鈴は heartbeat の前に掛ける(送っている間に来た変化も鳴らす — #2692)。
    (<- bell (| Promise None) (armed-bell state))
    (<- desired (polled state cell options watch-cell))
    (<- belled (| DesiredJobs DesiredUnreadable) (with-bell desired bell))
    ;; 前の読みの後からの送りの事実を渡して空にする(拍の外の送りも — 拍の Program が送りの間を測る・#3850・card ki-e38dbfca7671)。
    ;; 生存の合図の thread の送りも足し、送った順に並べる(card ki-9b58446e2ad6)。
    ;; 組を 1 度だけ読む(thread が間に入れ替えても、読んだ組の中は揃っている)。
    (val sent-so-far state.life.sent)
    (val total (get sent-so-far 0))
    (val recent (get sent-so-far 1))
    (val fresh (min (- total state.life.read-total) (len recent)))
    (val by-lifeline (if (> fresh 0) (tuple (cut recent (- fresh) None)) #()))
    (setv state.life.read-total total)
    (<- (lifeline-told state by-lifeline))
    (val sends (tuple (sorted (+ state.beating.unread by-lifeline) :key (fn [sent] sent.mono-ms))))
    (setv state.beating.unread #())
    (resume (replace belled :sends sends)))
  (WorkerWakes [began began-mono-ms]
    ;; 周の間の待ちを起こす物(#3871 の単位 4): 外側の答えに、heartbeat を送る期限・途絶の柵の期限と、前の heartbeat が届いていなければ
    ;; 送り直しの刻を足す。宣言の変化の呼び鈴は ReadDesired の答え(DesiredJobs.changed)が運ぶので足さない。
    (<- outer WakeSet effect)
    ;; 期限は壁の時計の刻(DueAt)で返す。最後に届いた返事と tick の頭を、今の壁の時計の枠へ写してから組み立てる: 今の壁の時計から、
    ;; その刻からの長さ(heartbeat_rules.silent-ms — 壁の時計と単調な時計の長い方)を引いた刻。壁の時計が動いていなければ元の刻と
    ;; 同じで、tick の途中や前の周の間に壁の時計が戻っていても、期限が戻った分だけ先へ延びない(card ki-069f4a5c07f7)。
    (<- now int (now-epoch-ms))
    (<- mono float (GetMonotonic))
    (<- silent int (heard-silence state now (monotonic-ms-of mono)))
    (<- ticking int (silent-ms now (monotonic-ms-of mono) began began-mono-ms))
    (val last-ok (- now silent))
    (val tick-began (- now ticking))
    ;; heartbeat と fence の期限は tick の頭の時刻を基準に組み立てる: 送るか・止めるかの判断(polled)は tick の中の ReadDesired で
    ;; 読んだ時刻で判定したので、tick の頭より後の期限はまだ判定していない。tick の後半が送信間隔より長くて既に過ぎていても捨てずに
    ;; 返し、待ち 0 で起きた次の tick が送る(#4332)。
    (<- beat (| DueAt DueNever) (beat-due tick-began last-ok state.beat-interval-ms))
    (<- fence (| DueAt DueNever) (fence-due tick-began last-ok state.fence-ms state.keep-fence-ms))
    ;; 時間で取り直す理由: 届かない coordinator の戻りを知る試し(届いた後は heartbeat の期限だけ)。再送は期限でなく再送の間の待ちなので、
    ;; tick の終わりの時刻から数える。
    (val resend (if (is state.last-desired None) (DueAt :at (+ now RESEND-AFTER-MS)) (DueNever)))
    (<- due (| DueAt DueNow DueNever) (earliest-due #(beat fence resend)))
    (<- merged WakeSet (wakes-with outer due #() #()))
    (resume merged))
  (PublishStatus [statuses note]
    ;; 状態は次の heartbeat で送る。file にも書くので、同じ効果を外側の status-file へ回す。
    (<- rows list (status-rows state statuses))
    (setv state.statuses rows)
    (reperform effect)))
