;; 手入れの「消す前に送る」(maintenance.hy の頭の註 — card ki-c32e43651f2e・2026-10-11 11:21 に使い手の記録の Postgres の
;; disk が 100% になった事故の根の直し): 送りの関数を持つ計画の手入れ 1 回は、送りの答えの番号を刈りの上限に渡し、送り先に届かなかった
;; 回は刈らない。送りを持たない計画は上限なしで刈る(今までと同じ)。置き場の handler は撃たれた PruneChanges を控えるだけの顔。
(require doeff-hy.macros [deftest defhandler defk <- val var])
(import collections.abc [Callable])
(import doeff [with_handlers])
(import doeff_records [Event Unreachable])
(import doeff_records.maintenance [ArchivedThrough MaintenanceReport PruneChanges Pruned SweepExpired Swept maintenance-tick
                                   ReadExpiredEvents ExpiredEvents ExpiredCursor RetireArchivedEvents RetiredEvents EventsArchived
                                   EventsArchiveReport])
(import functools [partial])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records._http_server [MaintenancePlan unsent-streams])
(import doeff_records._laws [LAW-SCHEMA])


(defhandler recorder [#^ list asked]
  ;; 引数に残す理由: asked は検が読む控え — 置き場の顔が撃たれた PruneChanges をそこへ積む。回収は何もしない。
  (SweepExpired []
    (resume (Swept 0)))
  (PruneChanges [keep-seconds upto-seq]
    (.append asked effect)
    (resume (Pruned (or upto-seq 99) 1))))


(defk pruned-asks [archive]
  {:pre [(: archive (| Callable None))] :post [(: % tuple)]}
  "送りの関数 archive の計画で手入れを 1 回撃ち、#(答え 撃たれた PruneChanges の並び) を返すため(置き場は控えるだけの顔)。"
  (val asked [])
  (<- report (with_handlers [(recorder asked)] (maintenance-tick 60.0 archive)))
  #(report (tuple asked)))


(defk archived-through [seq]
  {:pre [(: seq int)] :post [(: % Callable)]}
  "送りの関数(答え = ArchivedThrough seq)を作るため。"
  (defk archive [keep-seconds]
    {:pre [(: keep-seconds float)] :post [(: % ArchivedThrough)]}
    "送り先に seq まで在ると答える送り。"
    (ArchivedThrough seq))
  archive)


(defk unreachable-archive [keep-seconds]
  {:pre [(: keep-seconds float)] :post [(: % Unreachable)]}
  "送り先に届かない送り。"
  (Unreachable "cold に届かない"))


(deftest test-the-tick-prunes-only-up-to-the-archived-seq
  ;; 失敗ケース = 送りの答えを刈りに渡さない(送っていない古い変更まで消す)。
  (<- archive (archived-through 7))
  (<- seen tuple (pruned-asks archive))
  (val report (get seen 0))
  (val asked (get seen 1))
  (assert (= asked #((PruneChanges 60.0 :upto-seq 7))) asked)
  (assert (and (= report.archived (ArchivedThrough 7)) (= report.pruned (Pruned 7 1))) report))


(deftest test-the-tick-does-not-prune-when-the-archive-is-unreachable
  ;; 失敗ケース = 送り先に届かない回にも時の境だけで刈る(送れていない変更が消える)。
  (<- seen tuple (pruned-asks unreachable-archive))
  (val report (get seen 0))
  (val asked (get seen 1))
  (assert (= asked #()) asked)
  (assert (and (isinstance report.archived Unreachable) (isinstance report.pruned Unreachable)) report))


(deftest test-a-plan-without-archive-prunes-without-a-bound
  (<- seen tuple (pruned-asks None))
  (val report (get seen 0))
  (val asked (get seen 1))
  (assert (= asked #((PruneChanges 60.0))) asked)
  (assert (is report.archived None) report))


;; 出来事を送ってから外す(maintenance.hy の頭の註 — card ki-707aad0f88e8): 手入れの回は刻 T を 1 度読み、その境で期限を過ぎた出来事を
;; 頁で読んで送り、確かめた番号のうち頁に在った物だけを同じ刻 T の境で外す。置き場は頁を答えて撃たれた外しを控えるだけの顔。

(defclass ExpiredShelf []
  "置き場の顔の控え: pages = 答える頁の並び(読みのたびに先頭から 1 つ)/ asked = 撃たれた ReadExpiredEvents と RetireArchivedEvents の並び。"
  (defn __init__ [self pages]  ; defk にできない: 検の控えの class の初期化
    (setv self.pages (tuple pages)
          self.asked #())))


(defhandler expired-shelf [#^ ExpiredShelf shelf]
  ;; 引数に残す理由: shelf は検が読む控え — 撃たれた effect を tuple で積み直す(読みは頁を 1 つずつ答える)。
  (SweepExpired []
    (resume (Swept 0)))
  (PruneChanges [keep-seconds upto-seq]
    (resume (Pruned 0 0)))
  (ReadExpiredEvents [at after limit]
    (setv shelf.asked (+ shelf.asked #(effect))
          page (get shelf.pages 0)
          shelf.pages (tuple (cut shelf.pages 1 None)))
    (resume page))
  (RetireArchivedEvents [stream at sequences]
    (setv shelf.asked (+ shelf.asked #(effect)))
    (resume (RetiredEvents (len sequences)))))


(defk confirm-sequences [wanted events]
  {:pre [(: wanted tuple) (: events tuple)] :post [(: % EventsArchived)]}
  "送り先の顔: 頁に関わらず wanted の番号を送り先に在ると答える(頁に無い番号を含めてよい — 外しは頁に在った物だけ)。"
  (EventsArchived wanted))


(defk unreachable-events [events]
  {:pre [(: events tuple)] :post [(: % Unreachable)]}
  "送り先に届かない出来事の送り。"
  (Unreachable "送り先に届かない"))


(val SHELF-PAGES
  #((ExpiredEvents #((Event "a" 1 "a-1" {"n" 1} "maker" 1000) (Event "a" 2 "a-2" {"n" 2} "maker" 2000)) (ExpiredCursor "a" 2000 2))
    (ExpiredEvents #() (ExpiredCursor "b"))
    (ExpiredEvents #((Event "b" 5 "b-5" {"n" 5} "maker" 5000)) None)))


(deftest test-the-tick-retires-only-confirmed-events-of-each-page
  ;; 失敗ケース = 送りの答えの番号を頁と照らさずに外す・頁ごとに刻を読み直す(読みと外しの境がずれる)・空の頁を送る。
  (val shelf (ExpiredShelf SHELF-PAGES))
  (<- report (with_handlers [(sim-time-handler :clock (SimClock)) (expired-shelf shelf)] (maintenance-tick 60.0 None :archive-events (partial confirm-sequences #(2 5 9)))))
  (val reads (tuple (gfor ask shelf.asked :if (isinstance ask ReadExpiredEvents) ask)))
  (val retires (tuple (gfor ask shelf.asked :if (isinstance ask RetireArchivedEvents) ask)))
  (assert (= (lfor ask reads ask.after) [None (ExpiredCursor "a" 2000 2) (ExpiredCursor "b")]) reads)
  (assert (= (len (frozenset (gfor ask shelf.asked ask.at))) 1) shelf.asked)
  (assert (= (lfor ask retires #(ask.stream ask.sequences)) [#("a" #(2)) #("b" #(5))]) retires)
  (assert (= report.events (EventsArchiveReport :sent 3 :confirmed 2 :unreached 0 :retired 2)) report))


(deftest test-the-tick-retires-nothing-when-the-destination-is-unreachable
  ;; 失敗ケース = 送り先に届かない頁の出来事を外す・届かない後も次の頁を送る。
  (val shelf (ExpiredShelf SHELF-PAGES))
  (<- report (with_handlers [(sim-time-handler :clock (SimClock)) (expired-shelf shelf)] (maintenance-tick 60.0 None :archive-events unreachable-events)))
  (assert (= (lfor ask shelf.asked (type ask)) [ReadExpiredEvents]) shelf.asked)
  (assert (= report.events (EventsArchiveReport :sent 2 :confirmed 0 :unreached 2 :retired 0 :halted (Unreachable "送り先に届かない")))
          report))


(deftest test-a-plan-without-the-event-archive-reads-no-expired-events
  ;; 失敗ケース = 出来事の送りの関数を持たない計画でも送りの読みを撃つ(前からの手入れの形が変わる)。
  (val shelf (ExpiredShelf SHELF-PAGES))
  (<- report (with_handlers [(sim-time-handler :clock (SimClock)) (expired-shelf shelf)] (maintenance-tick 60.0 None)))
  (assert (= shelf.asked #()) shelf.asked)
  (assert (is report.events None) report))


(deftest test-a-plan-without-the-event-archive-is-refused-for-marked-streams
  ;; 失敗ケース = 送ってから外す列を宣言した置き場に、出来事の送りの関数を持たない手入れを立てる(期限を過ぎた出来事が外れずに溜まる)。
  (<- bare (unsent-streams LAW-SCHEMA (MaintenancePlan :interval-seconds 60.0 :keep-seconds 60.0)))
  (<- sending (unsent-streams LAW-SCHEMA (MaintenancePlan :interval-seconds 60.0 :keep-seconds 60.0
                                                          :archive-events unreachable-events)))
  (<- unplanned (unsent-streams LAW-SCHEMA None))
  (assert (= bare #("relay-beats" "relay-pairs" "relay-ticks" "relays")) bare)
  (assert (= #(sending unplanned) #(#() #())) #(sending unplanned)))
