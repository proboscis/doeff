;;; 共有の保存の handler 2 つ。shared-memory = 同じ process の dict(テストの fake)・shared-http = coordinator の /board(クラスタ)。
;;; HTTP の client はこの module の中に閉じる(業務コードは ReadShared / WriteShared しか知らない)。
;;; 要求の形(board-read-request・board-write-request・lease-request)は、この client と手元の sim-cluster の偽の宿(local.hy)が
;;; 同じ関数で作る(本文を写さない)。
(require doeff-hy.macros [defhandler defk deff <- val])
(import json)
(import urllib.parse [quote :as url-quote])
(import httpx)
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.shared_model [ReadShared WriteShared ANY AnyExpect JsonValue])
(import doeff_cluster.shared.intent.semaphore_model [LeaseOp])
(import doeff_cluster.shared.core.board_rules [board-ttl-refusal cas-allows])
(import doeff_cluster.shared.core.lease_rules [lease-op semaphore-key])
(import doeff_cluster.coordinator_http [CoordinatorEndpoint send-idempotent REPLY-SECONDS])


(defk json-snapshot [value]
  {:pre [(: value JsonValue)] :post [(: % JsonValue)] :tags {:context "doeff-cluster" :role "judgment"}}
  "盤の値を JSON に通した写しにするため: fake の保存(shared-memory)が、本物(coordinator の /board へ JSON で運ぶ)と同じく
   書いた・読んだ値を呼び手の object と切り離し、同じ形(dict の鍵は文字列・tuple は list)で返す。JSON にできない値は TypeError
   (本物の client が本文を JSON にする時と同じ)。"
  (json.loads (json.dumps value)))


(deff board-read-request [#^ str prefix]  ; defk にできない: 本番の client(Program の外の I/O の道具)と sim の宿が同じ形を作る純粋な判断
  {:pre [(: prefix str)] :post [(: % tuple) (= (len %) 4)] :tags {:context "doeff-cluster" :role "protocol"}}
  "ReadShared を coordinator の盤の読みの要求 #(method path query 本文) にするため(本番の SharedClient と sim の宿で同じ形)。"
  #("GET" "/board" {"prefix" prefix} None))


(deff board-write-request [#^ str key value expect #^ (| int float None) ttl-seconds]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: key str) (: value JsonValue) (: expect (| JsonValue AnyExpect))
         (: ttl-seconds (| int float None))]
   :post [(: % tuple) (= (len %) 4)] :tags {:context "doeff-cluster" :role "protocol"}}
  "WriteShared を盤の compare-and-set の要求 #(method path query 本文) にするため。expect の 3 値を JSON で運ぶ: 欄が無い = 無条件・
   null = 行が無い時だけ・値 = その値の時だけ。答えの読みは 409 = 偽(合わなかった)・300 未満 = 真。"
  #("PUT" (+ "/board/" key) {}
    (| {"value" value}
       (if (is expect ANY) {} {"expect" expect})
       (if (is ttl-seconds None) {} {"ttlSeconds" ttl-seconds}))))


(deff lease-request [#^ str name #^ str op #^ str token #^ int permits #^ int ttl-ms]  ; defk にできない: 本番の client と sim の宿が同じ形を作る純粋な判断
  {:pre [(: name str) (: op str) (: token str) (: permits int) (: ttl-ms int)] :post [(: % tuple) (= (len %) 4)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "LeaseOp を coordinator の lease の口の要求 #(method path query 本文) にするため(本番の SharedClient と sim の宿で同じ形)。"
  #("POST" (+ "/leases/" (url-quote name :safe "")) {} {"op" op "token" token "permits" permits "ttlMs" ttl-ms}))


;; fake の保存。本物(shared-http → coordinator の /board)と同じ契約を tests/test_shared_contract.hy が両方で回す: 値は JSON に通した
;; 写しで持ち・返し(json-snapshot)、書けない期限は同じ規則で断る(board-ttl-refusal — 本物は 400)。期限そのもの(期限を過ぎた行を
;; 消す)はまだ持たない — 期限つきの行も残り続ける(本物との食い違い — 契約の外)。
(defhandler shared-memory [#^ dict store]
  (ReadShared [prefix]
    (<- rows dict (json-snapshot (dfor #(k v) (.items store) :if (.startswith k prefix) k v)))
    (resume rows))
  (WriteShared [key value expect ttl-seconds]
    (<- refusal (board-ttl-refusal ttl-seconds))
    (when (is-not refusal None) (raise (ValueError refusal)))
    ;; 比べる期待の値も JSON に通す(本物は期待の値も JSON で運ぶ — tuple の期待が list の行と等しくなる)。ANY は運ばない印。
    (<- written list (json-snapshot [value (if (is expect ANY) None expect)]))
    (val ok (cas-allows (.get store key) (in key store) (if (is expect ANY) expect (get written 1))))
    (when ok (.update store {key (get written 0)}))
    (resume ok))
  ;; lease の操作: coordinator と同じ純粋な判断(lease_rules.lease-op)を、この保存の時計(doeff-time の GetTime)で当てる。
  (LeaseOp [name op token permits ttl-ms]
    (<- now int (now-epoch-ms))
    (setv key (semaphore-key name)
          #(row answer) (lease-op (.get store key) op token permits ttl-ms now))
    (when (is-not row None) (setv (get store key) row))
    (resume answer)))


(defclass SharedClient []
  "coordinator の /board との連絡(I/O)。"
  (defn #^ None __init__ [self #^ str url #^ float [timeout REPLY-SECONDS] #^ (| httpx.BaseTransport None) [transport None]]
    ;; url = 宛先を `,` で並べた物(前ほど優先)。接続は使い回し、接続できない時は次の宛先へ・書きでも送り直す
    ;; (要求が届いていない)— coordinator_http の説明。
    (setv self.endpoint (CoordinatorEndpoint url timeout 4 :transport transport)))

  (defn #^ dict read [self #^ str prefix]
    ;; 読みは何度送っても同じなので、tailnet の数秒の途絶は期限まで送り直して越える(1 回の失敗で service を落とさない)。
    (setv #(method path query _) (board-read-request prefix))
    (setv response (send-idempotent (fn [] (.request self.endpoint method path :params query))))
    (.raise-for-status response)
    (.json response))

  (defn #^ bool write [self #^ str key #^ JsonValue value #^ (| JsonValue AnyExpect) expect #^ (| int float None) [ttl-seconds None]]
    (setv #(method path _ body) (board-write-request key value expect ttl-seconds))
    (setv response (.request self.endpoint method path :json body))
    (when (= response.status-code 409) (return False))
    (.raise-for-status response)
    True)

  (defn #^ dict lease [self #^ str name #^ str op #^ str token #^ int permits #^ int ttl-ms]
    ;; claim と renew は同じ token で何度送っても同じ意味なので、途中で切れても期限まで送り直す。release・drop は 1 回だけ。
    (setv #(method path _ body) (lease-request name op token permits ttl-ms)
          send (fn [] (.request self.endpoint method path :json body))
          response (if (in op #("claim" "renew")) (send-idempotent send) (send)))
    (.raise-for-status response)
    (.json response)))


(defhandler shared-http [#^ SharedClient client]
  (ReadShared [prefix] (resume (.read client prefix)))
  (WriteShared [key value expect ttl-seconds] (resume (.write client key value expect ttl-seconds)))
  (LeaseOp [name op token permits ttl-ms] (resume (.lease client name op token permits ttl-ms))))
