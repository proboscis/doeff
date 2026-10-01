;;; coordinator の要求の受付の、まねた環境の言い換え — process の中の列(RequestQueue)で intent の NextRequests / IdleNextRequests・
;;; Reply・CoordinatorFault に答える(本番の答え手は shared/protocol/inbox.hy の http-requests と coordinator/protocol/faults.hy の coordinator-faults)。handler の組
;;; (coordinator/entry/handler_sets.hy の emulated-handlers)が並べる。entry の層から移した(DOEFF105)。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_time [Delay])
;; 模擬の列は、要求の無い間に眠る長さを本番の判断の関数で試す(idle_policy)。
(import doeff_cluster.coordinator.core.idle_policy [quiet-ticks rest-to-tick])
(import doeff_cluster.coordinator.core.api_policy [TICK-MS])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Promise])
(import doeff_cluster.shared.intent.protocol [Request Reply])
(import doeff_cluster.coordinator.intent.cluster_model [IdleProbe IdleNextRequests CoordinatorFault] doeff_cluster.shared.intent.protocol [NextRequests])
(import doeff_cluster.shared.core.promise_wait [promise-or-timeout])


(defclass RequestQueue []
  "process の中の要求の列(HTTP の受付の代わり)。送り手は Request の slot に doeff の Promise を入れて並べ、Wait で返事
   #(status 本文)を受ける。up = 受け付けているか(coordinator の process が止まっている間は偽 — 送り手は接続の失敗として扱う)。
   bells = 切り離した task の key → 呼び鈴(doeff の Promise)の tuple。送り手が task の終わりを読み直さずに待つため、読む前に掛ける。
   模擬の coordinator の Persist の見張り(local.hy の observe-requests)が、その key の task の終わりの phase を書いた時に鳴らす。
   takers = 列の取り手(queued-requests の NextRequests)が、列が空の間に掛けた呼び鈴(doeff の Promise の list — 掛けた順)。送り手が
   列に積んだ時(enqueue-request)に全部鳴らして外す。列は読み直さない(前は仮想の 0.05 秒ごとに見直していた — 使い手の仮想の
   1700 秒の検で 37,222 回眠り、所要の大半になった)。
   faults = coordinator の中の欠陥の log の行(CoordinatorFault の Fault — 出た順)。本番の受付が stderr へ出す 1 行の代わり。
   skip-idle = 要求が無い間、調停が何も変えない拍の数だけ一度に眠るか(idle_policy.quiet-ticks — 模擬の時計の下の入口だけが真に
   する・2026-09-30。偽なら本番と同じく timeout 秒ごとに起きる)。takes = 取り手が取った回数(coordinator の拍の数 — 検が読む)。"
  (defn #^ None __init__ [self #^ bool [skip-idle False]]
    (setv self.pending [] self.up False self.bells {} self.takers [] self.faults [] self.skip-idle skip-idle self.takes 0)
    None))


(defk enqueue-request [queue request]
  {:pre [(: queue RequestQueue) (: request Request)] :post [(: % None)] :tags {:context "doeff-cluster" :role "protocol"}}
  "要求を列の後ろに積み、列が空の間に待っていた取り手の呼び鈴を全部鳴らして外すため(取り手は積んだのと同じ仮想の刻で起きる)。
   積む順 = 取る順(列は先頭から取る)。鳴らすのは積んだ後 — 起きた取り手は必ず積んだ要求を見る。"
  (.append queue.pending request)
  (val waiting (tuple queue.takers))
  (.clear queue.takers)
  (for [bell waiting]
    (<- (CompletePromise bell True)))
  None)


(defk nudge-takers [queue]
  {:pre [(: queue RequestQueue)] :post [(: % None)] :tags {:context "doeff-cluster" :role "protocol"}}
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
   :tags {:context "doeff-cluster" :role "protocol"}}
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


