;;; 共有の保存の契約テスト — 同じ effect(ReadShared・WriteShared・LeaseOp)に答える本物(shared-http → coordinator)と
;;; fake(同じ shared-http を HTTP の層の fake の盤 tests/board_fake.hy の上で)が、同じ deftest を通る。解釈器の組み立ては
;;; coordinator_contract_handlers.hy。
;;;
;;;   * 書いた値が読める(prefix で始まる行だけ・無い prefix は空の dict)・coordinator の側の盤に同じ値で残る
;;;   * 書いた値と読んだ値は写し(書いた後・読んだ後に手元の値を変えても保存の値は変わらない)・JSON の形で戻る
;;;   * compare-and-set: ANY は無条件・None は行が無い時だけ・値は今の値と等しい時だけ書き、合わなければ偽で値を変えない
;;;   * 書けない行の期限(ttl-seconds が数でない・0 以下・30 日を越える)は例外で断り、行を変えない
;;;   * lease: 取る / 他の持ち主が持つ間の断り / 延ばす / 持っていない延長は lost / 返す / 2 度目の返しは lost / 担い手の頭で外す
;;;   * lease の期限は保存の時計: 期限の前は他が取れず、期限で取れ、奪われた持ち主の延長は lost
;;;   * 形の悪い lease の操作(知らない操作・期限が 0 か上限を越える・token が無い)は例外で断り、行を変えない
;;; 契約の外: 盤の容量の上限(本物だけが 507 で断る)・期限つきの行が期限で消えること(fake は期限を持たず行が残る)ほか —
;;; tests/board_fake.hy の頭の註。
(require doeff-hy.macros [defk deftest <- val])
(import doeff_time [Delay])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_cluster.shared.intent.shared_model [ReadShared WriteShared ANY])
(import doeff_cluster.shared.intent.semaphore_model [LeaseOp LeaseAnswer])
(import doeff_cluster.shared.core.lease_rules [semaphore-key])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import tests.coordinator_contract_handlers [BoardSeen])

(val TTL-MS 5000)
(val LEASE "contract-lock")


