;;; coordinator の要求の受付の、まねた環境の言い換え — process の中の列(RequestQueue)で intent の NextRequests・Reply・
;;; CoordinatorFault に答える(本番の答え手は shared/protocol/inbox.hy の http-requests と coordinator/protocol/faults.hy の coordinator-faults)。handler の組
;;; (coordinator/entry/handler_sets.hy の emulated-handlers)が並べる。entry の層から移した(DOEFF105)。
;;; 取りは本番の受付と同じく、要求が積まれるか、渡された待ちの秒(None = 期限なし)が過ぎるか、外の出来事(nudge-takers)で起きる。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "coordinator" :role "protocol"})
(import dataclasses [dataclass])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_time [Delay])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Promise])
(import doeff_cluster.shared.intent.protocol [Request Reply])
(import doeff_cluster.coordinator.intent.cluster_model [CoordinatorFault]
        doeff_cluster.shared.intent.protocol [NextRequests])
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
   takes = 取り手が取った回数(coordinator の歩の数 — 検が読む)。crash-waiting = 落ちの注入(次の Persist で落とす — local.hy の
   CrashCoordinator)が待っているか(世界が立て、落ちで下ろす)。模擬の coordinator の書きの見張り(local.hy の observe-requests)は、
   これが偽の書きでは落ちの判断を世界へ問わない(#3132)。arrivals = 積んだ要求の id → 積んだ刻(同じ刻の要求を送り手の名の順に取る)。"
  (defn #^ None __init__ [self]
    (setv self.pending [] self.up False self.bells {} self.takers [] self.faults [] self.takes 0 self.crash-waiting False
          self.arrivals {})
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


(defk await-first-request [queue timeout-seconds]
  {:pre [(: queue RequestQueue) (: timeout-seconds (| float int None))] :post [(: % (| bool None))]
   :tags {:context "coordinator" :role "protocol"}}
  "列が空なら、送り手が積む(enqueue-request が呼び鈴を鳴らす)か timeout 秒(None = 期限なし)が過ぎるまで 1 回だけ眠るため(読み
   直さない)。列に何か在れば眠らない。起きた時(時間切れ・取り消しを含む)は自分の呼び鈴を取り手の list から外す。答え = True
   (積まれた)・False(nudge-takers — 要求ではない出来事)・None(時間切れか、眠らなかった)。"
  (var woke None)
  (when (and (not queue.pending) (or (is timeout-seconds None) (> timeout-seconds 0)))
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


(defk take-requests [queue timeout-seconds limit]
  {:pre [(: queue RequestQueue) (: timeout-seconds (| float None)) (: limit int)] :post [(: % list)]
   :tags {:context "coordinator" :role "protocol"}}
  "本番の http-requests と同じ意味で列から要求を取るため: 最初の 1 件を timeout 秒まで(None = 期限なし)待ち、その時点で並んでいる
   要求を limit 件まで一緒に取る。待ちは列への書き(enqueue-request)で起きる — 本番の受付が要求の届いた瞬間に起きるのと同じ刻。外の
   出来事(nudge-takers)では要求の無いまま返る。要求の無いまま返る時は、0 秒の Delay を 1 回はさむ: 同じ仮想の刻に来る出来事(筋書きの
   止めの注入など)を先に全部通してから歩を回す(はさまないと、同じ刻の出来事と歩の順が時計の timer の登録順で決まる — 2026-09-30 の
   レビューの再現)。"
  (<- started int (now-epoch-ms))
  (val until (if (is timeout-seconds None) None (+ started (round (* 1000 timeout-seconds)))))
  (var going True)
  (while going
    (<- now int (now-epoch-ms))
    (cond
      queue.pending (:= going False)
      (and (is-not until None) (>= now until)) (:= going False)
      True (do (<- woke (| bool None) (await-first-request queue (if (is until None) None (/ (- until now) 1000.0))))
               (when (is woke False)
                 (:= going False)))))
  (when (not queue.pending)
    (<- (Delay 0.0)))
  ;; 同じ仮想の刻に届いた要求は送り手の名の順に並べる(同じ送り手の中の順は保つ — 並べ替えは安定)。模擬の時計では、同じ刻に起きる
  ;; task の順が timer を登録した順で決まる。本番の同じ刻の到着の順は決まっておらず、名の順はその 1 つの並び。
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
    (<- (CompletePromise request.slot #(status body)))
    (resume None))
  (CoordinatorFault [fault]
    (.append queue.faults fault)
    (resume None)))
