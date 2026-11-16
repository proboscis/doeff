;;; 担い手の居なくなった lease の行を盤に残さない(2026-11-16): 担い手が空になる書きは行を消し、担い手の在る行は担い手の期限の最大 +
;;; SEMAPHORE-ROW-GRACE-MS を行の期限に持ち、期限の切れた行は調停(sweep-board)が掃く。行の期限を書く前の版が残した行は、起き直しの時に
;;; 期限を付けて次の拍で掃く。出自 = 2026-11-15 の測り: 盤の lease の行 31,574 行のうち担い手の在る行は 101 行で、盤の lease の行の読み 1 回が
;;; 0.4〜1.4 秒かかった。
(require doeff-hy.macros [defk deftest <- val])
(import dataclasses [replace])
(import doeff_hy._table [TableWrite table-of])
(import doeff_cluster [semaphore-key ClusterTiming BoardRow ClusterState http-request responded])
(import doeff_cluster._shared.core.lease_rules [semaphore-row-expiry])
(import doeff_cluster._shared.intent.semaphore_model [SEMAPHORE-ROW-GRACE-MS])
(import doeff_cluster._coordinator.core.cluster_policy [sweep-board board-tally-of board-without-holders])
(import doeff_cluster._coordinator.core.api_policy [resume-after-downtime])
(import doeff_cluster._coordinator.protocol.durable_kv [durable-delta])

(setv T (ClusterTiming))
(setv KEY (semaphore-key "lock"))


(defk call [state method path [body None] [now 1000]]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| dict list str int float bool None)) (: now int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator の純粋な判断 responded に要求を 1 つ渡し、(状態 状態の番号 本文) の組を返すため。"
  (responded state (! (http-request method path {} body :actor "c-test")) now T))


(defk lease [state op token now [ttl 15000]]
  {:pre [(: state ClusterState) (: op str) (: token str) (: now int) (: ttl int)] :post [(: % ClusterState)]
   :tags {:context "doeff-cluster-test" :role "judgment"}}
  "lease の口 POST /leases/lock へ操作 1 つを渡し、通った後の状態を返すため(断られたら test を落とす)。"
  (<- answer (call state "POST" "/leases/lock" {"op" op "token" token "permits" 2 "ttlMs" ttl} now))
  (assert (= (get answer 1) 200) answer)
  (get answer 0))


(deftest test-the-row-expiry-follows-the-latest-holder-and-is-zero-without-holders
  (assert (= (semaphore-row-expiry {"permits" 2 "holders" {"a/1" 5000 "b/1" 9000}}) (+ 9000 SEMAPHORE-ROW-GRACE-MS)))
  (assert (= (semaphore-row-expiry {"permits" 1 "holders" {}}) 0))
  ;; 形の読めない値は触らない
  (assert (is (semaphore-row-expiry {"permits" 1}) None))
  (assert (is (semaphore-row-expiry {"permits" 1 "holders" {"a/1" "soon"}}) None))
  (assert (is (semaphore-row-expiry 7) None)))


(deftest test-the-last-release-removes-the-lease-row-from-the-board
  (val claimed (! (lease (! (lease (ClusterState) "claim" "a/1" 1000)) "claim" "b/1" 1000)))
  (assert (= (. (.row claimed.board KEY) expires-ms) (+ 16000 SEMAPHORE-ROW-GRACE-MS)))
  (val one-left (! (lease claimed "release" "a/1" 2000)))
  (assert (= (get (. (.row one-left.board KEY) value) "holders") {"b/1" 16000}))
  (val none-left (! (lease one-left "release" "b/1" 3000)))
  (assert (is (.row none-left.board KEY) None) "最後の担い手を返したら行は無い")
  (assert (= (. (board-tally-of none-left.board) rows) 0))
  ;; 消した行は保存の差分でも消える
  (assert (= (! (durable-delta one-left none-left)) {(+ "board/" KEY) None}))
  ;; 消した後に取り直せる(版は 1 から)
  (val again (! (lease none-left "claim" "c/1" 4000)))
  (assert (= (. (.row again.board KEY) version) 1)))


