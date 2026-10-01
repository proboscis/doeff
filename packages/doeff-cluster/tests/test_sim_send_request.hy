;; 模擬の送り手(sim.local の send-request)は、本番の HTTP の client と同じ REPLY-SECONDS までしか返事を待たない。
;;
;; - 上限の内に返った返事は、そのまま送り手へ渡る。
;; - 受けたまま返事をしない coordinator の前では、REPLY-SECONDS ちょうどで途中で切れた失敗 #(None …) を返す(待ち続けない)。
;;
;; 前の形(返事を上限なしで待つ)は、2 本目で 20 秒後の 200 をそのまま受けて赤になる。上限なしの待ちは、拍の判断が落ち続ける
;; coordinator の前で worker の拍と止めの手順を止め、模擬が仮想の時計を回し続けた(使い手の検が 60 秒の上限に当たった・#2596)。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_core_effects.scheduler [Spawn Task Wait CompletePromise])
(import doeff_time [Delay sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue])
(import doeff_cluster.foundation.coordinator_http [REPLY-SECONDS])
(import doeff_cluster.sim.local [SimLink send-request])
(import tests.clock_fixtures [clock-at])


(defk answer-after [queue seconds]
  {:pre [(: queue RequestQueue) (: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの coordinator の代役: seconds 秒眠ってから、列の先頭の要求に 200 で答える(受けた刻ではなく遅れて答える受け口)。"
  (<- (Delay seconds))
  (val request (.pop queue.pending 0))
  (<- (CompletePromise request.slot #(200 {"ok" True})))
  None)


(defk send-and-read [link]
  {:pre [(: link SimLink)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "送り手: heartbeat を 1 件送り、返事と返った刻(起点からの仮想のミリ秒)を返す。"
  (<- answer tuple (send-request link "POST" "/heartbeat" {} {}))
  (<- at int (now-epoch-ms))
  #(answer at))


(defk send-against [reply-seconds]
  {:pre [(: reply-seconds float)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "受け付けている(up)列に、reply-seconds 秒後に答える代役を並べて送り、送り手の読み #(返事 刻) を返すため。"
  (val queue (RequestQueue))
  (setv queue.up True)
  (val link (SimLink :queue queue :actor "worker-1" :revision "sim" :peer "worker-1"))
  (<- read tuple ((sim-time-handler :clock (clock-at 0)) (send-beside-answer link queue reply-seconds)))
  read)


(defk send-beside-answer [link queue reply-seconds]
  {:pre [(: link SimLink) (: queue RequestQueue) (: reply-seconds float)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "送り手と遅れて答える代役を並べて回し、両方の終わりを待つため。答え = 送り手の読み。"
  (<- sending Task (Spawn (send-and-read link)))
  (<- answering Task (Spawn (answer-after queue reply-seconds)))
  (<- got tuple (Wait sending))
  (<- (Wait answering))
  got)


(deftest test-a-reply-inside-the-limit-passes-through-unchanged
  (<- read tuple (send-against 2.0))
  (assert (= read #(#(200 {"ok" True}) 2000)) read))


(deftest test-an-unanswered-request-is-cut-off-at-the-reply-limit
  ;; 代役は 20 秒後に答える(REPLY-SECONDS の後)。送り手は REPLY-SECONDS ちょうどで途中で切れた失敗を受け、遅い 200 は受けない。
  (assert (< REPLY-SECONDS 20.0) REPLY-SECONDS)
  (<- read tuple (send-against 20.0))
  (val answer (get read 0))
  (val at (get read 1))
  (assert (is (get answer 0) None) read)
  (assert (in "返事が" (get (get answer 1) "error")) read)
  (assert (= at (int (* REPLY-SECONDS 1000))) read))