(deftest test-a-written-value-is-read-back-and-kept-on-the-coordinator
  {:interpreters ["shared-fake" "shared-http"]}
  (val rows {"app/a" {"n" 1 "nested" {"xs" [1 2.5 "s" True None]}}
             "app/b" [1 2 3]
             "app/c" "text"
             "app/d" 0
             "app/e" False
             "app/f" None})
  (for [#(key value) (.items rows)]
    (<- written bool (WriteShared key (OpaqueJson.of value)))
    (assert written key))
  (<- (WriteShared "other/x" (OpaqueJson.of 1)))
  (<- read dict (ReadShared "app/"))
  (<- nothing dict (ReadShared "missing/"))
  (<- board dict (BoardSeen))
  (assert (= read rows) read)
  (assert (= nothing {}) nothing)
  (assert (= board (| rows {"other/x" 1})) board))


(deftest test-written-and-read-values-are-copies
  {:interpreters ["shared-fake" "shared-http"]}
  (val value {"items" [1]})
  (<- (WriteShared "copy/row" (OpaqueJson.of value)))
  (.append (get value "items") 2)
  (<- first dict (ReadShared "copy/"))
  (.append (get first "copy/row" "items") 3)
  (<- second dict (ReadShared "copy/"))
  (assert (= second {"copy/row" {"items" [1]}})
          (.format "書いた後・読んだ後に手元の値を変えると保存の値が変わった: {}" second)))


(deftest test-values-come-back-in-json-form
  {:interpreters ["shared-fake" "shared-http"]}
  ;; 本物は値を JSON で運ぶので、dict の鍵は文字列・tuple は list で戻る。期待の値も同じ形で比べる。
  (<- (WriteShared "form/row" (OpaqueJson.of {1 #(1 2)})))
  (<- read dict (ReadShared "form/"))
  (<- matched bool (WriteShared "form/row" (OpaqueJson.of "next") (OpaqueJson.of {"1" #(1 2)})))
  (assert (= read {"form/row" {"1" [1 2]}}) read)
  (assert matched "JSON の形で等しい期待の値が合わなかった"))


(deftest test-a-row-lifetime-out-of-range-is-refused-and-changes-nothing
  {:interpreters ["shared-fake" "shared-http"]}
  (<- (WriteShared "life/row" (OpaqueJson.of "kept")))
  (val refused [])
  (for [ttl [0 -1 "60" (* 31 24 3600)]]
    (try
      (<- (WriteShared "life/row" (OpaqueJson.of "replaced") :ttl-seconds ttl))
      (except [Exception]
        (.append refused ttl))))
  (<- read dict (ReadShared "life/"))
  (assert (= refused [0 -1 "60" (* 31 24 3600)]) (.format "断られなかった期限がある: {}" refused))
  (assert (= read {"life/row" "kept"}) read))


(deftest test-compare-and-set-writes-only-when-the-expectation-holds
  {:interpreters ["shared-fake" "shared-http"]}
  (<- absent-only bool (WriteShared "cas/k" (OpaqueJson.of "v1") None))
  (<- absent-again bool (WriteShared "cas/k" (OpaqueJson.of "v2") None))
  (<- stale bool (WriteShared "cas/k" (OpaqueJson.of "v3") (OpaqueJson.of "v0")))
  (<- after-refusals dict (ReadShared "cas/"))
  (<- matched bool (WriteShared "cas/k" (OpaqueJson.of "v4") (OpaqueJson.of "v1")))
  (<- unconditional bool (WriteShared "cas/k" (OpaqueJson.of "v5") ANY))
  (<- board dict (BoardSeen))
  (assert (= #(absent-only absent-again stale matched unconditional) #(True False False True True)))
  (assert (= after-refusals {"cas/k" "v1"}) (.format "合わない書きが値を変えた: {}" after-refusals))
  (assert (= (get board "cas/k") "v5") board))


(deftest test-compare-and-set-compares-the-decoded-values-not-their-spelling
  ;; 盤の値は OpaqueJson(中を読まずに運ぶ JSON の文字列)で渡るが、compare-and-set は盤が解いた値で比べる(#2543)。
  ;; 欄の順だけが違う 2 つの値は等しい・中身が違えば違う。OpaqueJson の文字列の等しさ(欄の順まで見る)で比べる形に変えると赤になる。
  {:interpreters ["shared-fake" "shared-http"]}
  (val written (OpaqueJson.of {"a" 1 "b" [1 2]}))
  (val reordered (OpaqueJson.from-text "{\"b\": [1, 2], \"a\": 1}"))
  (assert (!= written reordered) "前提: 欄の順の違う 2 つの OpaqueJson は文字列としては違う")
  (<- (WriteShared "order/k" written))
  (<- same-content bool (WriteShared "order/k" (OpaqueJson.of "v2") reordered))
  (<- other-content bool (WriteShared "order/k" (OpaqueJson.of "v3") (OpaqueJson.of {"a" 1 "b" [2 1]})))
  (<- read dict (ReadShared "order/"))
  (assert same-content "欄の順だけが違う期待の値が合わなかった")
  (assert (not other-content) "中身の違う期待の値で書けた")
  (assert (= read {"order/k" "v2"}) read))


(defk lease [op token]
  {:pre [(: op str) (: token str)] :post [(: % LeaseAnswer)] :tags {:context "doeff-cluster-test" :role "program"}}
  "契約の lease(permits 1・TTL-MS)の操作 1 つ。"
  (<- answer LeaseAnswer (LeaseOp LEASE op token 1 TTL-MS))
  answer)


(defk holders []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator の側の盤にある契約の lease の担い手 {token: 期限の epoch ミリ秒}(行が無ければ空)。"
  (<- board dict (BoardSeen))
  (.get (.get board (semaphore-key LEASE) {}) "holders" {}))


(deftest test-a-lease-is-claimed-refused-renewed-and-released
  {:interpreters ["shared-fake" "shared-http"]}
  (<- start int (now-epoch-ms))
  (<- claimed LeaseAnswer (lease "claim" "a/1/x/1"))
  (<- refused LeaseAnswer (lease "claim" "b/1/y/1"))
  (<- held dict (holders))
  (<- read dict (ReadShared "semaphore/"))
  (<- (Delay 1.0))
  (<- renewed LeaseAnswer (lease "renew" "a/1/x/1"))
  (<- stranger LeaseAnswer (lease "renew" "b/1/y/1"))
  (<- renewed-holders dict (holders))
  (<- released LeaseAnswer (lease "release" "a/1/x/1"))
  (<- released-again LeaseAnswer (lease "release" "a/1/x/1"))
  (<- taken LeaseAnswer (lease "claim" "b/1/y/1"))
  (<- dropped LeaseAnswer (lease "drop" "b/1/"))
  (<- after-drop dict (holders))
  (assert (= claimed (LeaseAnswer :ok True :reason None :ttl-ms TTL-MS :dropped 0)) claimed)
  (assert (= refused (LeaseAnswer :ok False :reason "空きが無い" :ttl-ms TTL-MS :dropped 0)) refused)
  (assert (= held {"a/1/x/1" (+ start TTL-MS)}) held)
  (assert (= read {(semaphore-key LEASE) {"permits" 1 "holders" held}}) (.format "lease の行が ReadShared で読めない: {}" read))
  (assert (= renewed (LeaseAnswer :ok True :reason None :ttl-ms TTL-MS :dropped 0)) renewed)
  (assert (= stranger (LeaseAnswer :ok False :reason "lost" :ttl-ms TTL-MS :dropped 0)) stranger)
  (assert (= renewed-holders {"a/1/x/1" (+ start 1000 TTL-MS)}) renewed-holders)
  (assert (= released (LeaseAnswer :ok True :reason None :ttl-ms 0 :dropped 0)) released)
  (assert (= released-again (LeaseAnswer :ok False :reason "lost" :ttl-ms 0 :dropped 0)) released-again)
  (assert taken.ok taken)
  (assert (= dropped (LeaseAnswer :ok True :reason None :ttl-ms 0 :dropped 1)) dropped)
  (assert (= after-drop {}) after-drop))


(deftest test-a-lease-expires-by-the-store-clock
  {:interpreters ["shared-fake" "shared-http"]}
  (<- (lease "claim" "a/1/x/1"))
  (<- (Delay (/ (- TTL-MS 100) 1000)))
  (<- before LeaseAnswer (lease "claim" "b/1/y/1"))
  (<- (Delay 0.1))
  (<- at-expiry LeaseAnswer (lease "claim" "b/1/y/1"))
  (<- robbed LeaseAnswer (lease "renew" "a/1/x/1"))
  (<- now int (now-epoch-ms))
  (<- held dict (holders))
  (assert (not before.ok) (.format "期限の前に他が取れた: {}" before))
  (assert at-expiry.ok (.format "期限で取れない: {}" at-expiry))
  (assert (= robbed.reason "lost") robbed)
  (assert (= held {"b/1/y/1" (+ now TTL-MS)}) held))


(deftest test-a-malformed-lease-op-is-refused-and-changes-nothing
  {:interpreters ["shared-fake" "shared-http"]}
  (<- (lease "claim" "a/1/x/1"))
  (<- before dict (holders))
  (val refused [])
  ;; 知らない操作・期限 0 の取る・上限(10 分)を越える延長・token の無い操作
  (for [#(label op token ttl-ms) [#("unknown-op" "steal" "b/1/y/1" TTL-MS) #("zero-ttl" "claim" "b/1/y/1" 0)
                                   #("too-long" "renew" "a/1/x/1" (+ (* 10 60 1000) 1)) #("no-token" "claim" "" TTL-MS)]]
    (try
      (<- (LeaseOp LEASE op token 1 ttl-ms))
      (except [Exception]
        (.append refused label))))
  (<- after dict (holders))
  (assert (= refused ["unknown-op" "zero-ttl" "too-long" "no-token"]) (.format "断られなかった操作がある: {}" refused))
  (assert (= after before) after))
