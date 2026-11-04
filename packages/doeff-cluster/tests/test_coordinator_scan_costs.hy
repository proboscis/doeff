;;; coordinator の main thread の拍と要求ごとの全件走査(2026-11-04 の stack の計り — 入れ替えの宣言の指紋 2 割・盤の期限の掃除 1.6 割・
;;; GET /board の並べ替え 0.8 割)を、要る時・変わった時だけ・絞った後だけにした事を確かめる:
;;;   1. 入れ替えの見張り(handoff_policy.next-watch・handoff-due・watch-handoffs)は、入れ替えでない Service の宣言の指紋を求めず、
;;;      同じ宣言の object の指紋を求め直さない。
;;;   2. 盤の期限の掃除(cluster_policy.sweep-board)と次に起きる刻(sweep-due)は、期限つきの行が無い・期限の下限がまだ来ない拍に
;;;      盤の全行を読まない。
;;;   3. GET /board は前置きで絞ってから並べる(全行を並べ替えない)。
(require doeff-hy.macros [defk deftest <- val var])
(import collections [Counter])
(import dataclasses [replace])
(import doeff_hy._table [Table TableWrite table-of])
(import doeff_cluster [BoardRow ClusterState ClusterTiming http-request responded])
(import doeff_cluster._coordinator.intent.cluster_model [BoardTally HandoffWatch])
(import doeff_cluster._coordinator.core.cluster_policy [board-tally-of job-from-json sweep-board sweep-due])
(import doeff_cluster._coordinator.core [handoff_policy])
(import doeff_cluster._coordinator.core.handoff_policy [declaration-fingerprint handoff-due next-watch watch-handoffs])
(import tests.program_rows [SAMPLE-RUN])

(val T (ClusterTiming))
(val HANDOFF {"name" "h" "revision" "r1" "needs" ["net"] "run" SAMPLE-RUN "peakMiB" 200 "settleMiB" 100 "replicas" 1
              "readiness" {"windowSeconds" 10 "handoffTimeoutSeconds" 30} "update" "handoff"})
(val RECREATE {"name" "r" "revision" "r1" "needs" ["net"] "run" SAMPLE-RUN "peakMiB" 200 "settleMiB" 100 "replicas" 1
               "readiness" {"windowSeconds" 10}})


;;; 1. 宣言の指紋

