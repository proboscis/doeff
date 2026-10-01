;; 模擬の coordinator の要求の列(coordinator_handler_sets.RequestQueue と queued-requests)の待ちは読み直さず、列への書き
;; (enqueue-request)で起きる。
;;
;; - 列に書く前は取り手が起きない(期限まで眠る — 期限で起きた時は空のまとまり)。
;; - 書いたら、書いたのと同じ仮想の刻で起きる(前の形は仮想の 0.05 秒ごとに見直し、書きから最大 0.05 秒遅れて起きた)。
;; - 複数の書きの順が、取る順に保たれる(limit で分けて取っても)。
;;
;; 各の検は、同じ筋書きを反例の handler(前の見直しの形・待たずに答える形・後ろから取る形)でも回し、判定が赤になることを確かめる。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import doeff [with-handlers Program])
(import doeff_core_effects.scheduler [Spawn Task Wait])
(import doeff_time [Delay sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [NextRequests])
(import doeff_cluster.foundation.coordinator_inbox [http-request])
(import doeff_cluster.coordinator.entry.handler_sets [RequestQueue queued-requests enqueue-request])
(import tests.clock_fixtures [clock-at count-delays])


;; --- 反例の handler(判定が赤になることを確かめるための、正しくない列の取り方)---------------------------------------------

(defhandler polled-requests [#^ RequestQueue queue]
  ;; 引数に残す理由: 検が作った列そのものを読む(反例は本物の handler と同じ引数で差し替える)。
  ;; 反例 — 前の形: 列が空の間、仮想の 0.05 秒ごとに見直す(書きから最大 0.05 秒遅れて起き、眠りの数が期限 × 20 になる)。
  (NextRequests [timeout-seconds limit]
    (var waited 0.0)
    (while (and (not queue.pending) (< waited timeout-seconds))
      (<- (Delay 0.05))
      (:= waited (+ waited 0.05)))
    (val batch (cut queue.pending 0 limit))
    (setv queue.pending (cut queue.pending limit None))
    (resume batch)))


(defhandler eager-requests [#^ RequestQueue queue]
  ;; 引数に残す理由: 検が作った列そのものを読む。
  ;; 反例 — 待たずに答える: 列が空でもすぐ空のまとまりで返る(書く前に起きる)。
  (NextRequests [timeout-seconds limit]
    (val batch (cut queue.pending 0 limit))
    (setv queue.pending (cut queue.pending limit None))
    (resume batch)))


(defhandler newest-first-requests [#^ RequestQueue queue]
  ;; 引数に残す理由: 検が作った列そのものを読む。
  ;; 反例 — 後ろから取る: 起きる刻は本物と同じで、取る順だけが書いた順の逆。
  (NextRequests [timeout-seconds limit]
    (val taken (list (reversed (cut queue.pending (- limit) None))))
    (setv queue.pending (cut queue.pending 0 (max 0 (- (len queue.pending) limit))))
    (resume taken)))


;; --- 筋書き --------------------------------------------------------------------------------------------------------

(defrecord Take
  "取り手の 1 回の取り: woke-ms = NextRequests から戻った刻・paths = 取った要求の path(取った順)。"
  (#^ int woke-ms)
  (#^ tuple paths))


(defrecord Write
  "筋書きの書き 1 回: at-seconds = 書く刻(起点からの仮想の秒)・paths = 続けて積む要求の path(積む順)。"
  (#^ float at-seconds)
  (#^ tuple paths))


(defk write-later [queue writes]
  {:pre [(: queue RequestQueue) (: writes tuple)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの送り手: 書きの列(Write)の刻ごとに、要求を列へ積む(送り手の口 enqueue-request — 本物の模擬の送り手と同じ)。"
  (var at 0.0)
  (for [write writes]
    (when (> write.at-seconds at)
      (<- (Delay (- write.at-seconds at)))
      (:= at write.at-seconds))
    (for [path write.paths]
      (<- (enqueue-request queue (http-request "GET" path {} None)))))
  None)


(defk take-times [timeout-seconds limit times]
  {:pre [(: timeout-seconds float) (: limit int) (: times int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの取り手: NextRequests を times 回続けて出し、各回の起きた刻と取った要求を読む(Take の tuple)。"
  (var seen #())
  (for [_ (range times)]
    (<- batch list (NextRequests timeout-seconds :limit limit))
    (<- woke int (now-epoch-ms))
    (:= seen (+ seen #((Take :woke-ms woke :paths (tuple (gfor r batch r.path)))))))
  seen)


(defrecord Seen
  "筋書きの読み: takes = 取り手の各回(Take)・delays = 眠りの秒の列(取り手と送り手と期限の鳴らしの全部 — 読み直しの数を見る)。"
  (#^ tuple takes)
  (#^ list delays))


(defk run-queue [handler writes timeout-seconds limit times]
  {:pre [(: handler Callable) (: writes tuple) (: timeout-seconds float) (: limit int) (: times int)] :post [(: % Seen)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "列の取り方 handler(本物の queued-requests か反例 — 列を受けて handler を返す関数)の下で、送り手と取り手を仮想の時計(起点 0)で
   並べて回し、取り手の読みと眠りの列を返すため。"
  (val queue (RequestQueue))
  (val delays [])
  (<- takes tuple
      ((sim-time-handler :clock (clock-at 0))
        ((count-delays delays)
          (do-both (with-handlers [(handler queue)] (take-times timeout-seconds limit times))
                   (write-later queue writes)))))
  (Seen :takes takes :delays delays))


(defk do-both [taker writer]
  {:pre [(: taker Program) (: writer Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "取り手を先に走らせ(列が空の間に待ちへ入る)、送り手を並べて、両方の終わりを待つため。答え = 取り手の答え。"
  (<- taking Task (Spawn taker))
  (<- writing Task (Spawn writer))
  (<- takes tuple (Wait taking))
  (<- (Wait writing))
  takes)


(defk wake-breaches [seen expected]
  {:pre [(: seen Seen) (: expected tuple)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "取り手の各回(起きた刻と取った順)が期待(Take の tuple)と違う所を並べるため(空 = 合格)。"
  (lfor #(i #(got want)) (enumerate (zip seen.takes expected)) :if (!= got want)
        (.format "{} 回目: 期待 {} ・実際 {}" (+ i 1) want got)))


;; --- 検 --------------------------------------------------------------------------------------------------------------

(val LATE-WRITE (Write :at-seconds 2.53 :paths #("/a")))


(deftest test-a-write-wakes-the-taker-at-the-same-virtual-tick-and-not-before
  ;; 2.53 秒に 1 件書く・取り手の期限は 10 秒: 取り手は 2.53 秒ちょうどに起きてその 1 件を取る(書く前には起きない)。眠りは期限の
  ;; 鳴らしと送り手の 2 回だけ(読み直さない)。
  (val expected #((Take :woke-ms 2530 :paths #("/a"))))
  (<- seen Seen (run-queue queued-requests #(LATE-WRITE) 10.0 256 1))
  (<- breaches list (wake-breaches seen expected))
  (assert (= breaches []) breaches)
  (assert (<= (len seen.delays) 2) seen.delays)
  ;; 反例 — 前の見直しの形: 2.55 秒に起きる(書きから 0.02 秒遅れる)・眠りは 51 回。
  (<- polled Seen (run-queue polled-requests #(LATE-WRITE) 10.0 256 1))
  (<- polled-breaches list (wake-breaches polled expected))
  (assert polled-breaches polled)
  (assert (> (len polled.delays) 50) (len polled.delays))
  ;; 反例 — 待たずに答える形: 0 秒に空で起きる(書く前に起きる)。
  (<- eager Seen (run-queue eager-requests #(LATE-WRITE) 10.0 256 1))
  (<- eager-breaches list (wake-breaches eager expected))
  (assert eager-breaches eager)
  (assert (= (. (get eager.takes 0) woke-ms) 0) eager))


(deftest test-a-taker-without-a-write-wakes-empty-at-its-timeout
  ;; 書きの無い取り手は期限の 1 秒ちょうどに空のまとまりで起きる(次の取りも同じ)— coordinator の拍の意味は変えない。
  (val expected #((Take :woke-ms 1000 :paths #()) (Take :woke-ms 2000 :paths #())))
  (<- seen Seen (run-queue queued-requests #() 1.0 256 2))
  (<- breaches list (wake-breaches seen expected))
  (assert (= breaches []) breaches)
  ;; 反例 — 待たずに答える形: 期限を待たずに 0 秒で返る。
  (<- eager Seen (run-queue eager-requests #() 1.0 256 2))
  (<- eager-breaches list (wake-breaches eager expected))
  (assert eager-breaches eager))


(deftest test-several-writes-are-taken-in-the-written-order
  ;; 1 秒に a b c を続けて書き、3 秒に d を書く・取り手は 2 件ずつ 3 回取る: a b(1 秒)→ c(1 秒 — 残りは待たずに取る)→ d(3 秒)。
  (val writes #((Write :at-seconds 1.0 :paths #("/a" "/b" "/c")) (Write :at-seconds 3.0 :paths #("/d"))))
  (val expected #((Take :woke-ms 1000 :paths #("/a" "/b")) (Take :woke-ms 1000 :paths #("/c")) (Take :woke-ms 3000 :paths #("/d"))))
  (<- seen Seen (run-queue queued-requests writes 10.0 2 3))
  (<- breaches list (wake-breaches seen expected))
  (assert (= breaches []) breaches)
  ;; 反例 — 後ろから取る形: 起きる刻は同じで、取る順が逆になる。
  (<- reversed-seen Seen (run-queue newest-first-requests writes 10.0 2 3))
  (<- reversed-breaches list (wake-breaches reversed-seen expected))
  (assert reversed-breaches reversed-seen))
