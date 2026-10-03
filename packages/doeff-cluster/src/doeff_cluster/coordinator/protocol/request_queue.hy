;;; coordinator の要求の受付の、まねた環境の言い換え — process の中の列(RequestQueue)で intent の NextRequests / IdleNextRequests・
;;; Reply・CoordinatorFault に答える(本番の答え手は shared/protocol/inbox.hy の http-requests と coordinator/protocol/faults.hy の coordinator-faults)。handler の組
;;; (coordinator/entry/handler_sets.hy の emulated-handlers)が並べる。entry の層から移した(DOEFF105)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import dataclasses [dataclass])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_time [Delay])
;; 模擬の列は、要求の無い間の静かな区間を本番の判断の関数で試す(idle_policy)。
(import doeff_cluster.coordinator.core.idle_policy [quiet-stretch next-step-at MAX-QUIET-MS])
(import doeff_cluster.coordinator.core.api_policy [TICK-MS])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Promise])
(import doeff_cluster.shared.intent.protocol [Request Reply])
(import dataclasses [replace])
(import doeff_cluster.coordinator.intent.cluster_model [IdleProbe IdleNextRequests IdleTaken QuietStep QuietStretch ProvisionalBeat
                                                       HeartbeatReply CoordinatorFault]
        doeff_cluster.shared.intent.protocol [NextRequests])
(import doeff_cluster.coordinator.protocol.replies [reply-json])
(import doeff_cluster.shared.core.promise_wait [promise-or-timeout])


(val REPLAN "replan")   ; 取り手の呼び鈴の答え: 預けた仮の拍が変わった(区間を試し直す — 要求でも外の出来事でもない)
(val BEAT "beat")       ; 預けた仮の拍の鈴の答え: その拍は静かでない(worker が本物の heartbeat を送る)
(val DOWN "down")       ; 預けた仮の拍の鈴の答え: coordinator が止まった・落ちた(預けた拍は受けられない)


