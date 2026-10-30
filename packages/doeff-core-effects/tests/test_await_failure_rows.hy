;;; 失敗した Await の約束の行が、自分の handle を持ち続けて掃除されない形(agora card ki-84b4a6fb6503 — messaging の process で、
;;; 失敗で終わった要求ごとに httpcore の AsyncHTTPConnection・Origin・ExternalPromise・Await が 1:1:1:1 で約 0.8 個/秒ずつ増え、
;;; 3.5 時間で 9,971 個・gc の後も減らなかった)。scheduler は失敗した約束の例外を、掃除(#502)が handle を全部死んだと読むまで約束の表に
;;; 持つ。await-handler の橋の coroutine が捕えた例外の traceback の頭は橋自身の frame で、その closure が ExternalPromise と Await
;;; (待った coroutine ごと)を持つので、行が自分の handle を生かし、掃除が永く消さなかった。
;;; 検は socket を使わない — 接続を拒む httpcore の network backend を本物の接続の pool に渡し、pool が作る AsyncHTTPConnection ごと失敗させる。
(require doeff-hy.macros [deftest defk deff <- val with-handler])
;; 接続を拒む network backend を据える所は httpx の公開の欄が無いので、transport の _pool を替える(答え手の _client-pools も同じ欄を読む)。
(import gc)
(import httpcore)
(import httpx)
(import doeff_core_effects [await-handler])
(import doeff_core_effects [HttpFailed HttpRequest])
(import doeff_core_effects [http-production-handler])

;; 順に出す失敗の要求の数。直す前は要求ごとに 1 つずつ残るので、残りの数がこの数になる。
(val REQUESTS 40)
;; 直した後に残ってよい数 — 最後の要求の行は、次の掃除(待ちの合間)までは表に在ってよい。
(val LEFT-AT-MOST 2)


(defclass RefusingBackend [httpcore.AsyncNetworkBackend]
  "検の相手役 — どの接続も拒む network backend(socket を開かない)。"
  (defn :async connect-tcp [self host port [timeout None] [local-address None] [socket-options None]]
    "接続を拒むため(httpcore が接続の拒みとして読む例外を上げる)。"
    (raise (httpcore.ConnectError "refused by the test backend"))))


(deff refusing-client []  ; defk にできない: 答え手が範囲の初めに Program の外で素の関数として呼ぶ client の作り手
  {:pre [] :post [(: % httpx.AsyncClient)]}
  "接続を拒む backend の接続の pool を持つ本物の httpx の client を作るため(要求ごとに pool が AsyncHTTPConnection を作り、つなぐ所で失敗する)。"
  (setv transport (httpx.AsyncHTTPTransport))
  (setv transport._pool (httpcore.AsyncConnectionPool :network-backend (RefusingBackend)))
  (httpx.AsyncClient :transport transport))


;; 数える型の名(直す前に失敗した要求ごとに 1 つずつ残った 2 つ)。
(val COUNTED #("AsyncHTTPConnection" "ExternalPromise"))


(defk live-counts []
  {:pre [] :post [(: % (get dict #(str int)))] :tags {:context "http" :role "program"}}
  "gc の後に生きている COUNTED の型ごとの object の数を読むため。"
  (gc.collect)
  (val names (lfor o (gc.get-objects) (. (type o) __name__)))
  (dfor name COUNTED name (.count names name)))


(defk fail-many [n]
  {:pre [(: n int) (> n 0)] :post [(: % (get dict #(str int)))] :tags {:context "http" :role "program"}}
  "失敗する要求を n 本順に出し、出した後に生きている接続と約束の数を答えるため。"
  (for [_ (range n)]
    (<- answer HttpFailed (HttpRequest "GET" "http://refused.test/" :max-retries 0 :failures-as-values True))
    (assert (isinstance answer HttpFailed) answer))
  (<- counts (get dict #(str int)) (live-counts))
  counts)


(deftest test-failed-requests-leave-no-connection-or-promise-behind
  ;; 失敗ケース: 直す前は失敗した要求ごとに、約束の行の例外の traceback の頭の橋の frame が ExternalPromise を生かし、掃除が行を消さず、
  ;; 例外の traceback が持つ httpcore の AsyncHTTPConnection も残る — 40 本の後にどちらも 40 個増える。
  (<- before dict (live-counts))
  (<- after dict (with-handler [await-handler (http-production-handler :client-factory refusing-client)]
                   (fail-many REQUESTS)))
  (val grown (dfor #(name count) (.items after) name (- count (get before name))))
  (assert (all (gfor count (.values grown) (<= count LEFT-AT-MOST))) grown))
