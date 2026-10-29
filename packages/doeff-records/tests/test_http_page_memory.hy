;;; 記録の client が頁を何度読んでも、読んだ頁の本文と値が読みの後に残らない事 — 頁を N 回読んだ常駐量(RSS)の伸びが頁の数に比例しない。
;;; 前は答え手の組(http-production-handler の Await)が、待ち終えた httpx の応答を scheduler の約束の表に残し、id を 1024 個払い出すごとの
;;; 掃除まで放さなかった(doeff_core_effects._handlers の _AwaitedValue)。1 頁 約 3 MiB の列を位置 0 から 238 頁読む job の RSS が
;;; 131 → 895 MiB に育ち、gc の後も戻らなかった(表は scheduler の持ち物で、循環ではない)。
;;; 検は模擬の HTTP の相手(127.0.0.1 の空き port)で 1 頁の本文を大きくし、本番と同じ答え手の組で読む。
(require doeff-hy.macros [deftest deff defk <- val])
(import gc)
(import json)
(import sys)
(import threading)
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler http-client-factory])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records [ReadEvents])
(import doeff_records [Events])
(import doeff_records [RecordsEndpoint http-records-handler records-unwaited])

;; 1 頁の出来事の数と、出来事 1 つの本文の大きさ(本番の読みと同じ 1,000 件の頁・1 頁の本文が約 1.1 MiB)。
(val PAGE-EVENTS 1000)
(val EVENT-TEXT-BYTES 1024)
;; 測る前に読む頁の数(allocator の置き場と接続の持ち方が落ち着くまで)と、測る頁の数。
(val WARM-PAGES 10)
(val PAGES 100)
;; 測る頁を読む間の RSS の伸びの上限を、1 頁の本文の大きさの何倍で置くか。前の形は 1 頁ごとに本文の bytes と str(本文の約 2 倍)が
;; 残り、100 頁で本文の約 200 倍伸びた。読み終えた頁を放す形の伸びは頁の数に依らず、本文の 1 倍に届かない。
(val GROWTH-LIMIT-PAGES 4)


(defn page-payload []
  "模擬の相手が返す read-events の答えの本文(wire の events の形 — 出来事 PAGE-EVENTS 個・1 つ EVENT-TEXT-BYTES の本文)。"
  (.encode (json.dumps {"kind" "events"
                        "items" (lfor sequence (range 1 (+ PAGE-EVENTS 1))
                                  {"stream" "journal" "sequence" sequence "idempotencyKey" (+ "k" (str sequence))
                                   "body" {"text" (* "x" EVENT-TEXT-BYTES)} "writer" "maker" "at" sequence})
                        "lastSequence" PAGE-EVENTS})
           "utf-8"))


(defclass FixedPage [BaseHTTPRequestHandler]
  "記録の service の代役: どの要求にも 200 と同じ頁の本文 server.payload で答える。"
  (setv protocol-version "HTTP/1.1")
  (deff do-POST [self]  ; defk にできない: http.server が要求ごとの thread で呼ぶ素の method
    {:pre [(: self FixedPage)] :post [(: % None)] :tags {:context "records" :role "foundation"}}
    (.read self.rfile (int (.get self.headers "Content-Length" "0")))
    (.send-response self 200)
    (.send-header self "Content-Type" "application/json")
    (.send-header self "Content-Length" (str (len self.server.payload)))
    (.end-headers self)
    (.write self.wfile self.server.payload)
    None)
  (deff log-message [self #* args]  ; defk にできない: http.server が要求ごとに呼ぶ log の口(検の出力を要求の行で埋めない)
    {:pre [(: self FixedPage) (: args tuple)] :post [(: % None)] :tags {:context "records" :role "foundation"}}
    None))


(defn resident-bytes []
  "この process の今の常駐量(/proc/self/status の VmRSS)— gc を回した後に読む。"
  (gc.collect)
  (with [status (open "/proc/self/status")]
    (for [line status]
      (when (.startswith line "VmRSS:")
        (return (* 1024 (int (get (.split line) 1))))))))


(defk read-pages [n]
  {:pre [(: n int) (> n 0)] :post [(: % None)] :tags {:context "records" :role "program"}}
  "列を位置 0 から n 回読む(読んだ頁は次の読みで捨てる)。"
  (for [_ (range n)]
    (<- page Events (ReadEvents "journal" :after 0 :limit PAGE-EVENTS))
    (assert (= (len page.items) PAGE-EVENTS) page.last-sequence))
  None)


(defk measured-growth [warm pages]
  {:pre [(: warm int) (: pages int)] :post [(: % int)] :tags {:context "records" :role "program"}}
  "warm 頁を読んでから pages 頁を読み、その間の RSS の伸びを返す(1 つの run の中で測る — run が終わると scheduler の状態ごと放される)。"
  (<- (read-pages warm))
  (val before (resident-bytes))
  (<- (read-pages pages))
  (- (resident-bytes) before))


(deftest test-reading-many-pages-does-not-keep-each-page
  ;; 失敗ケース: 読んだ頁の応答の本文が読みの後も残ると、RSS が頁の数に比例して伸びる(前の形で 100 頁 約 200 MiB)。直した後は、読み終えた
  ;; 頁は次の読みまでに放され、伸びは頁の数に依らない。
  (when (not (.startswith sys.platform "linux"))
    (pytest.skip "常駐量は Linux の /proc/self/status で読む"))
  (val server (ThreadingHTTPServer #("127.0.0.1" 0) FixedPage))
  (setv server.payload (page-payload))
  (.start (threading.Thread :target server.serve-forever :daemon True))
  (val endpoint (RecordsEndpoint (+ "http://127.0.0.1:" (str (get server.server-address 1))) :request-timeout 10.0))
  (try
    (do
      (val growth (run (scheduled (with_handlers [await-handler
                                                  (http-production-handler :client-factory (fn [] (http-client-factory :trust-env False)))
                                                  (sim-time-handler :clock (SimClock)) records-unwaited (http-records-handler endpoint)]
                                                 (measured-growth WARM-PAGES PAGES)))))
      (val limit (* GROWTH-LIMIT-PAGES (len server.payload)))
      (assert (< growth limit)
              (.format "{} 頁(1 頁 {} byte)を読む間に RSS が {:.1f} MiB 伸びた(上限 = 1 頁の本文の {} 倍の {:.1f} MiB)"
                       PAGES (len server.payload) (/ growth 1048576) GROWTH-LIMIT-PAGES (/ limit 1048576))))
    (finally (.shutdown server) (.server-close server))))
