;; coordinator への口の沈黙の長さ(最後に届いた返事からの ms)が、機体の壁の時計が動いても実際に過ぎた時間を下回らない事の検
;; (card ki-069f4a5c07f7 — #3825・ki-3d02b3888acb の計りの弱さの、判断の側)。
;;
;; 実例の形: 途絶の柵(自己停止)と heartbeat を送る間隔は、沈黙の長さを壁の時計の差(今 − 最後に届いた返事の刻)で判じていた。
;;   - 途絶の最中に壁の時計が Δ 戻ると、沈黙が Δ 縮み、自己停止が Δ 遅れる。coordinator は自分の時計で移し替え(reassign-after-ms 60 秒 =
;;     fence 20 + 止め切りの内訳 32 + 余白 8)を数えるので、Δ が余白 8 秒を越えると条 C4(移し替えは worker の止め切りより後)が崩れ、Δ が
;;     40 秒を越えると、worker が止めると決める前に移し替えの刻が来る(同じ job が 2 か所で走る)。
;;   - 途絶が無くても、届いた直後に壁の時計が Δ 戻ると、次の heartbeat の送りが Δ 遅れる(拍の送りも拍の外の送りも、前の刻 + 間隔を
;;     壁の時計で待つ)。Δ が移し替えの 60 秒を越えると、届いている worker の job を coordinator が他へ移す。
;; 沈黙の長さは max(単調な時計の差, 壁の時計の差) で計る(早く止まる向きにだけ寄せる):
;;   - 壁が戻る   → 単調な差が実際の長さを守る。
;;   - 機体の suspend → 単調な時計(CLOCK_MONOTONIC)は眠っている間を数えない。壁の差が拾う。
;;   - 壁が進む   → 沈黙が進んだ分だけ伸び、早く止まる(suspend と同じ読みで見分けられない — 2 か所で走らない向き)。
;; 台本: 本物の調整ループ(run-worker)・本物の coordinator-link(GET /watch を使う本番の形)・本番の周の間の待ち(tick-pauses)を仮想の
;; 時計の下で回す。偽の coordinator は自分の時計(単調な時計 — worker の壁の時計と別の機体)で途絶の始まりを決め、壁の時計は台本の
;; 出来事(届かなかった最初の heartbeat・N 回目の heartbeat の後の観測・ある刻より後の最初の周の間の待ち)で動かす(effect
;; StepWallClock — 手前の handler が GetTime の答えだけをずらす。動かす契機を時刻の読みの回数や経過秒にしない)。
;;   - 周の間の待ち(tick_pauses)も同じ計りで待つ: 起きる期限(DueAt — 壁の時計の枠の刻)を組み立てた後・待ちが時計を読む前に壁の
;;     時計が Δ 戻ると、「期限 − 今の壁の時計」で待つ形は待ちが Δ 延び、柵の判断が Δ 遅れた(設計検証の盲検 A の失敗例 — 途絶の
;;     沈黙 19.5 秒の待ちの直前に 45 秒戻すと、自己停止を決めるのが沈黙 65 秒の時)。待ちは周の頭の刻の対からの経過で数える。
;;   - 調整ループが起きる期限の基準にする拍の頭の刻も同じ計りで導く: 頭で壁の時計と単調な時計の対を読み、判断の時の壁の時計の枠へ
;;     写す。単調な差だけで導くと、拍の途中(heartbeat の返事の処理の間)に機体が眠った時、拍の頭が眠りの後へずれ、眠りの間に過ぎた
;;     heartbeat と柵の期限を「この拍がもう判じた」として捨てた(設計検証の盲検 A の副次の観測 — 拍だけが heartbeat を送る口で、
;;     自己停止を決めるのが眠りの 210 秒後)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import datetime [datetime timedelta])
(import json)
(import doeff [with-handlers])
(import doeff_core_effects [slog-handler state])
(import doeff_core_effects [os-file-handler])
(import doeff_core_effects [ReadEnvironment])
(import doeff_core_effects [HttpRequest HttpResponse HttpFailed HttpFailureKind])
(import doeff_core_effects [StopRequested])
(import doeff_time [Delay GetMonotonic GetTimeEffect SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [monotonic-ms-of])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.worker.intent.worker_model [WorldView WorkerPolicy WorkerState DesiredJobs ReadDesired ObserveWorld EnvReport
                                                 PublishStatus AwaitNextTick])
