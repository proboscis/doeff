;; 入口の反例: main と同じ組み立て(assembled-from-env → opened)で実の socket の口を開き、身元つきの数 KB の問いを 8 本並列に撃ち、
;; 続けて覚えている時だけの束(/peek)に鍵 1000 個を撃つ。接続が 1 byte も書かれずに切れない(RST・応答の前の切断が無い)こと、
;; 全部に答えることを確かめる — 棚卸し(agora-redesign #843)の見立て 1「要求の thread が Exception 以外で落ちて接続が黙って切れる」の反例。
;; 本物の Jev は検の中の偽の上流(127.0.0.1 の空き port・本物の鍵は使わない・外へ出ない)。
(require doeff-hy.macros [deftest defk deff <- val var])
(import concurrent.futures [ThreadPoolExecutor])
(import hashlib)
(import http.client [HTTPConnection])
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import json)
(import os)
(import types [NoneType])
(import tempfile)
(import threading)
(import time)
(import urllib.parse [urlsplit])
(import doeff_jev_proxy.main [Assembled Unassembled assembled-from-env opened])
(import doeff_jev_proxy.http_server [ProxyServerConfig start-proxy-server stop-server])
(import tests.world [OPERATOR-TOKEN question])

;; 偽の上流が受け取るキー(検のためだけの綴り — 本物の鍵ではない)。
(val SECRET "apikey_for_entry_test_only")
;; 同時に撃つ問いの数と、束の鍵の数(linter が 1 度に撃つ束と同じ大きさ)。
(val PARALLEL 8)
(val PEEK-KEYS 1000)
;; 偽の上流が答える前に待つ秒(8 本の問いが代理の中で重なるように)。
(val UPSTREAM-DELAY 0.3)
(val UPSTREAM-ANSWER b"{\"answers\":{\"q\":{\"noul\":0.5}},\"model\":\"jev-fake\"}")


(deff fake-upstream-post [handler]  ; defk にできない: http.server が要求ごとの thread で呼ぶ callback(do_POST)
  {:pre [(: handler BaseHTTPRequestHandler)] :post [(: % NoneType)] :tags {:context "jev-proxy" :role "foundation"}}
  "偽の上流の POST: 本文を読み、届いた Authorization を server.seen に積み、少し待って同じ答えを返す。"
  (.read handler.rfile (int (.get handler.headers "Content-Length" "0")))
  (with [_ handler.server.lock]
    (.append handler.server.seen (.get handler.headers "Authorization")))
  (time.sleep UPSTREAM-DELAY)
  (.send-response handler 200)
  (.send-header handler "Content-Type" "application/json")
  (.send-header handler "Content-Length" (str (len UPSTREAM-ANSWER)))
  (.end-headers handler)
  (.write handler.wfile UPSTREAM-ANSWER)
  None)