(defrecord AbsorbedWatch
  "区間の中で吸った名指しの待ち 1 件(#2790): slot = 待ちの要求の返事の札(送り手が待つ Promise)・at = 期限を最後に引き直した刻(1 拍ずつの
   走りで、送り手が「変わっていない」の返事を受けて同じ問いを送り直した刻)。送り手の返事の打ち切りはこの刻から数え直す(await-answer)。"
  (#^ Promise slot)
  (#^ int at))


(defclass RestBell []
  "静かな拍を眠る worker の宿の呼び鈴(#2790): promise = 宿が待つ Promise・rung = もう鳴らしたか。列(静かでない拍の刻・coordinator の
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
  "worker の宿が列に預けた仮の拍 1 つ(#2790): beat = 仮の拍(刻・要求・解いた本文・worker の名)・bell = 宿の眠りを起こす呼び鈴(列が
   その拍を静かでないと判じた刻に BEAT で、coordinator が止まった時に DOWN で鳴らす — 同じ宿の預けは同じ鈴を持つ)。"
  (#^ ProvisionalBeat beat)
  (#^ RestBell bell))


(defclass RequestQueue []
  "process の中の要求の列(HTTP の受付の代わり)。送り手は Request の slot に doeff の Promise を入れて並べ、Wait で返事
   #(status 本文)を受ける。up = 受け付けているか(coordinator の process が止まっている間は偽 — 送り手は接続の失敗として扱う)。
   bells = 切り離した task の key → 呼び鈴(doeff の Promise)の tuple。送り手が task の終わりを読み直さずに待つため、読む前に掛ける。
   模擬の coordinator の Persist の見張り(local.hy の observe-requests)が、その key の task の終わりの phase を書いた時に鳴らす。
   takers = 列の取り手(queued-requests の NextRequests)が、列が空の間に掛けた呼び鈴(doeff の Promise の list — 掛けた順)。送り手が
   列に積んだ時(enqueue-request)に全部鳴らして外す。列は読み直さない(前は仮想の 0.05 秒ごとに見直していた — 使い手の仮想の
   1700 秒の検で 37,222 回眠り、所要の大半になった)。
   faults = coordinator の中の欠陥の log の行(CoordinatorFault の Fault — 出た順)。本番の受付が stderr へ出す 1 行の代わり。
   skip-idle = 要求が無い間、静かな区間を一度に眠るか(idle_policy.quiet-stretch — 模擬の時計の下の入口だけが真にする・2026-09-30・
   #2790。偽なら本番と同じく timeout 秒ごとに起きる)。takes = 取り手が取った回数(coordinator の歩の数 — 検が読む)。
   absorbed = 区間の中で吸った名指しの待ち(返事の札の id → AbsorbedWatch — 送り手が打ち切りを数え直す)。ends-at-marks = 生存の印を
   書く最初の歩で区間を切るか(落ちの注入が次の Persist を待つ間だけ真 — local.hy の CrashCoordinator が立て、落ちで下ろす)。模擬の
   coordinator の書きの見張り(local.hy の observe-requests)は、これが偽の書きでは落ちの判断を世界へ問わない(#3132)。
   beats = worker の宿が預けた仮の拍(DepositedBeat の list — 刻の順・#2790)。replies = worker の名 → その worker が最後に受けた
   heartbeat の返事(JSON の本文 — 仮の拍の返事が同じかを比べる。返事の答え手 Reply が書く)。arrivals = 積んだ要求の id → 積んだ刻
   (同じ刻の要求を送り手の名の順に取る)。planned = 今の区間の試しが静かと判じた仮の拍(list)・consumed = 調停ループへ渡した
   (届いた)仮の拍のうち宿がまだ写していない物(list)— 宿は眠りの拍ごとに、この 2 つに在る拍を届いたものとして宿の真実へ写す
   (#2850)。どちらも拍そのものを持ち、同じ拍かを is で比べる: id で覚えると、写されないまま残った覚え(世代の終わった宿の拍など)の
   id が、その拍が消えた後に別の仮の拍に再び使われ、「届いた」と誤って判じる。settled = worker の名 → その宿が宿の真実へ写し終えた
   最後の仮の拍の刻(forget-heard が書く)。宿は預けの拍を刻の順に写し、写し終えた刻より後の拍だけを beat-heard で問う(新しい預けの
   拍は今より後の刻)ので、consumed はその刻より後の拍だけを持つ: 調停ループへ渡した拍のうち、既に写した拍(今の区間の試し planned で
   写した拍・起きた宿が残りをまとめて写した拍)は覚えない。宿が起きた(withdraw-beats)・預け直した(deposit-beats)時は、その宿の
   覚えを外す(#2769 — 前は外す者の無い覚えが走りの長さに比例して伸び、宿の拍ごとの問いと外しの費用が窓の長さの 2 乗になった)。"
  (defn #^ None __init__ [self #^ bool [skip-idle False]]
    (setv self.pending [] self.up False self.bells {} self.takers [] self.faults [] self.skip-idle skip-idle self.takes 0
          self.absorbed {} self.ends-at-marks False self.beats [] self.replies {} self.arrivals {} self.planned []
          self.consumed [] self.settled {})
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
  "要求ではない外の出来事(止めの合図・止まりの注入)を、眠っている取り手に知らせるため。起きた取り手は、要求が無ければ本番の拍の
   刻(眠り始め + 整数秒)まで眠り直してから拍を回す(本番のループがその出来事に気づくのと同じ刻 — await-idle)。skip-idle でない
   列の取り手は起こさない(1 秒ごとの拍が、本番と同じ刻でその出来事に気づく — 起こすと本番より早く気づく)。"
  (when queue.skip-idle
    (val waiting (tuple queue.takers))
    (.clear queue.takers)
    (for [bell waiting]
      (<- (CompletePromise bell False))))
  None)


(defk replan-takers [queue]
  {:pre [(: queue RequestQueue)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "預けた仮の拍が変わった(預けた・取り下げた)ことを、静かな区間を眠っている取り手に知らせるため(REPLAN — 取り手は起きた刻より前の
   歩を残して区間を試し直す。要求ではないので本物の歩は回さない)。"
  (val waiting (tuple queue.takers))
  (.clear queue.takers)
  (for [bell waiting]
    (<- (CompletePromise bell REPLAN)))
  None)


(defk deposit-beats [queue name beats bell]
  {:pre [(: queue RequestQueue) (: name str) (: beats tuple) (: bell RestBell)] :post [(: % None)]
   :tags {:context "coordinator" :role "protocol"}}
  "worker の宿が、静かな拍の heartbeat を仮の拍(ProvisionalBeat の tuple — 刻の順)として預けるため(#2790)。同じ worker の前の
   預けは置き換える。取り手に区間を試し直させる。bell = 預けた拍のどれかを列が静かでないと判じた刻に鳴らす宿の呼び鈴。
   宿が前の預けについて問うことはもう無いので、その宿の届いた拍の覚え(consumed)も外す(起きずに終わった世代の残りを含む — #2769)。"
  (setv queue.beats (sorted (+ (lfor held queue.beats :if (!= held.beat.name name) held)
                               (lfor beat beats (DepositedBeat :beat beat :bell bell)))
                            :key (fn [held] held.beat.at)))
  (setv queue.consumed (lfor held queue.consumed :if (!= held.name name) held))
  (<- (replan-takers queue))
  None)


(defk withdraw-beats [queue name since]
  {:pre [(: queue RequestQueue) (: name str) (: since int)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "worker の宿が、起きた刻 since 以後の預けた仮の拍を取り下げるため(宿は起きた後の拍を自分で打つ)。取り下げた拍が在れば、取り手に
   区間を試し直させる。since より前の拍は残す(1 拍ずつの走りでは届いていた heartbeat — 取り手が積む)。起きた宿は眠りの拍を
   beat-heard で問わない(残りは起きた時にまとめて写す)ので、その宿の届いた拍の覚え(consumed)も外す(#2769)。"
  (setv queue.consumed (lfor held queue.consumed :if (!= held.name name) held))
  (val kept (lfor held queue.beats :if (or (!= held.beat.name name) (< held.beat.at since)) held))
  (when (!= (len kept) (len queue.beats))
    (setv queue.beats kept)
    (<- (replan-takers queue)))
  None)


(defk drop-beats [queue]
  {:pre [(: queue RequestQueue)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "coordinator が止まった・落ちた時に、預けた仮の拍を全部捨て、預けた宿を DOWN で起こすため(止まっている coordinator は heartbeat を
   受けない — 宿は次の拍から本物の heartbeat を送り、届かないことを本番と同じに数える)。届いた拍の覚え(consumed)はここでは外さない:
   起こした宿は起きた時に withdraw-beats で自分の覚えを外し、起きるまでの同じ刻に眠りの拍を写す(beat-heard で問う)ことがある。"
  (val held (tuple queue.beats))
  (setv queue.beats [])
  (for [deposit held]
    (<- (ring-bell deposit.bell DOWN)))
  None)


(defk same-reply [queue beat reply]
  {:pre [(: queue RequestQueue) (: beat ProvisionalBeat) (: reply HeartbeatReply)] :post [(: % bool)]
   :tags {:context "coordinator" :role "protocol"}}
  "仮の拍への返事(判断の答えの本文)が、その worker が最後に受けた heartbeat の返事と同じ JSON かを知るため — 同じなら worker の
   宿の真実は時刻の欄のほか変わらない(静かな拍)。返事の綴りは本番の返事の答え手 reply-bodies と同じ reply-json。"
  (<- spelled (reply-json reply))
  (= spelled (.get queue.replies beat.name)))


(defk await-first-request [queue timeout-seconds]
  {:pre [(: queue RequestQueue) (: timeout-seconds (| float int))] :post [(: % (| bool str None))]
   :tags {:context "coordinator" :role "protocol"}}
  "列が空なら、送り手が積む(enqueue-request が呼び鈴を鳴らす)か timeout 秒が過ぎるまで 1 回だけ眠るため(読み直さない)。列に何か
   在れば眠らない。起きた時(時間切れ・取り消しを含む)は自分の呼び鈴を取り手の list から外す。答え = True(積まれた)・False
   (nudge-takers — 要求ではない出来事)・REPLAN(預けた仮の拍が変わった)・None(時間切れか、眠らなかった)。"
  (var woke None)
  (when (and (not queue.pending) (> timeout-seconds 0))
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


(defk note-absorbed [queue steps before]
  {:pre [(: queue RequestQueue) (: steps tuple) (: before tuple)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "区間の歩が期限を引き直した名指しの待ち(前の歩の同じ札の待ちと期限が違う物)を、引き直した刻と一緒に列へ覚えるため — 吸った待ちの
   送り手が、返事の打ち切りを送り直しの刻から数え直す(await-answer)。before = 区間の前の待ち。"
  (var prior before)
  (for [step steps]
    (for [watcher step.watchers]
      (val was (next (gfor w prior :if (is w.request.slot watcher.request.slot) w.deadline-ms) None))
      (when (and (is-not was None) (!= was watcher.deadline-ms) (isinstance watcher.request.slot Promise))
        (setv (get queue.absorbed (id watcher.request.slot)) (AbsorbedWatch :slot watcher.request.slot :at step.at))))
    (:= prior step.watchers))
  None)


(defk cut-at-marks [queue start stretch]
  {:pre [(: queue RequestQueue) (: start QuietStep) (: stretch QuietStretch)] :post [(: % QuietStretch)]
   :tags {:context "coordinator" :role "protocol"}}
  "落ちの注入(次の Persist で落とす — local.hy の CrashCoordinator)が待っている間は、生存の印を書く最初の歩で区間を切るため。その歩を
   本物の歩として回すので、1 拍ずつの走りと同じ書きの刻で落ちる(静かな歩のうち置き場へ書くのは生存の印の歩だけ)。"
  (if (not queue.ends-at-marks)
      stretch
      (do (val marked (next (gfor step stretch.steps :if step.marked step) None))
          (if (is marked None)
              stretch
              (QuietStretch :steps (tuple (gfor step stretch.steps :if (< step.at marked.at) step)) :end-at marked.at)))))


(defk pending-beats [queue steps]
  {:pre [(: queue RequestQueue) (: steps tuple)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "預けた仮の拍のうち、試した歩 steps がまだ受けていない拍(ProvisionalBeat の tuple — 刻の順)を知るため(区間を試し直す起点の材料)。"
  (val heard (frozenset (gfor step steps beat step.beats (id beat))))
  (tuple (gfor held queue.beats :if (not-in (id held.beat) heard) held.beat)))


(defk ring-beats-at [queue at]
  {:pre [(: queue RequestQueue) (: at int)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "区間の終わりの刻 at に届くはずだった仮の拍を、その worker に本物の heartbeat として送らせるため(その宿の預けを全部外して鈴を BEAT で
   鳴らす — 宿はその刻に起きて拍を打ち、後の拍は起きた後に預け直す)。答え = 起こした worker の名。"
  (val due (tuple (gfor held queue.beats :if (= held.beat.at at) held)))
  (setv queue.beats (lfor held queue.beats :if (not (any (gfor other due (is other.bell held.bell)))) held))
  (for [held due]
    (<- (ring-bell held.bell BEAT)))
  (tuple (gfor held due held.beat.name)))


(defk await-peers [queue names seconds]
  {:pre [(: queue RequestQueue) (: names tuple) (: seconds float)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "起こした worker の名 names の要求が全部 列に積まれるまで(上限 seconds 秒)待つため — 列に他の要求が在っても眠る(取り手の呼び鈴は
   積まれるたびに鳴るので、積まれた刻に確かめ直す。預けの取り下げの REPLAN でも確かめ直す)。"
  (<- started int (now-epoch-ms))
  (var going True)
  (while going
    (if (all (gfor name names (any (gfor request queue.pending (= request.peer name)))))
        (:= going False)
        (do (<- now int (now-epoch-ms))
            (val left (- (+ started (int (* 1000 seconds))) now))
            (if (<= left 0)
                (:= going False)
                (do (<- bell Promise (CreatePromise))
                    (.append queue.takers bell)
                    (try
                      (<- (promise-or-timeout bell.future (/ left 1000.0)))
                      (finally
                        (when (in bell queue.takers)
                          (.remove queue.takers bell)))))))))
  None)


(defk heartbeats-at [queue at]
  {:pre [(: queue RequestQueue) (: at int)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "区間が刻 at で終わる時(要求・外の出来事・静かでない歩・上限のどれでも)に、その刻に届くはずだった仮の拍をその worker に本物の
   heartbeat として送らせ、積まれるのを待つため — 調停ループが 1 拍ずつの走りと同じく、同じ刻の要求と 1 つの歩で受ける(#2850)。"
  (<- names tuple (ring-beats-at queue at))
  (when names
    (<- (await-peers queue names (/ TICK-MS 1000.0)))
    ;; 要求で起きた取り手と同じく 0 秒の眠りをはさむ(await-first-request): 同じ刻に先に登録された timer(他の worker の拍)を先に
    ;; 回し、その要求も同じ取りに入れる(1 拍ずつの走りの取りと同じまとまり — #2850 の系全体の静かな区間の 30.1 秒の w2)。
    (<- (Delay 0.0)))
  None)


(defk follow-stretch [queue probe started cut-at]
  {:pre [(: queue RequestQueue) (: probe IdleProbe) (: started int) (: cut-at (| int None))] :post [(: % tuple)]
   :tags {:context "coordinator" :role "protocol"}}
  "取り手が、静かな区間を本番の判断(idle_policy.quiet-stretch)で試しながら一度に眠り、起きた刻より前の歩(QuietStep の tuple — 1 拍
   ずつの走りが下したはずの歩)を返すため。区間は 1 拍・2 拍・4 拍…と倍に伸ばして試す(要求が早く来た時に、来なかった先の歩を試す
   費用を払わない)。worker の宿が預けた仮の拍は、その刻の歩で受けて試す(#2790)。起き方: 要求が積まれた刻・最初の静かでない歩の刻
   (その歩は調停ループが本物の歩として回す — 仮の拍の刻なら、その worker を起こして本物の heartbeat を待つ)・要求ではない出来事の後の
   最初の歩の刻(cut-at — 本番の 1 秒の拍がその出来事に気づく歩)・上限 MAX-QUIET-MS。預けた仮の拍が変わったら(REPLAN)、起きた刻より
   前の歩を残して試し直す。"
  (val origin (QuietStep :at started :state probe.state :watchers probe.watchers :marked False))
  (var steps #())
  (var end None)
  (var until cut-at)
  (var chunk TICK-MS)
  (var horizon (+ started TICK-MS))
  (var taken None)
  (while (is taken None)
    (val last (if steps (get steps -1) origin))
    (when (and (is end None) (< last.at horizon))
      (<- pending tuple (pending-beats queue steps))
      (<- tried QuietStretch (quiet-stretch (replace probe :beats pending) last horizon (fn [beat reply] (same-reply queue beat reply))))
      (<- stretch QuietStretch (cut-at-marks queue last tried))
      (<- (note-absorbed queue stretch.steps last.watchers))
      (:= steps (+ steps stretch.steps))
      (:= end stretch.end-at))
    ;; 要求ではない出来事の後: その刻以後の最初の歩(試した歩か、試した最後の歩の次の歩)を本物の歩にする。
    (when (is-not until None)
      (val later (next (gfor step steps :if (>= step.at until) step.at) None))
      (<- unheard tuple (pending-beats queue steps))
      (<- after int (next-step-at (if steps (get steps -1) origin) unheard))
      (:= end (cond (is-not later None) later (is-not end None) end True after))
      (:= steps (tuple (gfor step steps :if (< step.at end) step))))
    (<- now int (now-epoch-ms))
    (val target (if (is end None) horizon end))
    (var woke None)
    (when (> target now)
      (<- answer (| bool str None) (await-first-request queue (/ (- target now) 1000.0)))
      (:= woke answer))
    (<- at int (now-epoch-ms))
    (cond
      ;; 要求が積まれた(区間を試している間に積まれた要求は呼び鈴を鳴らさない — 列に在れば眠らずに返す await-first-request の答えは
      ;; None なので、列を見て要求で起きたと数える)。
      (or (is woke True) queue.pending) (do (<- (heartbeats-at queue at))
                                            (:= taken (tuple (gfor step steps :if (< step.at at) step))))
      (is woke False) (:= until at)
      ;; 預けた仮の拍が変わった: 起きた刻より前の歩を残して試し直す。
      (= woke REPLAN) (do (:= steps (tuple (gfor step steps :if (< step.at at) step)))
                          (:= end None))
      (and (is-not end None) (>= at end))
        (do (<- (heartbeats-at queue end))
            (:= taken steps))
      ;; 上限の刻の歩は、静かでも本物の歩として回す。
      (>= (- horizon started) MAX-QUIET-MS) (do (:= end horizon)
                                                (:= steps (tuple (gfor step steps :if (< step.at end) step))))
      True (do (:= chunk (* 2 chunk))
               (:= horizon (min (+ horizon chunk) (+ started MAX-QUIET-MS)))))
    ;; 今の試しが静かと判じた仮の拍(宿が拍ごとに届いたものとして写す材料)。
    (setv queue.planned (lfor step steps beat step.beats beat)))
  ;; 返す歩が受けた仮の拍は預けから外し、届いた物として宿が写すまで覚える(調停ループが歩ごとに保存する)。覚えるのは宿がまだ写して
  ;; いない刻の拍だけ — 既に写した拍(今の区間の試しで写した拍・起きた宿が残りとしてまとめて写した拍)を宿は二度と問わず、覚えると
  ;; 外す者が無い(#2769)。
  (val heard-beats (tuple (gfor step taken beat step.beats beat)))
  (val heard (frozenset (gfor beat heard-beats (id beat))))
  (setv queue.beats (lfor held queue.beats :if (not-in (id held.beat) heard) held))
  (.extend queue.consumed (gfor beat heard-beats :if (or (not-in beat.name queue.settled) (> beat.at (get queue.settled beat.name))) beat))
  (setv queue.planned [])
  taken)


(defk beat-heard [queue beat]
  {:pre [(: queue RequestQueue) (: beat ProvisionalBeat)] :post [(: % bool)] :tags {:context "coordinator" :role "protocol"}}
  "仮の拍 beat を列が静かと判じたか(今の区間の試しの歩に在るか、調停ループへ渡したか)を知るため — 宿が眠りの拍ごとに、届いたものと
   して宿の真実へ写してよいかを判じる(静かでない拍は BEAT で起こされ、本物で打つ)。"
  (or (any (gfor held queue.planned (is held beat))) (any (gfor held queue.consumed (is held beat)))))


(defk forget-heard [queue beats]
  {:pre [(: queue RequestQueue) (: beats tuple)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "宿が宿の真実へ写した仮の拍 beats を、その宿の写し終えた刻(settled)として覚え、届いた拍の覚え(consumed)からその刻までの拍を
   外すため — 宿は預けの拍を刻の順に写し、写し終えた刻より後の拍だけを問うので、それまでの拍(写した拍と、同じ宿の前の預けの残り)は
   もう問われない。"
  ;; 写し終えた刻は遅い方を残す(初めて写した宿は、その拍の刻)。
  (for [beat beats]
    (setv (get queue.settled beat.name) (max beat.at (.get queue.settled beat.name beat.at))))
  (when beats
    (setv queue.consumed (lfor held queue.consumed :if (or (not-in held.name queue.settled) (> held.at (get queue.settled held.name))) held)))
  None)


(defk await-idle [queue probe]
  {:pre [(: queue RequestQueue) (: probe IdleProbe)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "要求の無い間の眠りを、静かな区間(idle_policy.quiet-stretch — 本番の判断の関数で試した歩)の分だけ一度に取り、眠った間の歩を返すため
   (#2790)。要求が積まれればすぐ起きる(本番と同じ刻)。仮の拍が預けられていなければ、区間を試すのは要求の来ないまま最初の 1 拍が
   過ぎた時だけ(要求が 1 秒より短い間隔で続く系では、飛ばせる歩が無いのに状態の大きい判断を歩ごとに試すことになり、1 秒ごとの歩より
   遅くなった — 使い手の模擬の全体の検の実測 49.5 秒 → 60 秒超)。最初の 1 拍のうちに要求ではない出来事で起こされたら、その刻以後の
   最初の歩を本物の歩にする(本番の 1 秒の拍がその出来事に気づく歩)。仮の拍が在れば、その刻の歩を逃さないよう最初から試す。"
  (<- started int (now-epoch-ms))
  (var woke None)
  (when (not queue.beats)
    (<- answer (| bool str None) (await-first-request queue (/ TICK-MS 1000.0)))
    (:= woke answer))
  (<- now int (now-epoch-ms))
  (var steps #())
  (when (and (is-not woke True) (not queue.pending))
    (<- slept tuple (follow-stretch queue probe started (if (is woke False) now None)))
    (:= steps slept))
  steps)


(defk take-requests [queue timeout-seconds limit idle]
  {:pre [(: queue RequestQueue) (: timeout-seconds float) (: limit int) (: idle (| IdleProbe None))] :post [(: % (| list IdleTaken))]
   :tags {:context "coordinator" :role "protocol"}}
  "本番の http-requests と同じ意味で列から要求を取るため: 最初の 1 件を timeout 秒まで待ち、その時点で並んでいる要求を limit 件まで
   一緒に取る。待ちは列への書き(enqueue-request)で起きる — 本番の受付が要求の届いた瞬間に起きるのと同じ刻。skip-idle の列(模擬の
   時計の下)は、idle の材料があれば、要求が無い間の静かな区間を一度に眠り、眠った間の歩を添えて返す(IdleTaken — await-idle)。
   要求の無いまま起きた時は、0 秒の Delay を 1 回はさむ: 同じ仮想の刻に来る出来事(筋書きの止めの注入など)を先に全部通してから拍を
   回す。はさまないと、同じ刻の出来事と拍の順が時計の timer の登録順で決まり、1 秒ごとの拍と飛ばす拍で順が違う(拍の timer の有無が
   違うため — 2026-09-30 のレビューの再現)。"
  (var steps #())
  (if (and queue.skip-idle (is-not idle None) (not queue.pending))
      (do (<- slept tuple (await-idle queue idle))
          (:= steps slept))
      (<- (await-first-request queue timeout-seconds)))
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
  (if steps (IdleTaken :steps steps :batch batch) batch))


(defk taken-batch [taken]
  {:pre [(: taken (| list IdleTaken))] :post [(: % list)] :tags {:context "coordinator" :role "protocol"}}
  "列の答え(要求の list か、眠った区間の歩を添えた IdleTaken)から、取った要求の list を読むため(取った要求を篩う見張りが読む)。"
  (match taken
    (IdleTaken) taken.batch
    _ taken))


(defk await-answer [queue slot seconds]
  {:pre [(: queue RequestQueue) (: slot Promise) (: seconds float)] :post [(: % (| tuple None))]
   :tags {:context "coordinator" :role "protocol"}}
  "送り手が、列に積んだ要求の返事を seconds 秒(本番の HTTP の client の打ち切り)まで待つため。区間の中で吸った名指しの待ち
   (AbsorbedWatch)の打ち切りは、1 拍ずつの走りで送り手が同じ問いを送り直した刻から数え直す — 1 拍ずつの走りでは 10 秒ごとに返事と
   送り直しがあり、打ち切りは来ない。答え = 返事か、時間切れの None。"
  (<- first (| tuple None) (promise-or-timeout slot.future seconds))
  (var answer first)
  (var going (is first None))
  (while going
    (<- now int (now-epoch-ms))
    (val held (.get queue.absorbed (id slot)))
    (val until (if (and (is-not held None) (is held.slot slot)) (+ held.at (int (* 1000 seconds))) None))
    (if (and (is-not until None) (> until now))
        (do (<- again (| tuple None) (promise-or-timeout slot.future (/ (- until now) 1000.0)))
            (:= answer again)
            (:= going (is again None)))
        (:= going False)))
  (.pop queue.absorbed (id slot) None)
  answer)


(defhandler queued-requests [#^ RequestQueue queue]
  ;; 節は上から isinstance で当てるので、材料 idle を持つ子 class(coordinator の調停ループが出す)を先に置き、record-store などが出す
  ;; 素の NextRequests は材料なしで取る(#2180)。
  (IdleNextRequests [timeout-seconds limit idle]
    (<- taken (| list IdleTaken) (take-requests queue timeout-seconds limit idle))
    (resume taken))
  (NextRequests [timeout-seconds limit]
    (<- batch (| list IdleTaken) (take-requests queue timeout-seconds limit None))
    (resume batch))
  (Reply [request status body]
    ;; この組の要求の列は、返事の札を CreatePromise で作る(Request.slot は受け口ごとの札 — HTTP の受け口は ReplySlot)。
    ;; 札が Promise でなければ、別の受け口の要求がこの組に来た誤り — 返事を落とさず名指して落ちる。
    (when (not (isinstance request.slot Promise))
      (raise (TypeError (.format "返事の札が Promise でない({}): {} {}" (type request.slot) request.method request.path))))
    ;; 返事をした待ちは、もう吸っていない(送り手は返事を受ける)。
    (.pop queue.absorbed (id request.slot) None)
    ;; worker が最後に受けた heartbeat の返事を覚える(仮の拍の返事が同じかを比べる — same-reply)。
    (when (and (= status 200) (= request.path "/heartbeat") (isinstance request.actor str))
      (setv (get queue.replies request.actor) body))
    (<- (CompletePromise request.slot #(status body)))
    (resume None))
  (CoordinatorFault [fault]
    (.append queue.faults fault)
    (resume None)))