(import doeff_cluster.worker.core.heartbeat_rules [silent-ms])
(import doeff_cluster.worker.core.program [run-worker])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import doeff_cluster.worker.protocol.worker_wakes [no-wakes])
(import doeff_cluster.worker.protocol.coordinator_link [LinkState coordinator-link RESEND-AFTER-MS])
(import tests.link_rig [LINK-ROUTE])
(import tests.stop_fixtures [stop-signal-never-comes])
(import tests.transport_http [route-cell])

(val POLICY (WorkerPolicy))
(val WORKER "agent-worker-2")
;; 本番と同じ時間の設定(lease 10 秒・fence 20 秒・移し替え 60 秒)。
(val T (ClusterTiming))
(val REVISION 3)
;; 条 C4 の余白(shared/intent/protocol の ClusterTiming の註: 移し替え 60 秒 = fence 20 + 返事の上限 15 + 接続の上限 2 + 停止の猶予 15 + 余白 8)。
(val C4-MARGIN-MS 8000)


(defrecord Script
  "台本 1 つ。cut-ms = 偽の coordinator が届かなくなる刻(台本の始まりからの ms — coordinator の機体の時計。None = 途絶えない)・
   cut-back-seconds = 届かなかった最初の heartbeat の時に worker の壁の時計を戻す秒(負 = 進める・0 = 動かさない)・
   step-after-beats = この数の heartbeat が届いた後の最初の観測(ObserveWorld)で壁の時計を動かす(None = 動かさない)・
   beats-back-seconds = その時に戻す秒(負 = 進める)・until-ms = 調整ループを止める刻(台本の始まりからの ms)。"
  (#^ (| int None) cut-ms)
  (#^ float cut-back-seconds)
  (#^ (| int None) step-after-beats)
  (#^ float beats-back-seconds)
  (#^ int until-ms))


(defrecord Seen
  "台本 1 つの読み(刻はどれも台本の始まりからの ms — 単調な時計)。delivered = 偽の coordinator が答えた heartbeat の刻の列・
   stepped-ms = 壁の時計を動かした刻(None = 動かしていない)・cut-off-ms = worker の宣言の読みが最初に途絶の絞り(CutOff)を返した刻
   (None = 返していない — 自己停止を決めていない)・beats-at-cut-off = その時までに届いていた heartbeat の数。"
  (#^ (get tuple #(int ...)) delivered)
  (#^ (| int None) stepped-ms)
  (#^ (| int None) cut-off-ms)
  (#^ (| int None) beats-at-cut-off))


(defeffect StepWallClock
  "壁の時計だけを seconds 戻す(負 = 進める)— 検だけの操作。GetMonotonic は動かさない。"
  {:fields [(: seconds float)] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect ScriptClockMs
  "台本の始まりからの ms(単調な時計)を読む — 検だけの問い。"
  {:fields [] :answer int :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NoteCutOff
  "worker の宣言の読みが途絶の絞りを返した事を偽の coordinator の記録に残す — 検だけの操作。"
  {:fields [] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect ReadSeen
  "台本の読みを問う — 検だけの問い。"
  {:fields [] :answer Seen :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler wall-clock-stepped
  "worker の機体の壁の時計の代役: StepWallClock の分だけ GetTime の答えをずらす(壁の時計だけ動く — GetMonotonic は通す)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var back-seconds 0.0)
  (StepWallClock [seconds]
    (:= back-seconds (+ back-seconds seconds))
    (resume None))
  (GetTimeEffect []
    (<- now datetime effect)
    (resume (- now (timedelta :seconds back-seconds)))))


(defhandler script-clock
  "台本の時計: 最初の読みを 0 として、単調な時計の ms を答える(偽の coordinator の機体の時計 — worker の壁の時計と別)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var zero None)
  (ScriptClockMs []
    (<- mono float (GetMonotonic))
    (val at (monotonic-ms-of mono))
    (when (is zero None)
      (:= zero at))
    (resume (- at zero))))


(defk answered [url body]
  {:pre [(: url str) (: body dict)] :post [(: % HttpResponse)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "偽の coordinator の 200 の応答を作るため。"
  (val text (json.dumps body))
  (HttpResponse 200 {} (.encode text "utf-8") text url 0.0))


(defhandler scripted-coordinator [#^ Script script]
  "偽の coordinator と、worker の外側の偽の応答。台本の cut-ms より前は heartbeat に空の宣言・timing(本番の値)・版を答えて刻を記録し、
   GET /watch は問われた秒だけ待って「変わっていない」と答える。cut-ms 以後はどちらも接続の失敗。届かなかった最初の heartbeat の時と、
   step-after-beats 回の heartbeat の後の最初の観測で、壁の時計を台本の分動かす。StopRequested は until-ms 以後に止めの理由を答える。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに台本を変える(Ask で区別できない)。
  (session var delivered #())
  (session var stepped-ms None)
  (session var cut-off-ms None)
  (session var beats-at-cut-off None)
  (HttpRequest [url params]
    (<- at int (ScriptClockMs))
    (val cut (and (is-not script.cut-ms None) (>= at script.cut-ms)))
    (match #((get (.rsplit url "/" 1) 1) cut)
      #("heartbeat" False)
        (do (:= delivered (+ delivered #(at)))
            (<- reply HttpResponse (answered url {"jobs" [] "tasks" [] "warm" [] "draining" False "revision" REVISION
                                                  "timing" {"lease_ms" T.lease-ms "fence_ms" T.fence-ms}}))
            (resume reply))
      #("heartbeat" True)
        (do (when (and (is stepped-ms None) (!= script.cut-back-seconds 0.0))
              (:= stepped-ms at)
              (<- (StepWallClock script.cut-back-seconds)))
            (resume (HttpFailed :url url :detail "ConnectError: refused" :kind HttpFailureKind.CONNECT-FAILED)))
      #(_ False)
        (do (<- (Delay (float (get params "timeoutSeconds"))))
            (<- reply HttpResponse (answered url {"revision" REVISION "changed" False}))
            (resume reply))
      _
        (resume (HttpFailed :url url :detail "ConnectError: refused" :kind HttpFailureKind.CONNECT-FAILED))))
  (ReadEnvironment [names]
    (resume #()))
  (EnvReport []
    (resume None))
  (ObserveWorld []
    (when (and (is stepped-ms None) (is-not script.step-after-beats None) (>= (len delivered) script.step-after-beats))
      (<- at int (ScriptClockMs))
      (:= stepped-ms at)
      (<- (StepWallClock script.beats-back-seconds)))
    (resume (WorldView #() #())))
  (PublishStatus [statuses note]
    (resume None))
  (StopRequested []
    (<- at int (ScriptClockMs))
    (resume (match (>= at script.until-ms)
              True "signal 15"
              _ None)))
  (NoteCutOff []
    (when (is cut-off-ms None)
      (<- at int (ScriptClockMs))
      (:= cut-off-ms at)
      (:= beats-at-cut-off (len delivered)))
    (resume None))
  (ReadSeen []
    (resume (Seen :delivered delivered :stepped-ms stepped-ms :cut-off-ms cut-off-ms :beats-at-cut-off beats-at-cut-off))))


(defhandler cut-off-noted
  "coordinator への口の手前(調整ループの側)に立つ代役: 宣言の読みの答えが途絶の絞り(CutOff)なら、その刻を記録に残して通す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (ReadDesired [env-report stopping]
    (<- read effect)
    (when (and (isinstance read DesiredJobs) (is-not read.cut-off None))
      (<- (NoteCutOff)))
    (resume read)))


(defk worker-then-seen []
  {:pre [] :post [(: % Seen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の調整ループを止めの刻まで動かし、台本の読みを返すため。"
  (<- _zero int (ScriptClockMs))
  (<- _state WorkerState (run-worker POLICY))
  (<- seen Seen (ReadSeen))
  seen)


(defk seen-on [script task-dir]
  {:pre [(: script Script) (: task-dir str)] :post [(: % Seen)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "台本 script を、本物の coordinator-link(本番の入口と同じく GET /watch を使う・最後の連絡の刻 0)と本番の周の間の待ちの下で回すため。
   壁の時計の代役は worker の側の全部(口・待ち・調整ループ)の外側、偽の coordinator はその内側で台本の時計(単調)だけを読む。"
  (val link (LinkState WORKER #("cpu") 1 0 T.fence-ms task-dir "boot" 0 0 0 :watch True))
  (<- seen Seen (with-handlers [(state) (sim-time-handler :clock (SimClock)) os-file-handler slog-handler no-wakes stop-signal-never-comes
                                script-clock wall-clock-stepped (scripted-coordinator script) tick-pauses
                                (coordinator-link link (route-cell "http://coord") LINK-ROUTE (route-cell "http://coord"))
                                cut-off-noted]
                  (worker-then-seen)))
  seen)


(defk cut-with-step [back-seconds]
  {:pre [(: back-seconds float)] :post [(: % Script)] :tags {:context "doeff-cluster-test" :role "program"}}
  "途絶の台本: 10 秒目から coordinator に届かない。届かなかった最初の heartbeat の時に壁の時計を back-seconds 戻す。途絶の始まりから
   100 秒動かす(移し替えの 60 秒より後まで)。"
  (Script :cut-ms 10000 :cut-back-seconds back-seconds :step-after-beats None :beats-back-seconds 0.0 :until-ms 110000))


(defk silence-at-cut-off [seen]
  {:pre [(: seen Seen)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "自己停止を決めた刻の、実際の沈黙の長さ(coordinator が最後に答えた heartbeat からの ms — coordinator が移し替えを数える起点と同じ)。"
  (assert (is-not seen.cut-off-ms None) seen)
  (- seen.cut-off-ms (get seen.delivered -1)))


(deftest test-the-silence-is-the-longer-of-the-wall-and-the-monotonic-differences
  ;; 判断(heartbeat_rules.silent-ms)を直に呼ぶ: 起点(壁 100000・単調 5000)から実際に 30 秒過ぎた時の沈黙。
  ;; 壁の時計が動かない: 2 つの差は同じ。
  (<- steady int (silent-ms 130000 35000 100000 5000))
  (assert (= steady 30000))
  ;; 壁の時計が 45 秒戻った: 壁の差は負になる — 単調な差が実際の長さを守る。
  (<- stepped-back int (silent-ms 85000 35000 100000 5000))
  (assert (= stepped-back 30000))
  ;; 機体が 30 秒のうち 25 秒 suspend していた: 単調な時計は 5 秒しか進まない — 壁の差が拾う。
  (<- suspended int (silent-ms 130000 10000 100000 5000))
  (assert (= suspended 30000))
  ;; 壁の時計が 10 秒進んだ: 進んだ分だけ長く読む(suspend と見分けられない — 早く止まる向き)。
  (<- stepped-forward int (silent-ms 140000 35000 100000 5000))
  (assert (= stepped-forward 40000)))


(deftest test-a-cut-off-worker-decides-its-self-stop-at-the-fence [tmp-path]
  ;; 対照(直す前も後も緑): 壁の時計が動かない途絶では、沈黙が fence を越えた最初の周(届かない間は RESEND-AFTER-MS ごとに送り直す)で
  ;; 自己停止を決める。
  (<- script Script (cut-with-step 0.0))
  (<- seen Seen (seen-on script (str (/ tmp-path "tasks"))))
  (<- silence int (silence-at-cut-off seen))
  (assert (< T.fence-ms silence (+ T.fence-ms (* 2 RESEND-AFTER-MS))) #(silence seen)))


(deftest test-a-wall-clock-stepped-back-past-the-c4-margin-does-not-delay-the-self-stop [tmp-path]
  ;; 失敗ケース(余白 8 秒を越える 9 秒の戻り): 途絶の最中に壁の時計が 9 秒戻った worker は、戻らない worker と同じ刻に自己停止を決める。
  ;; 壁の時計の差で判じると決める刻が 9 秒遅れ、止め切りの最悪(決めた刻 + 返事の上限 15 + 接続の上限 2 + 停止の猶予 15)が移し替えの
  ;; 60 秒を越える(条 C4 の余白 8 秒を使い切る)。
  (<- control-script Script (cut-with-step 0.0))
  (<- control Seen (seen-on control-script (str (/ tmp-path "control"))))
  (<- script Script (cut-with-step 9.0))
  (<- seen Seen (seen-on script (str (/ tmp-path "stepped"))))
  (assert (is-not seen.stepped-ms None) seen)
  (<- silence int (silence-at-cut-off seen))
  (<- control-silence int (silence-at-cut-off control))
  (assert (= silence control-silence) #(silence control-silence seen))
  (assert (< (- silence T.fence-ms) C4-MARGIN-MS) #(silence seen)))


(deftest test-a-wall-clock-stepped-back-45-seconds-still-decides-the-self-stop-before-the-reassign-deadline [tmp-path]
  ;; 失敗ケース(2 か所で走る時間帯): 途絶の最中に壁の時計が 45 秒戻っても、coordinator が job を他へ移す刻(最後に届いた heartbeat から
  ;; reassign-after-ms)より前に自己停止を決める。壁の時計の差で判じると、決めるのは沈黙 65 秒の時 — coordinator が 60 秒で他へ置いた job と、
  ;; まだ止めると決めていないこの worker の job が同時に走る。
  (<- script Script (cut-with-step 45.0))
  (<- seen Seen (seen-on script (str (/ tmp-path "tasks"))))
  (<- silence int (silence-at-cut-off seen))
  (assert (< silence T.reassign-after-ms) #(silence seen))
  (assert (< T.fence-ms silence (+ T.fence-ms (* 2 RESEND-AFTER-MS))) #(silence seen)))


(deftest test-a-wall-clock-stepped-forward-during-a-cut-only-brings-the-self-stop-earlier [tmp-path]
  ;; 壁が進む向き(直す前も後も緑): 途絶の最中に壁の時計が 5 秒進むと、沈黙が 5 秒伸びたと読み、5 秒早く自己停止を決める。遅くはならない
  ;; (suspend と見分けられないので、早く止まる向きに寄せる)。
  (<- script Script (cut-with-step -5.0))
  (<- seen Seen (seen-on script (str (/ tmp-path "tasks"))))
  (<- silence int (silence-at-cut-off seen))
  (assert (< (- T.fence-ms 5000) silence (+ (- T.fence-ms 5000) (* 2 RESEND-AFTER-MS))) #(silence seen)))


(deftest test-a-worker-resumed-from-a-suspend-past-the-fence-decides-its-self-stop-without-waiting-for-a-reply [tmp-path]
  ;; 機体の suspend(直す前も後も緑 — 単調な時計だけで計ると赤): suspend は process から見ると、単調な時計が進まずに壁の時計だけが
  ;; 進む。届いている worker が 3 回目の heartbeat の後に 30 秒(fence 20 秒の後)眠ると、戻った最初の周で、heartbeat の返事を待たずに
  ;; 自己停止を決める(#2806 — 眠っている間に coordinator が移し替えていても、返事を待つ間に動かし続けない)。
  (val script (Script :cut-ms None :cut-back-seconds 0.0 :step-after-beats 3 :beats-back-seconds -30.0 :until-ms 20000))
  (<- seen Seen (seen-on script (str (/ tmp-path "tasks"))))
  (assert (is-not seen.stepped-ms None) seen)
  (assert (is-not seen.cut-off-ms None) seen)
  ;; 決めたのは戻った最初の周(単調な時計では眠りは 0 秒 — 次の heartbeat の期限までに)で、その間に届いた heartbeat は無い。
  (assert (<= seen.stepped-ms seen.cut-off-ms (+ seen.stepped-ms (// T.lease-ms 2))) seen)
  (assert (= seen.beats-at-cut-off (len (lfor at seen.delivered :if (<= at seen.stepped-ms) at))) seen))


(deftest test-a-wall-clock-stepped-forward-within-the-fence-stops-nothing [tmp-path]
  ;; 壁が進む向きの対照(直す前も後も緑): 届いている worker の壁の時計が 5 秒(fence の内)進んでも、自己停止は決めない。
  (val script (Script :cut-ms None :cut-back-seconds 0.0 :step-after-beats 3 :beats-back-seconds -5.0 :until-ms 20000))
  (<- seen Seen (seen-on script (str (/ tmp-path "tasks"))))
  (assert (is-not seen.stepped-ms None) seen)
  (assert (is seen.cut-off-ms None) seen))


(deftest test-a-wall-clock-stepped-back-while-in-touch-does-not-hold-the-heartbeats-back [tmp-path]
  ;; 失敗ケース(途絶なし): 届いている worker の壁の時計が、3 回目の heartbeat の後に 70 秒戻っても、heartbeat の送りの間はどれも lease の
  ;; 半分(5 秒)以下。壁の時計の差で待つと、拍の送りも拍の外の送りも「前の刻 + 間隔」まで 70 秒待ち、coordinator は移し替えの 60 秒を
  ;; 越えた沈黙と読んで、届いている worker の job を他へ移す(worker の側の沈黙は負なので自己停止もしない — 2 か所で走る)。
  (val script (Script :cut-ms None :cut-back-seconds 0.0 :step-after-beats 3 :beats-back-seconds 70.0 :until-ms 100000))
  (<- seen Seen (seen-on script (str (/ tmp-path "tasks"))))
  (assert (is-not seen.stepped-ms None) seen)
  (val gaps (tuple (gfor #(earlier later) (zip seen.delivered (cut seen.delivered 1 None)) (- later earlier))))
  (assert (<= (max gaps) (// T.lease-ms 2)) #((max gaps) seen))
  (assert (is seen.cut-off-ms None) seen))


(defhandler wall-stepped-before-the-wait [#^ float back-seconds #^ int step-at-ms]
  "調整ループのすぐ外(coordinator への口と周の間の待ちより手前)に立つ代役: 台本の時計が step-at-ms 以後の最初の周の間の待ち
   (AwaitNextTick)で、待ちへ渡す前に壁の時計を back-seconds 戻す(1 度だけ)— 起きる期限を組み立てた後・待ちが時計を読む前の step。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに戻す秒と刻を変える(Ask で区別できない)。
  (session var stepped False)
  (AwaitNextTick [policy changed wakes began began-mono-ms stopping]
    (<- at int (ScriptClockMs))
    (when (and (not stepped) (>= at step-at-ms))
      (:= stepped True)
      (<- (StepWallClock back-seconds)))
    (<- waited effect)
    (resume waited)))


(defk seen-with-a-step-before-the-wait [back-seconds step-at-ms task-dir]
  {:pre [(: back-seconds float) (: step-at-ms int) (: task-dir str)] :post [(: % Seen)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "途絶の台本(10 秒目から届かない・台本の側では壁の時計を動かさない)を、seen-on と同じ組の一番内側に wall-stepped-before-the-wait を
   足して回すため。"
  (val script (Script :cut-ms 10000 :cut-back-seconds 0.0 :step-after-beats None :beats-back-seconds 0.0 :until-ms 110000))
  (val link (LinkState WORKER #("cpu") 1 0 T.fence-ms task-dir "boot" 0 0 0 :watch True))
  (<- seen Seen (with-handlers [(state) (sim-time-handler :clock (SimClock)) os-file-handler slog-handler no-wakes stop-signal-never-comes
                                script-clock wall-clock-stepped (scripted-coordinator script) tick-pauses
                                (coordinator-link link (route-cell "http://coord") LINK-ROUTE (route-cell "http://coord"))
                                cut-off-noted (wall-stepped-before-the-wait back-seconds step-at-ms)]
                  (worker-then-seen)))
  seen)


(deftest test-a-wall-clock-stepped-back-right-before-a-wait-does-not-delay-the-self-stop [tmp-path]
  ;; 失敗ケース(設計検証の盲検 A): 途絶の最中、起きる期限を組み立てた後・周の間の待ちが時計を読む前に壁の時計が戻っても、戻らない
  ;; worker と同じ刻に自己停止を決める。待ちを「期限 − 今の壁の時計」で数えると、待ちが戻った分だけ延びる — 沈黙 19.5 秒(台本の 27 秒目)
  ;; の待ちの直前に 9 秒戻すと沈黙 29 秒(条 C4 の余白 8 秒を越える)・途絶の最初の待ち(10 秒目)の直前に 45 秒戻すと沈黙 48 秒。
  (<- control-script Script (cut-with-step 0.0))
  (<- control Seen (seen-on control-script (str (/ tmp-path "control"))))
  (<- control-silence int (silence-at-cut-off control))
  (for [#(back-seconds step-at-ms) [#(9.0 27000) #(45.0 10000)]]
    (<- seen Seen (seen-with-a-step-before-the-wait back-seconds step-at-ms (str (/ tmp-path (str step-at-ms)))))
    (<- silence int (silence-at-cut-off seen))
    (assert (= silence control-silence) #(back-seconds step-at-ms silence control-silence seen))))


(deftest test-a-wall-clock-stepped-back-45-seconds-right-before-the-last-wait-inside-the-fence-decides-before-the-reassign-deadline [tmp-path]
  ;; 失敗ケース(2 か所で走る時間帯 — 設計検証の盲検 A): 沈黙 19.5 秒の待ちの直前に壁の時計が 45 秒戻っても、coordinator が job を他へ
  ;; 移す刻(最後に届いた heartbeat から reassign-after-ms)より前に自己停止を決める。前の形は沈黙 65 秒の時に決めた。
  (<- seen Seen (seen-with-a-step-before-the-wait 45.0 27000 (str (/ tmp-path "tasks"))))
  (<- silence int (silence-at-cut-off seen))
  (assert (< silence T.reassign-after-ms) #(silence seen))
  (assert (< T.fence-ms silence (+ T.fence-ms (* 2 RESEND-AFTER-MS))) #(silence seen)))


(defhandler slept-inside-the-reply [#^ float seconds]
  "coordinator への口と偽の coordinator の間に立つ代役: 3 回目の heartbeat の返事の処理の途中(口が ready の file の環境変数を問う
   ReadEnvironment — 口はもう返事の刻を読んでいる・調整ループはまだ壁の時計を読んでいない)で機体が seconds 秒眠り(壁の時計だけが進む)、
   戻った後は coordinator に届かない(以後の要求は接続の失敗)。眠った刻は台本の記録(Seen.stepped-ms)に残らないので、3 回目の heartbeat
   が届いた刻(Seen.delivered の 3 つ目)で読む。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに眠る秒を変える(Ask で区別できない)。
  (session var asked 0)
  (session var slept False)
  (ReadEnvironment [names]
    (:= asked (+ asked 1))
    (when (= asked 3)
      (:= slept True)
      (<- (StepWallClock (- seconds))))
    (<- found effect)
    (resume found))
  (HttpRequest [url]
    (when slept
      (return (resume (HttpFailed :url url :detail "ConnectError: refused" :kind HttpFailureKind.CONNECT-FAILED))))
    (<- answer effect)
    (resume answer)))


(defk seen-with-a-sleep-inside-the-reply [beat-beside task-dir]
  {:pre [(: beat-beside bool) (: task-dir str)] :post [(: % Seen)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "途絶えない台本を、seen-on と同じ組の口のすぐ外側に slept-inside-the-reply(30 秒 — fence 20 秒の後)を足して回すため。beat-beside =
   拍の外の背景の task でも heartbeat を送るか(本番の入口は真 — 偽は、背景の task が止まった口と同じく拍だけが送る形)。"
  (val script (Script :cut-ms None :cut-back-seconds 0.0 :step-after-beats None :beats-back-seconds 0.0 :until-ms 40000))
  (val link (LinkState WORKER #("cpu") 1 0 T.fence-ms task-dir "boot" 0 0 0 :watch True :beat-beside beat-beside))
  (<- seen Seen (with-handlers [(state) (sim-time-handler :clock (SimClock)) os-file-handler slog-handler no-wakes stop-signal-never-comes
                                script-clock wall-clock-stepped (scripted-coordinator script) (slept-inside-the-reply 30.0) tick-pauses
                                (coordinator-link link (route-cell "http://coord") LINK-ROUTE (route-cell "http://coord"))
                                cut-off-noted]
                  (worker-then-seen)))
  seen)


(deftest test-a-worker-that-slept-inside-the-reply-handling-decides-its-self-stop-at-the-first-tick-after-the-resume [tmp-path]
  ;; 失敗ケース(設計検証の盲検 A の副次の観測): 3 回目の heartbeat の返事の処理の途中に機体が 30 秒(fence の後)眠り、戻ったら
  ;; coordinator に届かない。拍の外の送りが在る口(本番)も、拍だけが heartbeat を送る口も、戻った最初の周で自己停止を決める。拍の頭の
  ;; 刻を単調な差だけで導くと、拍だけが送る口は眠りの間に過ぎた期限を捨て、決めるのが 210 秒後になった。
  (for [beat-beside [True False]]
    (<- seen Seen (seen-with-a-sleep-inside-the-reply beat-beside (str (/ tmp-path (str beat-beside)))))
    (assert (= (len seen.delivered) 3) #(beat-beside seen))
    (val slept-at (get seen.delivered 2))
    (assert (is-not seen.cut-off-ms None) #(beat-beside seen))
    (assert (<= slept-at seen.cut-off-ms (+ slept-at (// T.lease-ms 2))) #(beat-beside seen))))