(defk await-idle [queue probe]
  {:pre [(: queue RequestQueue) (: probe IdleProbe)] :post [(: % None)] :tags {:context "doeff-cluster" :role "protocol"}}
  "要求の無い間の眠りを、調停が何も変えない拍の数(idle_policy.quiet-ticks — 本番の判断の関数で試した数)だけ一度に取るため。
   要求が積まれればすぐ起きる(本番と同じ刻)。要求ではない出来事で起こされたら、本番の 1 秒の拍がそれに気づく刻(眠り始めから
   整数秒 — 1 秒以上)まで眠り直す。飛ばした拍は本番でも何も変えないので、起きる刻とそこでの判断は 1 秒ごとの拍と同じ。
   判断を試すのは、要求の来ないまま最初の 1 拍が過ぎた時だけ(要求が 1 秒より短い間隔で続く系では、飛ばせる拍が無いのに状態の
   大きい判断を拍ごとに試すことになり、1 秒ごとの拍より遅くなった — 使い手の模擬の全体の検の実測 49.5 秒 → 60 秒超)。要求では
   ない出来事で起こされた後は試さない(本番の 1 秒の拍が、その出来事に気づく拍で返す)。"
  (<- started int (now-epoch-ms))
  (var limit-ms TICK-MS)
  (var probed False)
  (var nudged False)
  (var left-ms TICK-MS)
  (while (and (not queue.pending) (> left-ms 0))
    (<- woke (| bool None) (await-first-request queue (/ left-ms 1000.0)))
    (<- now int (now-epoch-ms))
    (val elapsed (- now started))
    (cond
      (is woke False) (do (<- rest int (rest-to-tick elapsed limit-ms))
                          (:= nudged True)
                          (:= left-ms rest))
      (and (is woke None) (not probed) (not nudged) (not queue.pending))
        (do (<- quiet int (quiet-ticks probe started))
            (:= probed True)
            (:= limit-ms (* TICK-MS quiet))
            (:= left-ms (- limit-ms elapsed)))
      True (:= left-ms 0)))
  None)


(defk take-requests [queue timeout-seconds limit idle]
  {:pre [(: queue RequestQueue) (: timeout-seconds float) (: limit int) (: idle (| IdleProbe None))] :post [(: % list)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "本番の http-requests と同じ意味で列から要求を取るため: 最初の 1 件を timeout 秒まで待ち、その時点で並んでいる要求を limit 件まで
   一緒に取る。待ちは列への書き(enqueue-request)で起きる — 本番の受付が要求の届いた瞬間に起きるのと同じ刻。skip-idle の列(模擬の
   時計の下)は、idle の材料があれば、要求が無い間の何も変えない拍を一度に眠る(await-idle)。
   要求の無いまま起きた時は、0 秒の Delay を 1 回はさむ: 同じ仮想の刻に来る出来事(筋書きの止めの注入など)を先に全部通してから拍を
   回す。はさまないと、同じ刻の出来事と拍の順が時計の timer の登録順で決まり、1 秒ごとの拍と飛ばす拍で順が違う(拍の timer の有無が
   違うため — 2026-09-30 のレビューの再現)。"
  (if (and queue.skip-idle (is-not idle None) (not queue.pending))
      (<- (await-idle queue idle))
      (<- (await-first-request queue timeout-seconds)))
  (when (not queue.pending)
    (<- (Delay 0.0)))
  (val batch (cut queue.pending 0 limit))
  (setv queue.pending (cut queue.pending limit None))
  (+= queue.takes 1)
  batch)


(defhandler queued-requests [#^ RequestQueue queue]
  ;; 節は上から isinstance で当てるので、材料 idle を持つ子 class(coordinator の調停ループが出す)を先に置き、record-store などが出す
  ;; 素の NextRequests は材料なしで取る(#2180)。
  (IdleNextRequests [timeout-seconds limit idle]
    (<- batch list (take-requests queue timeout-seconds limit idle))
    (resume batch))
  (NextRequests [timeout-seconds limit]
    (<- batch list (take-requests queue timeout-seconds limit None))
    (resume batch))
  (Reply [request status body]
    ;; この組の要求の列は、返事の札を CreatePromise で作る(Request.slot は受け口ごとの札 — HTTP の受け口は ReplySlot)。
    ;; 札が Promise でなければ、別の受け口の要求がこの組に来た誤り — 返事を落とさず名指して落ちる。
    (when (not (isinstance request.slot Promise))
      (raise (TypeError (.format "返事の札が Promise でない({}): {} {}" (type request.slot) request.method request.path))))
    (<- (CompletePromise request.slot #(status body)))
    (resume None))
  (CoordinatorFault [fault]
    (.append queue.faults fault)
    (resume None)))
