;;; coordinator の要求の受付の、まねた環境の言い換え — process の中の列(RequestQueue)で intent の NextRequests・Reply・
;;; CoordinatorFault に答える(本番の答え手は shared/protocol/inbox.hy の http-requests と coordinator/protocol/faults.hy の coordinator-faults)。handler の組
;;; (coordinator/entry/handler_sets.hy の emulated-handlers)が並べる。entry の層から移した(DOEFF105)。
;;; 取りは本番の受付と同じく、要求が積まれるか、渡された待ちの秒(None = 期限なし)が過ぎるか、外の出来事(nudge-takers)で起きる。
;;; worker の代役(模擬の時計の下の宿)が静かな間に眠る前に預けた仮の heartbeat は、この列がその刻に普通の heartbeat の要求として積む
;;; — 調停ループは本番と同じ要求しか受けない(#3865・案 4)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import dataclasses [dataclass])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_time [Delay])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Promise])
(import doeff_cluster.shared.intent.protocol [Request Reply PlainText])
(import dataclasses [replace])
(import doeff_cluster.coordinator.intent.cluster_model [ProvisionalBeat CoordinatorFault]
        doeff_cluster.shared.intent.protocol [NextRequests])
(import doeff_cluster.shared.core.promise_wait [promise-or-timeout])


(val REPLAN "replan")   ; 取り手の呼び鈴の答え: 預けた仮の heartbeat が変わった(次に起きる刻を求め直す — 要求でも外の出来事でもない)
(val HEARD "heard")     ; 預けた仮の heartbeat の鈴の答え: 列がその heartbeat を積み、返事が worker の最後の返事と違う(宿はその刻のまま返事で動く)
(val DOWN "down")       ; 預けた仮の heartbeat の鈴の答え: coordinator が止まった・落ちた(預けた heartbeat は受けられない)


(defclass RestBell []
  "静かな拍を眠る worker の宿の呼び鈴(#2790): promise = 宿が待つ Promise・rung = もう鳴らしたか。列(返事が違う heartbeat・coordinator の
   止まり)と世界(宿の真実の書き換え)の両方が鳴らすので、2 度目は鳴らさない(満たした Promise をもう一度満たすと RuntimeError)。"
  (defn #^ None __init__ [self #^ Promise promise]
    (setv self.promise promise self.rung False)
    None))