(deff quiet-log [handler #* _]  ; defk にできない: http.server が要求ごとに呼ぶ callback(log_message)
  {:pre [(: handler BaseHTTPRequestHandler)] :post [(: % NoneType)] :tags {:context "jev-proxy" :role "foundation"}}
  "偽の上流の要求の行を書かない。"
  None)


(defk fake-upstream []
  {:pre [] :post [(: % ThreadingHTTPServer)] :tags {:context "jev-proxy" :role "foundation"}}
  "偽の上流(127.0.0.1 の空き port)を立てるため。届いた Authorization は server.seen に積む。"
  (val handler-class (type "FakeUpstream" #(BaseHTTPRequestHandler)
                           {"protocol_version" "HTTP/1.1" "do_POST" fake-upstream-post "log_message" quiet-log}))
  (val server (ThreadingHTTPServer #("127.0.0.1" 0) handler-class))
  (setv server.daemon-threads True
        server.seen []
        server.lock (threading.Lock))
  (.start (threading.Thread :target server.serve-forever :daemon True))
  server)


(defk entry-environ [upstream-url]
  {:pre [(: upstream-url str)] :post [(: % dict)] :tags {:context "jev-proxy" :role "foundation"}}
  "本番の Pod と同じ env の組を一時の dir の file で作るため(置き場・名簿・上流のキーの file・口は空いている port)。"
  (val root (tempfile.mkdtemp :prefix "jev-proxy-entry-"))
  (val roster-path (os.path.join root "roster.json"))
  (val key-path (os.path.join root "typesafe-api-key"))
  (with [handle (open roster-path "w" :encoding "utf-8")]
    (json.dump {"version" 1 "principals" [{"name" "operator"
                                             "tokenSha256" (.hexdigest (hashlib.sha256 (.encode OPERATOR-TOKEN "utf-8")))}]}
               handle))
  (with [handle (open key-path "w" :encoding "utf-8")] (.write handle SECRET))
  {"JEV_PROXY_DB" (os.path.join root "answers.sqlite")
   "JEV_PROXY_ROSTER_FILE" roster-path
   "JEV_PROXY_ADMINS" "operator"
   "JEV_PROXY_HOST" "127.0.0.1"
   "JEV_PROXY_PORT" "0"
   "JEV_PROXY_UPSTREAM_TIMEOUT_SECONDS" "10"
   "JEV_BASE_URL" upstream-url
   "JEV_MODEL" "jev-latest"
   "JEV_API_KEY_FILE" key-path})


(deff posted [url path body cache-control start]  ; defk にできない: 並列の検で ThreadPoolExecutor の thread が呼ぶ callback
  {:pre [(: url str) (: path str) (: body bytes) (: cache-control (| str None)) (: start (| threading.Barrier None))]
   :post [(: % dict)]
   :tags {:context "jev-proxy" :role "foundation"}}
  "実の socket で身元つきの POST を 1 本撃ち、{status headers body} か、応答の前に接続が切れた時は {failure: 例外の型と文} を返す。"
  (setv place (urlsplit url)
        connection (HTTPConnection place.hostname place.port :timeout 30)
        headers (| {"content-type" "application/json" "authorization" (+ "Bearer " OPERATOR-TOKEN)}
                   (if cache-control {"cache-control" cache-control} {})))
  (when start (.wait start))
  (try
    (.request connection "POST" path :body body :headers headers)
    (setv response (.getresponse connection))
    {"status" response.status "headers" (dict (gfor #(k v) (.getheaders response) #((.lower k) v))) "body" (.read response)}
    (except [error OSError]
      {"failure" (.format "{}: {}" (. (type error) __name__) error)})
    (finally
      (.close connection))))


(defk large-questions [count]
  {:pre [(: count int)] :post [(: % list)] :tags {:context "jev-proxy" :role "foundation"}}
  "linter が撃つのと同じ形で、定義の source が数 KB ある問いの本文を count 個作るため(1 つずつ別の鍵)。"
  (val bodies [])
  (for [index (range count)]
    (val filler (.join "\n" (lfor line (range 80) (.format "  ;; 定義 {} の本文の {} 行目 — 問いを本物の大きさにする詰め物" index line))))
    (<- body (question (.format "(defk entry-{} []\n{}\n  {})" index filler index) "jev-latest"))
    (.append bodies body))
  bodies)


(deftest test-entry-serves-parallel-large-asks-and-a-large-peek-without-dropping-the-connection
  (<- upstream (fake-upstream))
  (<- environ (entry-environ (.format "http://127.0.0.1:{}/v1/systemone" (get upstream.server-address 1))))
  ;; main(serve)と同じ順: env から組む → 置き場を用意して口を開く。違うのは env の中身と、signal を待たずに閉じる所だけ。
  (<- assembled (assembled-from-env environ))
  (assert (isinstance assembled Assembled) assembled)
  (<- running (opened assembled))
  (try
    (<- bodies (large-questions PARALLEL))
    (assert (all (gfor body bodies (> (len body) 4000))) "問いは数 KB")
    ;; 半分は較正と同じ問い直し(no-cache)・半分は普通の問い — linter が並列に撃つ 2 種類。
    (val cache-controls (lfor index (range PARALLEL) (if (% index 2) "no-cache" None)))
    (val start (threading.Barrier PARALLEL))
    (val first-round (with [pool (ThreadPoolExecutor :max-workers PARALLEL)]
                       (list (.map pool (fn [pair] (posted running.url "/v1/systemone" (get pair 0) (get pair 1) start))
                                   (zip bodies cache-controls)))))
    (val dropped (lfor reply first-round :if (in "failure" reply) (get reply "failure")))
    (assert (= dropped []) (.format "接続が応答の前に切れた: {}" dropped))
    (assert (= (lfor reply first-round (get reply "status")) (* [200] PARALLEL)) first-round)
    (assert (all (gfor reply first-round (= (get reply "body") UPSTREAM-ANSWER))) "全部が上流の答えをそのまま返す")
    (assert (= (sorted (lfor reply first-round (get (get reply "headers") "x-jev-proxy"))) (sorted (+ (* ["miss"] 4) (* ["refreshed"] 4))))
            first-round)
    (assert (= upstream.seen (* [(+ "Bearer " SECRET)] PARALLEL)) "上流には代理のキーだけが届く(呼び手の token ではない)")
    ;; 2 周目は全部が覚えから当たり、上流を呼ばない。
    (val again-start (threading.Barrier PARALLEL))
    (val second-round (with [pool (ThreadPoolExecutor :max-workers PARALLEL)]
                        (list (.map pool (fn [body] (posted running.url "/v1/systemone" body None again-start)) bodies))))
    (assert (= (lfor reply second-round #((.get reply "status") (.get (.get reply "headers" {}) "x-jev-proxy")))
               (* [#(200 "hit")] PARALLEL))
            second-round)
    (assert (= (len upstream.seen) PARALLEL) "2 周目は上流を呼ばない")
    ;; 覚えている時だけの束: 覚えた 8 個の鍵と、覚えていない 992 個の鍵(本文は約 67 KB)。
    (val known (lfor reply first-round (get (get reply "headers") "x-jev-proxy-key")))
    (val unknown (lfor index (range (- PEEK-KEYS PARALLEL)) (.hexdigest (hashlib.sha256 (.encode (.format "absent-{}" index) "utf-8")))))
    (val peek-body (.encode (json.dumps {"keys" (+ known unknown)}) "utf-8"))
    (assert (> (len peek-body) 60000) "束の本文は linter と同じ大きさ")
    (val peeked (posted running.url "/v1/systemone/peek" peek-body None None))
    (assert (not-in "failure" peeked) peeked)
    (assert (= (get peeked "status") 200) peeked)
    (val answers (get (json.loads (get peeked "body")) "answers"))
    (assert (= (sorted answers) (sorted known)) (sorted answers))
    (assert (= (len upstream.seen) PARALLEL) "束は上流を呼ばない")
    (finally
      (stop-server running)
      (.shutdown upstream)
      (.server-close upstream))))


(defclass SimulatedPanic [BaseException]
  "doeff の VM の panic が Python に出る形(Exception の外の BaseException の子)の代わり。")


(deftest test-request-failing-outside-exception-answers-500-instead-of-dropping
  ;; 要求の処理が Exception 以外で落ちても(panic・SystemExit・KeyboardInterrupt)、接続は黙って切れず 500 で答え、口は次の要求に答える。
  (for [kind [SimulatedPanic SystemExit KeyboardInterrupt]]
    (val running (start-proxy-server (ProxyServerConfig :runner (fn [request] (raise (kind "壊れた処理"))))))
    (try
      (val reply (posted running.url "/v1/systemone" b"{}" None None))
      (assert (not-in "failure" reply) (.format "{}: 接続が応答の前に切れた: {}" kind.__name__ reply))
      (assert (= (get reply "status") 500) reply)
      (val document (json.loads (get reply "body")))
      (assert (= (get document "error") "internal") document)
      (assert (.startswith (get document "reason") kind.__name__) document)
      (val next-reply (posted running.url "/v1/systemone" b"{}" None None))
      (assert (= (.get next-reply "status") 500) "口は落ちず次の要求にも答える")
      (finally
        (stop-server running)))))


(deftest test-entry-names-what-is-missing-instead-of-opening
  ;; 必須の env が無ければ口を開かず、理由を値で返す(入口はそれを SystemExit の文にする)。
  (<- environ (entry-environ "http://127.0.0.1:9/v1/systemone"))
  (<- assembled (assembled-from-env (dfor #(k v) (.items environ) :if (!= k "JEV_PROXY_ROSTER_FILE") k v)))
  (assert (isinstance assembled Unassembled) assembled)
  (assert (in "JEV_PROXY_ROSTER_FILE" assembled.reason) assembled.reason))