(deftest test-a-drop-that-empties-the-row-removes-it
  (val claimed (! (lease (ClusterState) "claim" "job/gen-1/1" 1000)))
  (val dropped (! (lease claimed "drop" "job/gen-1/" 2000)))
  (assert (is (.row dropped.board KEY) None))
  ;; 世代の担い手を外す書き(heartbeat の名乗り・止まった世代の判じ)も同じ
  (assert (= (! (board-without-holders claimed.board #("job/gen-1/"))) #((TableWrite KEY None)))))


(deftest test-a-row-whose-holders-expired-is-swept-after-the-grace
  (val claimed (! (lease (ClusterState) "claim" "a/1" 1000)))
  (val expiry (+ 16000 SEMAPHORE-ROW-GRACE-MS))
  ;; 担い手の期限を過ぎても猶予の間は行が残り、遅れた延長が通る
  (assert (is (! (sweep-board claimed (- expiry 1))) claimed))
  (val renewed (! (lease claimed "renew" "a/1" 20000)))
  (assert (= (. (.row renewed.board KEY) expires-ms) (+ 35000 SEMAPHORE-ROW-GRACE-MS)))
  ;; 猶予を過ぎたら調停が掃く
  (assert (is (.row (. (! (sweep-board claimed expiry)) board) KEY) None)))


(deftest test-an-old-direct-write-of-a-lease-row-gets-the-row-expiry
  (<- written (call (ClusterState) "PUT" (+ "/board/" KEY) {"value" {"permits" 1 "holders" {"a/1" 9000}}} 1000))
  (assert (= (. (.row (. (get written 0) board) KEY) expires-ms) (+ 9000 SEMAPHORE-ROW-GRACE-MS)))
  ;; 担い手を空にする直の書きは、答えの版を保ったまま次の拍で掃かれる
  (<- emptied (call (get written 0) "PUT" (+ "/board/" KEY) {"value" {"permits" 1 "holders" {}}} 2000))
  (assert (= (get emptied 1) 200))
  (assert (= (. (.row (. (get emptied 0) board) KEY) expires-ms) 2000))
  (assert (is (.row (. (! (sweep-board (get emptied 0) 2000)) board) KEY) None))
  ;; lease の外の行は ttlSeconds のまま
  (<- plain (call (ClusterState) "PUT" "/board/plain" {"value" {"holders" {}}} 1000))
  (assert (is (. (.row (. (get plain 0) board) "plain") expires-ms) None)))


(deftest test-the-restart-dates-the-rows-left-by-the-older-version-and-the-next-sweep-removes-the-empty-ones
  (val empty-key (semaphore-key "gone"))
  (val stale-key (semaphore-key "stale"))
  (val stored (replace (ClusterState) :alive-ms 1000000
                       :board (table-of #((TableWrite KEY (BoardRow :value {"permits" 1 "holders" {"a/1" 1010000}} :version 3 :expires-ms None :size 0))
                                          (TableWrite empty-key (BoardRow :value {"permits" 1 "holders" {}} :version 9 :expires-ms None :size 0))
                                          (TableWrite stale-key (BoardRow :value {"permits" 1 "holders" {"x/1" 5000}} :version 2 :expires-ms None :size 0))
                                          (TableWrite "plain" (BoardRow :value 1 :version 1 :expires-ms None :size 1))))))
  (val now 1000000)
  (val resumed (get (resume-after-downtime stored now) 0))
  (assert (= (. (.row resumed.board KEY) expires-ms) (+ 1010000 SEMAPHORE-ROW-GRACE-MS)))
  (assert (= (. (.row resumed.board KEY) version) 3) "期限を付けるだけでは版を進めない")
  (assert (is (. (.row resumed.board "plain") expires-ms) None))
  (val swept (! (sweep-board resumed now)))
  (assert (is (.row swept.board empty-key) None) "担い手の無い行は次の拍で消える")
  (assert (is (.row swept.board stale-key) None) "猶予より前に切れた担い手だけの行も消える")
  (assert (is-not (.row swept.board KEY) None) "生きた担い手の行は残る")
  (assert (= (! (durable-delta resumed swept)) {(+ "board/" empty-key) None (+ "board/" stale-key) None})))