(deftest test-the-declaration-fingerprint-is-taken-only-for-handoff-services-and-once-per-declaration
  (val recreate (job-from-json RECREATE))
  (val handoff (job-from-json HANDOFF))
  (val kept (HandoffWatch :declaration (declaration-fingerprint handoff) :since-ms 0))
  (val state (ClusterState :jobs #(recreate handoff)
                           :handoffs (table-of #((TableWrite "r" (HandoffWatch :declaration "old" :since-ms 0)) (TableWrite "h" kept)))))
  (val spelled (Counter))
  (val original handoff_policy.job-to-json)
  (.clear handoff_policy._FINGERPRINTS)
  (setv handoff_policy.job-to-json (fn [job] (+= (get spelled job.spec.name) 1) (original job)))
  (try
    ;; 入れ替えでない Service: 見張りを持っていても指紋を求めない。
    (assert (is (next-watch 1000 state recreate T) None))
    (<- due (handoff-due state recreate 1000 T))
    (assert (is due None))
    (assert (= spelled (Counter)) "recreate の Service の宣言は綴らない")
    ;; 入れ替えの Service: 同じ宣言の object なら 1 度だけ綴る(拍・期限の問いを重ねても)。
    (assert (is (next-watch 1000 state handoff T) kept) "担い手の報告が無い間は見張りをそのまま持つ")
    (assert (is (next-watch 2000 state handoff T) kept))
    (<- handoff-at (handoff-due state handoff 2000 T))
    (val swept (watch-handoffs 3000 state T))
    (assert (is (.row swept.handoffs "h") kept))
    (assert (is (.row swept.handoffs "r") None) "recreate の Service の見張りは捨てる")
    (assert (= spelled (Counter {"h" 1})) "宣言の変わらない拍は指紋を求め直さない")
    ;; 宣言が変われば(新しい object)求め直し、古い宣言の見張りを捨てる。
    (val redeclared (replace handoff :replicas 2))
    (assert (is (next-watch 4000 (replace state :jobs #(recreate redeclared)) redeclared T) None))
    (assert (= spelled (Counter {"h" 2})))
    (finally
      (setv handoff_policy.job-to-json original))))


;;; 2. 盤の期限の掃除

(defclass CountingTable [Table]
  "全行を読んだ回数を数える Table(行の引き .row と書き with-writes は数えない)。"
  (setv full-reads (Counter))
  (defn _flat-rows [self]
    (+= (get CountingTable.full-reads "flat") 1)
    (Table._flat-rows self)))


(defk counted-state [rows floor]
  {:pre [(: rows dict) (: floor (| int None))] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "盤を CountingTable にした状態(集計は rows から求め、期限の下限だけ floor に替える — 消しの後の古い下限を作る)。"
  (val counted (CountingTable rows {} (frozenset)))
  (val tally (board-tally-of (table-of (tuple (gfor #(k v) (.items rows) (TableWrite k v))))))
  (replace (ClusterState) :board counted :board-tally (replace tally :floor floor)))


(defk plain-rows [n]
  {:pre [(: n int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "期限の無い行 n 個(鍵 0〜n-1)。"
  (dfor i (range n) (str i) (BoardRow :value i :version 1 :expires-ms None :size 1)))


(deftest test-the-board-sweep-reads-no-rows-until-the-earliest-expiry
  ;; 期限つきの行が無い盤: 掃除も次に起きる刻も全行を読まない。
  (<- plain (counted-state (! (plain-rows 2057)) None))
  (.clear CountingTable.full-reads)
  (<- same (sweep-board plain 5000))
  (assert (is same plain))
  (<- due (sweep-due plain 5000 T))
  (assert (= (get CountingTable.full-reads "flat") 0) "期限つきの行が無ければ盤の全行を読まない")
  ;; 期限つきの行が在っても、下限が来る前の拍は読まない。
  (val rows (| (! (plain-rows 2057)) {"e1" (BoardRow :value 1 :version 1 :expires-ms 10000 :size 1)
                                  "e2" (BoardRow :value 2 :version 1 :expires-ms 20000 :size 1)}))
  (<- expiring (counted-state rows 10000))
  (.clear CountingTable.full-reads)
  (<- early (sweep-board expiring 9999))
  (assert (is early expiring))
  (<- next-due (sweep-due expiring 9999 T))
  (assert (= next-due.at 10000))
  (assert (= (get CountingTable.full-reads "flat") 0) "下限が来る前は盤の全行を読まない")
  ;; 下限が来た拍: 期限の来た行を消し、下限を残りのいちばん早い期限へ求め直す。
  (<- swept (sweep-board expiring 10000))
  (assert (is (.row swept.board "e1") None))
  (assert (= swept.board-tally (board-tally-of swept.board)))
  (assert (= swept.board-tally.floor 20000)))


(deftest test-a-stale-floor-is-recomputed-once-without-touching-the-rows
  ;; 消しの後の古い下限(10000 — その行はもう無い): 来た拍に 1 度だけ全行を読み、盤は同じ object のまま下限を求め直す。
  (val rows (| (! (plain-rows 2057)) {"e2" (BoardRow :value 2 :version 1 :expires-ms 20000 :size 1)}))
  (<- stale (counted-state rows 10000))
  (.clear CountingTable.full-reads)
  (<- swept (sweep-board stale 10000))
  (assert (is swept.board stale.board) "消す行が無ければ盤は同じ")
  (assert (= swept.board-tally.floor 20000))
  (val reads (get CountingTable.full-reads "flat"))
  (assert (> reads 0) "下限が来た拍は全行を読む")
  (<- later (sweep-board swept 15000))
  (assert (is later swept))
  (assert (= (get CountingTable.full-reads "flat") reads) "求め直した下限が来るまで全行を読まない"))


;;; 3. GET /board の並べ替え

(defclass CountedKey [str]
  "並べ替えの比べの回数を数える鍵。"
  (setv compares (Counter))
  (defn __lt__ [self other]
    (+= (get CountedKey.compares "lt") 1)
    (str.__lt__ self other)))


(deftest test-a-board-read-sorts-only-the-rows-under-the-prefix
  (val rows (+ (lfor i (range 2000) (TableWrite (CountedKey (.format "other/{:04d}" i)) (BoardRow :value i :version 1 :expires-ms None :size 1)))
               (lfor i [3 1 2] (TableWrite (CountedKey (.format "pick/{}" i)) (BoardRow :value i :version 1 :expires-ms None :size 1)))))
  (val state (replace (ClusterState) :board (table-of (tuple rows))))
  (.clear CountedKey.compares)
  (<- request (http-request "GET" "/board" {"prefix" "pick/"} None :actor "c-test"))
  (val answer (responded state request 1000 T))
  (assert (= (get answer 1) 200))
  (assert (= (list (.items (get answer 2))) [#("pick/1" 1) #("pick/2" 2) #("pick/3" 3)]) "前置きに合う行だけを鍵の順に")
  (assert (<= (get CountedKey.compares "lt") 10) "並べるのは前置きに合う 3 行だけ(全 2,003 行を並べ替えない)"))
