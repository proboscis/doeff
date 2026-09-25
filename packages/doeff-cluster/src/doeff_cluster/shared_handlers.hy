;;; 共有の保存の handler 2 つ。shared-memory = 同じ process の dict(テストの fake)・shared-http = coordinator の /board(クラスタ)。
;;; HTTP の client はこの module の中に閉じる(業務コードは ReadShared / WriteShared しか知らない)。
(require doeff-hy.macros [defhandler <-])
(import urllib.parse [quote :as url-quote])
(import doeff_cluster.clock [now-epoch-ms])
(import .shared_model [ReadShared WriteShared ANY cas-allows])
(import .semaphore_model [LeaseOp lease-op semaphore-key])
(import .coordinator_http [CoordinatorEndpoint send-idempotent REPLY-SECONDS])


(defhandler shared-memory [#^ dict store]
  (ReadShared [prefix]
    (resume (dfor #(k v) (.items store) :if (.startswith k prefix) k v)))
  (WriteShared [key value expect ttl-seconds]
    (setv present (in key store))
    (setv ok (cas-allows (.get store key) present expect))
    (when ok (setv (get store key) value))
    (resume ok))
  ;; lease の操作: coordinator と同じ純粋な判断(semaphore_model.lease-op)を、この保存の時計(doeff-time の GetTime)で当てる。
  (LeaseOp [name op token permits ttl-ms]
    (<- now int (now-epoch-ms))
    (setv key (semaphore-key name)
          #(row answer) (lease-op (.get store key) op token permits ttl-ms now))
    (when (is-not row None) (setv (get store key) row))
    (resume answer)))


(defclass SharedClient []
  "coordinator の /board との連絡(I/O)。"
  (defn __init__ [self #^ str url [timeout REPLY-SECONDS] [transport None]]
    ;; url = 宛先を `,` で並べた物(前ほど優先)。接続は使い回し、接続できない時は次の宛先へ・書きでも送り直す
    ;; (要求が届いていない)— coordinator_http の説明。
    (setv self.endpoint (CoordinatorEndpoint url timeout 4 :transport transport)))

  (defn #^ dict read [self #^ str prefix]
    ;; 読みは何度送っても同じなので、tailnet の数秒の途絶は期限まで送り直して越える(1 回の失敗で service を落とさない)。
    (setv response (send-idempotent (fn [] (.request self.endpoint "GET" "/board" :params {"prefix" prefix}))))
    (.raise-for-status response)
    (.json response))

  (defn #^ bool write [self #^ str key #^ object value #^ object expect #^ (| int float None) [ttl-seconds None]]
    ;; expect の 3 値を JSON で運ぶ: 欄が無い = 無条件・null = 行が無い時だけ・値 = その値の時だけ。
    (setv body {"value" value})
    (when (is-not expect ANY) (setv (get body "expect") expect))
    (when (is-not ttl-seconds None) (setv (get body "ttlSeconds") ttl-seconds))
    (setv response (.request self.endpoint "PUT" (+ "/board/" key) :json body))
    (when (= response.status-code 409) (return False))
    (.raise-for-status response)
    True)

  (defn #^ dict lease [self #^ str name #^ str op #^ str token #^ int permits #^ int ttl-ms]
    ;; claim と renew は同じ token で何度送っても同じ意味なので、途中で切れても期限まで送り直す。release・drop は 1 回だけ。
    (setv body {"op" op "token" token "permits" permits "ttlMs" ttl-ms}
          path (+ "/leases/" (url-quote name :safe ""))
          send (fn [] (.request self.endpoint "POST" path :json body))
          response (if (in op #("claim" "renew")) (send-idempotent send) (send)))
    (.raise-for-status response)
    (.json response)))


(defhandler shared-http [#^ SharedClient client]
  (ReadShared [prefix] (resume (.read client prefix)))
  (WriteShared [key value expect ttl-seconds] (resume (.write client key value expect ttl-seconds)))
  (LeaseOp [name op token permits ttl-ms] (resume (.lease client name op token permits ttl-ms))))
