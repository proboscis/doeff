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
(import doeff_cluster.coordinator.core.idle_policy [quiet-stretch rest-to-tick MAX-QUIET-MS])
(import doeff_cluster.coordinator.core.api_policy [TICK-MS])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Promise])
(import doeff_cluster.shared.intent.protocol [Request Reply])
(import doeff_cluster.coordinator.intent.cluster_model [IdleProbe IdleNextRequests IdleTaken QuietStep QuietStretch CoordinatorFault]
        doeff_cluster.shared.intent.protocol [NextRequests])
(import doeff_cluster.shared.core.promise_wait [promise-or-timeout])


(defrecord AbsorbedWatch
  "区間の中で吸った名指しの待ち 1 件(#2790): slot = 待ちの要求の返事の札(送り手が待つ Promise)・at = 期限を最後に引き直した刻(1 拍ずつの
   走りで、送り手が「変わっていない」の返事を受けて同じ問いを送り直した刻)。送り手の返事の打ち切りはこの刻から数え直す(await-answer)。"
  (#^ Promise slot)
  (#^ int at))


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
   書く最初の歩で区間を切るか(落ちの注入が次の Persist を待つ間だけ真 — local.hy の CrashCoordinator が立て、落ちで下ろす)。"
  (defn #^ None __init__ [self #^ bool [skip-idle False]]
    (setv self.pending [] self.up False self.bells {} self.takers [] self.faults [] self.skip-idle skip-idle self.takes 0
          self.absorbed {} self.ends-at-marks False)
    None))


(defk enqueue-request [queue request]
  {:pre [(: queue RequestQueue) (: request Request)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "要求を列の後ろに積み、列が空の間に待っていた取り手の呼び鈴を全部鳴らして外すため(取り手は積んだのと同じ仮想の刻で起きる)。
   積む順 = 取る順(列は先頭から取る)。鳴らすのは積んだ後 — 起きた取り手は必ず積んだ要求を見る。"
  (.append queue.pending request)
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


(defk await-first-request [queue timeout-seconds]
  {:pre [(: queue RequestQueue) (: timeout-seconds (| float int))] :post [(: % (| bool None))]
   :tags {:context "coordinator" :role "protocol"}}
  "列が空なら、送り手が積む(enqueue-request が呼び鈴を鳴らす)か timeout 秒が過ぎるまで 1 回だけ眠るため(読み直さない)。列に何か
   在れば眠らない。起きた時(時間切れ・取り消しを含む)は自分の呼び鈴を取り手の list から外す。答え = True(積まれた)・False
   (nudge-takers — 要求ではない出来事)・None(時間切れか、眠らなかった)。"
  (var woke None)
  (when (and (not queue.pending) (> timeout-seconds 0))
    (<- bell Promise (CreatePromise))
    (.append queue.takers bell)
    (try
      (<- answer (| bool None) (promise-or-timeout bell.future timeout-seconds))
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


(defk rest-to-first-tick [queue started]
  {:pre [(: queue RequestQueue) (: started int)] :post [(: % None)] :tags {:context "coordinator" :role "protocol"}}
  "最初の 1 拍のうちに要求ではない出来事(止めの注入など)で起こされた取り手が、本番の 1 秒の拍がそれに気づく刻(眠り始めの 1 拍後)まで
   眠り直すため。要求が積まれればすぐ起きる。"
  (var going True)
  (while going
    (<- now int (now-epoch-ms))
    (<- rest int (rest-to-tick (- now started) TICK-MS))
    (if (> rest 0)
        (do (<- woke (| bool None) (await-first-request queue (/ rest 1000.0)))
            (when (is-not woke False)
              (:= going False)))
        (:= going False)))
  None)


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


(defk follow-stretch [queue probe started]
  {:pre [(: queue RequestQueue) (: probe IdleProbe) (: started int)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "要求の来ないまま最初の 1 拍が過ぎた取り手が、静かな区間を本番の判断(idle_policy.quiet-stretch)で試しながら一度に眠り、起きた刻より
   前の歩(QuietStep の tuple — 1 拍ずつの走りが下したはずの歩)を返すため。区間は 1 拍・2 拍・4 拍…と倍に伸ばして試す(要求が早く来た
   時に、来なかった先の歩を試す費用を払わない)。起き方: 要求が積まれた刻・最初の静かでない歩の刻(その歩は調停ループが本物の歩として
   回す)・要求ではない出来事の後の最初の歩の刻(本番の 1 秒の拍がその出来事に気づく歩)・上限 MAX-QUIET-MS。"
  (var last (QuietStep :at started :state probe.state :watchers probe.watchers :marked False))
  (var steps #())
  (var end None)
  (var chunk TICK-MS)
  (var horizon (+ started TICK-MS))
  (var taken None)
  (while (is taken None)
    (when (and (is end None) (< last.at horizon))
      (<- tried QuietStretch (quiet-stretch probe last horizon))
      (<- stretch QuietStretch (cut-at-marks queue last tried))
      (<- (note-absorbed queue stretch.steps last.watchers))
      (:= steps (+ steps stretch.steps))
      (when stretch.steps
        (:= last (get stretch.steps -1)))
      (:= end stretch.end-at))
    (<- now int (now-epoch-ms))
    (val target (if (is end None) horizon end))
    (var woke None)
    (when (> target now)
      (<- answer (| bool None) (await-first-request queue (/ (- target now) 1000.0)))
      (:= woke answer))
    (<- at int (now-epoch-ms))
    (cond
      (is woke True) (:= taken (tuple (gfor step steps :if (< step.at at) step)))
      ;; 要求ではない出来事: その刻以後の最初の歩(試した歩か、試した最後の歩の次)を本物の歩にする。
      (is woke False) (do (val later (next (gfor step steps :if (>= step.at at) step.at) None))
                          (:= end (if (is later None) (+ last.at TICK-MS) later))
                          (:= steps (tuple (gfor step steps :if (< step.at end) step))))
      (and (is-not end None) (>= at end)) (:= taken steps)
      ;; 上限の刻の歩は、静かでも本物の歩として回す。
      (>= (- horizon started) MAX-QUIET-MS) (do (:= end horizon)
                                                (:= steps (tuple (gfor step steps :if (< step.at end) step))))
      True (do (:= chunk (* 2 chunk))
               (:= horizon (min (+ horizon chunk) (+ started MAX-QUIET-MS))))))
  taken)


(defk await-idle [queue probe]
  {:pre [(: queue RequestQueue) (: probe IdleProbe)] :post [(: % tuple)] :tags {:context "coordinator" :role "protocol"}}
  "要求の無い間の眠りを、静かな区間(idle_policy.quiet-stretch — 本番の判断の関数で試した歩)の分だけ一度に取り、眠った間の歩を返すため
   (#2790)。要求が積まれればすぐ起きる(本番と同じ刻)。区間を試すのは、要求の来ないまま最初の 1 拍が過ぎた時だけ(要求が 1 秒より
   短い間隔で続く系では、飛ばせる歩が無いのに状態の大きい判断を歩ごとに試すことになり、1 秒ごとの歩より遅くなった — 使い手の模擬の
   全体の検の実測 49.5 秒 → 60 秒超)。最初の 1 拍のうちに要求ではない出来事で起こされたら試さない(本番の 1 秒の拍が、その出来事に
   気づく拍で返す)。"
  (<- started int (now-epoch-ms))
  (<- woke (| bool None) (await-first-request queue (/ TICK-MS 1000.0)))
  (var steps #())
  (cond
    (is woke False) (<- (rest-to-first-tick queue started))
    (and (is woke None) (not queue.pending)) (do (<- slept tuple (follow-stretch queue probe started))
                                                 (:= steps slept)))
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
  (val batch (cut queue.pending 0 limit))
  (setv queue.pending (cut queue.pending limit None))
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
    (<- (CompletePromise request.slot #(status body)))
    (resume None))
  (CoordinatorFault [fault]
    (.append queue.faults fault)
    (resume None)))