(defk ring-bell [bell reason]
  {:pre [(: bell RestBell) (: reason str)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "宿の呼び鈴を reason で 1 度だけ鳴らすため(鳴らし済みなら何もしない — 確かめと満たしの間に他の task は割り込まない)。"
  (when (not bell.rung)
    (setv bell.rung True)
    (<- (CompletePromise bell.promise reason)))
  None)


(defrecord DepositedBeat
  "worker の宿が列に預けた仮の heartbeat 1 つ(#2790): beat = 仮の heartbeat(刻・要求・worker の名)・bell = 宿の眠りを起こす呼び鈴(列が
   積んだ heartbeat の返事が worker の最後の返事と違う時に HEARD で、coordinator が止まった時に DOWN で鳴らす — 同じ宿の預けは同じ鈴を持つ)。"
  (#^ ProvisionalBeat beat)
  (#^ RestBell bell))


(defrecord TakenBeat
  "刻の来た預けの仮の heartbeat を、宿を起こさずにその刻の要求として列に積んだ物 1 つ(#2850 の続き): held = 預け(heartbeat と宿の
   呼び鈴)・request = 積んだ要求(heartbeat の要求に、この組の返事の札を付けた物)。返事の答え手(Reply)が返事を受けるまで列が覚える。"
  (#^ DepositedBeat held)
  (#^ Request request))


(defrecord HeardBeat
  "列が積んだ預けの仮の heartbeat 1 つの返事: name = worker の名・at = heartbeat の刻・answer = 返事 #(status 本文)(送り手が本物の
   heartbeat で受けるのと同じ形)。宿はその刻に heartbeat を送る代わりにこの返事を読む(take-heard)— 写した heartbeat(forget-heard)の
   物は外す。"
  (#^ str name)
  (#^ int at)
  (#^ tuple answer))


(defclass RequestQueue []
  "process の中の要求の列(HTTP の受付の代わり)。送り手は Request の slot に doeff の Promise を入れて並べ、Wait で返事
   #(status 本文)を受ける。up = 受け付けているか(coordinator の process が止まっている間は偽 — 送り手は接続の失敗として扱う)。
   bells = 切り離した task の key → 呼び鈴(doeff の Promise)の tuple。送り手が task の終わりを読み直さずに待つため、読む前に掛ける。
   模擬の coordinator の Persist の見張り(local.hy の observe-requests)が、その key の task の終わりの phase を書いた時に鳴らす。
   takers = 列の取り手(queued-requests の NextRequests)が、列が空の間に掛けた呼び鈴(doeff の Promise の list — 掛けた順)。送り手が
   列に積んだ時(enqueue-request)に全部鳴らして外す。列は読み直さない(前は仮想の 0.05 秒ごとに見直していた — 使い手の仮想の
   1700 秒の検で 37,222 回眠り、所要の大半になった)。
   faults = coordinator の中の欠陥の log の行(CoordinatorFault の Fault — 出た順)。本番の受付が stderr へ出す 1 行の代わり。
   takes = 取り手が取った回数(coordinator の歩の数 — 検が読む)。crash-waiting = 落ちの注入(次の Persist で落とす — local.hy の
   CrashCoordinator)が待っているか(世界が立て、落ちで下ろす)。模擬の coordinator の書きの見張り(local.hy の observe-requests)は、
   これが偽の書きでは落ちの判断を世界へ問わない(#3132)。
   beats = worker の宿が預けた仮の heartbeat(DepositedBeat の list — 刻の順・#2790)。取り手はその刻に起きて要求として積む(heartbeats-at)。
   replies = worker の名 → その worker が最後に受けた heartbeat の返事(JSON の本文 — 積んだ heartbeat の返事が同じかを比べる。返事の
   答え手 Reply が書く)。arrivals = 積んだ要求の id → 積んだ刻(同じ刻の要求を送り手の名の順に取る)。settled = worker の名 → その宿が
   宿の真実へ写し終えた最後の仮の heartbeat の刻(forget-heard が書く — 写した刻までの返事 heard を外す)。taken = 積んだ預けの仮の
   heartbeat のうち、まだ返事の無い物(TakenBeat の list)・heard = 積んだ heartbeat の返事のうち、宿がまだ読んでいない・写していない物
   (HeardBeat の list)。deposits = 宿が heartbeat を預けた回数・heard-wakes = 返事が違って宿を HEARD で起こした回数(どちらも検が読む —
   起こされた宿は先の拍を試し直して預け直すので、預けの回数が宿の起きた回数の物差しになる)。"
  (defn #^ None __init__ [self]
    (setv self.pending [] self.up False self.bells {} self.takers [] self.faults [] self.takes 0 self.crash-waiting False
          self.beats [] self.replies {} self.arrivals {} self.settled {} self.taken [] self.heard [] self.deposits 0 self.heard-wakes 0)
    None))


(defk enqueue-request [queue request]
  {:pre [(: queue RequestQueue) (: request Request)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "要求を列の後ろに積み、列が空の間に待っていた取り手の呼び鈴を全部鳴らして外すため(取り手は積んだのと同じ仮想の刻で起きる)。
   積む順 = 取る順(列は先頭から取る)— ただし同じ仮想の刻に届いた要求は、取る時に送り手の名の順に並べる(take-requests)。鳴らすのは
   積んだ後 — 起きた取り手は必ず積んだ要求を見る。"
  (<- now int (now-epoch-ms))
  (.append queue.pending request)
  (setv (get queue.arrivals (id request)) now)
  (val waiting (tuple queue.takers))
  (.clear queue.takers)
  (for [bell waiting]
    (<- (CompletePromise bell True)))
  None)


(defk nudge-takers [queue]
  {:pre [(: queue RequestQueue)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "要求ではない外の出来事(止めの合図・止まりの注入)を、待っている取り手に知らせるため — 取り手は要求の無いまま返り、調停ループが
   その出来事に気づく(本番の停止の合図が受付の箱を起こすのと同じ・#3865)。"
  (val waiting (tuple queue.takers))
  (.clear queue.takers)
  (for [bell waiting]
    (<- (CompletePromise bell False)))
  None)


(defk replan-takers [queue]
  {:pre [(: queue RequestQueue)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "預けた仮の heartbeat が変わった(預けた・取り下げた)ことを、待っている取り手に知らせるため(REPLAN — 取り手は次に起きる刻を求め
   直して待ち直す。要求ではないので返らない)。"
  (val waiting (tuple queue.takers))
  (.clear queue.takers)
  (for [bell waiting]
    (<- (CompletePromise bell REPLAN)))
  None)


(defk deposit-beats [queue name beats bell]
  {:pre [(: queue RequestQueue) (: name str) (: beats tuple) (: bell RestBell)] :post [(: % None)]
   :tags {:context "coordinator" :role "protocol"}}
  "worker の宿が、静かな拍の heartbeat を仮の heartbeat(ProvisionalBeat の tuple — 刻の順)として預けるため(#2790)。同じ worker の前の
   預けは置き換える。取り手に次に起きる刻を求め直させる。bell = 列が積んだ heartbeat の返事が違う時と、coordinator が止まった時に鳴らす
   宿の呼び鈴。"
  (setv queue.beats (sorted (+ (lfor held queue.beats :if (!= held.beat.name name) held)
                               (lfor beat beats (DepositedBeat :beat beat :bell bell)))
                            :key (fn [held] held.beat.at)))
  (+= queue.deposits 1)
  (<- (replan-takers queue))
  None)


(defk withdraw-beats [queue name since]
  {:pre [(: queue RequestQueue) (: name str) (: since int)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "worker の宿が、起きた刻 since 以後の預けた仮の heartbeat を取り下げるため(宿は起きた後の拍を自分で打つ)。取り下げた heartbeat が
   在れば、取り手に次に起きる刻を求め直させる。since より前の heartbeat は残す(1 拍ずつの走りでは届いていた heartbeat — 取り手が積む)。"
  (val kept (lfor held queue.beats :if (or (!= held.beat.name name) (< held.beat.at since)) held))
  (when (!= (len kept) (len queue.beats))
    (setv queue.beats kept)
    (<- (replan-takers queue)))
  None)


(defk drop-beats [queue]
  {:pre [(: queue RequestQueue)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "coordinator が止まった・落ちた時に、預けた仮の heartbeat を全部捨て、預けた宿を DOWN で起こすため(止まっている coordinator は
   heartbeat を受けない — 宿は次の拍から本物の heartbeat を送り、届かないことを本番と同じに数える)。要求として積み、まだ返事の無い
   heartbeat(taken)も列から外して DOWN で起こす(宿はその刻の heartbeat を本物で送り、届かないことを本番と同じに数える)。"
  (val held (+ (tuple queue.beats) (tuple (gfor taken queue.taken taken.held))))
  (val unanswered (tuple (gfor taken queue.taken taken.request)))
  (setv queue.beats [])
  (setv queue.taken [])
  (setv queue.pending (lfor request queue.pending :if (not (any (gfor gone unanswered (is gone request)))) request))
  (for [deposit held]
    (<- (ring-bell deposit.bell DOWN)))
  None)


(defk await-first-request [queue timeout-seconds]
  {:pre [(: queue RequestQueue) (: timeout-seconds (| float int None))] :post [(: % (| bool str None))]
   :tags {:context "coordinator" :role "protocol"}}
  "列が空なら、送り手が積む(enqueue-request が呼び鈴を鳴らす)か timeout 秒(None = 期限なし)が過ぎるまで 1 回だけ眠るため(読み
   直さない)。列に何か在れば眠らない。起きた時(時間切れ・取り消しを含む)は自分の呼び鈴を取り手の list から外す。答え = True
   (積まれた)・False(nudge-takers — 要求ではない出来事)・REPLAN(預けた仮の heartbeat が変わった)・None(時間切れか、眠らなかった)。"
  (var woke None)
  (when (and (not queue.pending) (or (is timeout-seconds None) (> timeout-seconds 0)))
    (<- bell Promise (CreatePromise))
    (.append queue.takers bell)
    (try
      (<- answer (| bool str None) (promise-or-timeout bell.future timeout-seconds))
      (:= woke answer)
      ;; 積まれて起きたら、同じ刻に続けて積まれる残り(1 つの書き手が続けて積む要求)を待ってから取る — 0 秒の眠りは、同じ刻の
      ;; 書き手が手を止めるまで取り手を後ろへ回す(模擬の時計は普通の task が全部止まってから進む)。取りのまとまりが書きの途中で
      ;; 割れない(tests/test_request_queue_wakes の複数の書き・#2618)。
      (when (is answer True)
        (<- (Delay 0.0)))
      (finally
        (when (in bell queue.takers)
          (.remove queue.takers bell)))))
  woke)


(defk heartbeats-at [queue at]
  {:pre [(: queue RequestQueue) (: at int)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "刻 at までに刻の来た預けの仮の heartbeat を、宿を起こさずにその刻に届いた要求として列に積むため — 調停ループが 1 拍ずつの走りと
   同じく、同じ刻の要求と 1 つの歩で受ける(#2850)。返事は答え手(Reply)が見る: worker の最後の返事と同じなら宿は眠ったまま写し、
   違えば宿を HEARD で起こしてその刻のまま返事で動かす。宿の後の heartbeat の預けはそのまま残す。"
  (val due (tuple (gfor held queue.beats :if (<= held.beat.at at) held)))
  (when due
    (setv queue.beats (lfor held queue.beats :if (not (any (gfor other due (is other held)))) held))
    (for [held due]
      (<- slot Promise (CreatePromise))
      (val request (replace held.beat.request :slot slot))
      (setv queue.taken (+ queue.taken [(TakenBeat :held held :request request)]))
      (<- (enqueue-request queue request)))
    ;; 要求で起きた取り手と同じく 0 秒の眠りをはさむ(await-first-request): 同じ刻に先に登録された timer(眠っていない worker の本物の
    ;; 拍)を先に回し、その要求も同じ取りに入れる(1 拍ずつの走りの取りと同じまとまり — #2850 の系全体の静かな区間の 30.1 秒の w2)。
    (<- (Delay 0.0)))
  None)


(defk heard-beat [queue taken status body]
  {:pre [(: queue RequestQueue) (: taken TakenBeat) (: status int) (: body (| PlainText dict list tuple str int float bool None))]
   :post [(: % None)]
   :tags {:context "coordinator" :role "protocol"}}
  "列が積んだ預けの仮の heartbeat taken への返事 #(status body) を、眠っている宿へ届けるため — 返事を宿が読む物として覚え(heard)、
   worker の最後の返事と違えば(200 でない・JSON が違う)宿を HEARD で起こす(宿はその刻のまま、送る代わりにこの返事を読んで動く —
   take-heard)。同じなら宿は眠ったまま写す。比べるのは返事の答え手が最後の返事を書き換える前。"
  (val beat taken.held.beat)
  (val quiet (and (= status 200) (= body (.get queue.replies beat.name))))
  (setv queue.heard (+ queue.heard [(HeardBeat :name beat.name :at beat.at :answer #(status body))]))
  (when (not quiet)
    (+= queue.heard-wakes 1)
    (<- (ring-bell taken.held.bell HEARD)))
  None)


(defk take-heard [queue name at]
  {:pre [(: queue RequestQueue) (: name str) (: at int)] :post [(: % (| tuple None))] :tags {:context "coordinator" :role "protocol"}}
  "worker name の宿が刻 at の heartbeat を送る前に、列がその刻の預けの仮の heartbeat を既に積んで返事を受けたかを知り、受けていれば
   その返事 #(status 本文)を読んで外すため(送ると同じ刻の heartbeat を coordinator が 2 度受ける)。受けていなければ None(宿は本物で
   送る)。"
  (val found (next (gfor heard queue.heard :if (and (= heard.name name) (= heard.at at)) heard) None))
  (when (is found None)
    (return None))
  (setv queue.heard (lfor heard queue.heard :if (is-not heard found) heard))
  found.answer)


(defk forget-heard [queue beats]
  {:pre [(: queue RequestQueue) (: beats tuple)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "宿が宿の真実へ写した仮の heartbeat beats を、その宿の写し終えた刻(settled)として覚え、その刻までの返事(heard)を外すため — 宿は
   預けの heartbeat を刻の順に写し、写し終えた刻の heartbeat をもう送らない(読む者が無い)。"
  ;; 写し終えた刻は遅い方を残す(初めて写した宿は、その heartbeat の刻)。
  (for [beat beats]
    (setv (get queue.settled beat.name) (max beat.at (.get queue.settled beat.name beat.at))))
  (when beats
    (setv queue.heard (lfor heard queue.heard :if (or (not-in heard.name queue.settled) (> heard.at (get queue.settled heard.name))) heard)))
  None)


(defk take-requests [queue timeout-seconds limit]
  {:pre [(: queue RequestQueue) (: timeout-seconds (| float None)) (: limit int)] :post [(: % list)]
   :tags {:context "coordinator" :role "protocol"}}
  "本番の http-requests と同じ意味で列から要求を取るため: 最初の 1 件を timeout 秒まで(None = 期限なし)待ち、その時点で並んでいる
   要求を limit 件まで一緒に取る。待ちは列への書き(enqueue-request)で起きる — 本番の受付が要求の届いた瞬間に起きるのと同じ刻。
   預けた仮の heartbeat の刻が来たら、その heartbeat を要求として積む(heartbeats-at)ので、待ちは期限と次の預けの刻の早い方まで。外の
   出来事(nudge-takers)では要求の無いまま返る。要求の無いまま返る時は、0 秒の Delay を 1 回はさむ: 同じ仮想の刻に来る出来事(筋書きの
   止めの注入など)を先に全部通してから歩を回す(はさまないと、同じ刻の出来事と歩の順が時計の timer の登録順で決まる — 2026-09-30 の
   レビューの再現)。"
  (<- started int (now-epoch-ms))
  (val until (if (is timeout-seconds None) None (+ started (round (* 1000 timeout-seconds)))))
  (var going True)
  (while going
    (<- now int (now-epoch-ms))
    (<- (heartbeats-at queue now))
    (val beat-at (if queue.beats (. (get queue.beats 0) beat at) None))
    (val target (min (gfor at [until beat-at] :if (is-not at None) at) :default None))
    (cond
      queue.pending (:= going False)
      (and (is-not until None) (>= now until)) (:= going False)
      True (do (<- woke (| bool str None) (await-first-request queue (if (is target None) None (/ (- target now) 1000.0))))
               (when (is woke False)
                 (:= going False)))))
  (when (not queue.pending)
    (<- (Delay 0.0)))
  ;; 同じ仮想の刻に届いた要求は送り手の名の順に並べる(同じ送り手の中の順は保つ — 並べ替えは安定)。模擬の時計では、同じ刻に起きる
  ;; task の順が timer を登録した順で決まり、静かな拍を一度に眠る宿(#2850)と 1 拍ずつ眠る宿で入れ替わる。本番の同じ刻の到着の順は
  ;; 決まっておらず、名の順はその 1 つの並び。
  (val batch (sorted (cut queue.pending 0 limit) :key (fn [request] #((.get queue.arrivals (id request) 0) request.peer))))
  (setv queue.pending (cut queue.pending limit None))
  (for [request batch]
    (.pop queue.arrivals (id request) None))
  (+= queue.takes 1)
  batch)


(defk await-answer [queue slot seconds]
  {:pre [(: queue RequestQueue) (: slot Promise) (: seconds float)] :post [(: % (| tuple None))]
   :tags {:context "coordinator" :role "protocol"}}
  "送り手が、列に積んだ要求の返事を seconds 秒(本番の HTTP の client の打ち切り)まで待つため。答え = 返事か、時間切れの None。"
  (<- answer (| tuple None) (promise-or-timeout slot.future seconds))
  answer)


(defhandler queued-requests [#^ RequestQueue queue]
  (NextRequests [timeout-seconds limit]
    (<- batch list (take-requests queue timeout-seconds limit))
    (resume batch))
  (Reply [request status body]
    ;; この組の要求の列は、返事の札を CreatePromise で作る(Request.slot は受け口ごとの札 — HTTP の受け口は ReplySlot)。
    ;; 札が Promise でなければ、別の受け口の要求がこの組に来た誤り — 返事を落とさず名指して落ちる。
    (when (not (isinstance request.slot Promise))
      (raise (TypeError (.format "返事の札が Promise でない({}): {} {}" (type request.slot) request.method request.path))))
    ;; 刻の来た預けの仮の heartbeat を積んだ物なら、返事を眠っている宿へ届ける(最後の返事を書き換える前に比べる)。
    (val taken (next (gfor held queue.taken :if (is held.request request) held) None))
    (when (is-not taken None)
      (setv queue.taken (lfor held queue.taken :if (is-not held taken) held))
      (<- (heard-beat queue taken status body)))
    ;; worker が最後に受けた heartbeat の返事を覚える(積んだ heartbeat の返事が同じかを比べる — heard-beat)。
    (when (and (= status 200) (= request.path "/heartbeat") (isinstance request.actor str))
      (setv (get queue.replies request.actor) body))
    (<- (CompletePromise request.slot #(status body)))
    (resume None))
  (CoordinatorFault [fault]
    (.append queue.faults fault)
    (resume None)))
