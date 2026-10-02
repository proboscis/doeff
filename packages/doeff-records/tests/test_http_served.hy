;; 記録の service の GET /served(#2742)— 動いている process が走っている木の commit と世代、配っている表の宣言の要約を答える口。
;; 本物の待ち受け(127.0.0.1 の空き port)に doeff の HTTP の effect で要求を送って確かめる。置き場は共有の memory。
;;   - 身元の無い(名簿に無い token の)GET /served が 200 で答え、本文は単体の GET の実物(tests/served-answer.json)と同じ
;;   - 表の要約は、表ごとの宣言の repr の sha256(この検が自分で数え直した値)と一致する
;;   - 走っている木を渡さない口(served = None)は commits と instance を null で答え、要約は答える
;;   - 置き場が落ちている間も(記録の操作は 503)、/served は 200 で答える
;;   - /served は表の用意を問わない(用意の読みで落ちる世界でも答える — 反例の handler)
(require doeff-hy.macros [deftest defhandler defk <- val])
(import hashlib)
(import json)
(import pathlib [Path])
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.http_effects [HttpRequest])
(import doeff_core_effects.http_server_effects [HttpAddress])
(import doeff_time [SimClock])
(import doeff_records.faults [SetStoreOutage])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.service [HttpRequest :as ServiceRequest])
(import doeff_records.http_server [records-server-config RecordsServing ServedBuild RepoCommit PreparedHandlers REQUEST-MAX-BYTES
                                   answer-with ready-handlers start-records-server])
(import tests.interpreters [law-roster sim-request-handlers])

;; 単体の GET /served の実物(下の BUILD を渡した口が LAW-SCHEMA で答えた本文 — 表の宣言の形が変われば要約も変わる)。
(val FIXTURE (/ (. (Path __file__) parent) "served-answer.json"))
(val BUILD (ServedBuild :commits #((RepoCommit :repo "records-app" :commit (* "a" 40)) (RepoCommit :repo "doeff" :commit (* "b" 40)))
                        :instance "3-0123456789ab"))
(val OUTAGE-DETAIL "記録の service の置き場が落ちている(/served の検の筋書き)")


(defk get-json [url path]
  {:pre [(: url str) (: path str)] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "名簿に無い token を付けて GET を送り、#(status 本文の JSON) を読むため(身元を問わない口かを見る)。"
  (<- response (HttpRequest "GET" (+ url path) :headers {"Authorization" "Bearer not-in-roster"} :max-retries 0))
  #(response.status (json.loads response.text)))


(defk served-of [served]
  {:pre [(: served (| ServedBuild None))] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "走っている木 served を渡した口を共有の memory の置き場の上に開き、GET /served を 1 回読んで閉じるため。"
  (val store (MemoryStore LAW-SCHEMA))
  (val server (start-records-server (run (records-server-config LAW-SCHEMA (fn [writer] (memory-records-handler store writer))
                                                         :request-handlers (sim-request-handlers (SimClock)) :served served))))
  (try
    (<- seen tuple (with_handlers [(await-handler) (http-production-handler)] (get-json server.url "/served")))
    (finally (.close server)))
  seen)


(deftest test-served-answers-the-build-and-the-schema-digests-without-identity
  (<- seen tuple (served-of BUILD))
  (val status (get seen 0))
  (val body (get seen 1))
  (assert (= status 200) seen)
  (assert (= body (json.loads (.read-text FIXTURE :encoding "utf-8"))) body)
  ;; 要約 = 表ごとの宣言の repr の sha256(評価を数え直して比べる — 評価を変えると赤)。
  (assert (= (get body "schemaDigests")
             (dfor #(table declaration) (.items LAW-SCHEMA.tables)
                   (str table) (.hexdigest (hashlib.sha256 (.encode (repr declaration) "utf-8")))))
          body))


(deftest test-served-answers-null-when-the-build-is-unknown
  (<- seen tuple (served-of None))
  (val status (get seen 0))
  (val body (get seen 1))
  (assert (= status 200) seen)
  (assert (is (get body "commits") None) body)
  (assert (is (get body "instance") None) body)
  (assert (= (sorted (get body "schemaDigests")) (sorted (gfor t LAW-SCHEMA.tables (str t)))) body))


(deftest test-served-answers-while-the-store-is-down
  ;; 置き場が落ちている間: 記録の操作は 503・/served は 200(使い手は障害の間も動いている版を読める)。
  (val store (MemoryStore LAW-SCHEMA))
  (val server (start-records-server (run (records-server-config LAW-SCHEMA (fn [writer] (memory-records-handler store writer))
                                                         :request-handlers (sim-request-handlers (SimClock)) :served BUILD))))
  (try
    (<- (with_handlers [(memory-records-handler store MAKER)] (SetStoreOutage OUTAGE-DETAIL)))
    (<- record-op (with_handlers [(await-handler) (http-production-handler)]
                                 (HttpRequest "POST" (+ server.url "/v1/records/put-row")
                                              :headers {"X-Records-Writer" MAKER}
                                              :body {"table" "parts" "key" ["k"] "value" {"label" "a"} "expect" {"kind" "any"}}
                                              :max-retries 0)))
    (<- served tuple (with_handlers [(await-handler) (http-production-handler)] (get-json server.url "/served")))
    (finally (.close server)))
  (assert (= record-op.status 503) #(record-op.status record-op.text))
  (assert (= (get served 0) 200) served)
  (assert (= (get served 1 "instance") "3-0123456789ab") served))


(defhandler prepared-handlers-fail
  ;; 反例の世界: 表の用意の読みが落ちる(用意を待つ口はここで例外になる)。
  (PreparedHandlers [] (raise (RuntimeError "表の用意を読んだ"))))


(deftest test-served-does-not-ask-whether-the-store-is-prepared
  ;; /served は表の用意を問わない — 用意の読みで落ちる世界でも 200。同じ世界で /readyz は用意を読むので落ちる(反例が効いていることを見る)。
  (val serving (RecordsServing :address (HttpAddress :host "127.0.0.1" :port 0) :schema LAW-SCHEMA :roster (law-roster)
                               :prepare (ready-handlers (fn [writer] None)) :request-handlers #() :max-bytes REQUEST-MAX-BYTES
                               :maintenance None :stop-poll-seconds 0.1 :drain-seconds 0.0 :served BUILD))
  (<- answer (with_handlers [prepared-handlers-fail] (answer-with serving (ServiceRequest "GET" "/served" b""))))
  (assert (= answer.status 200) answer)
  (assert (= (get (json.loads answer.body) "instance") "3-0123456789ab") answer)
  (import pytest)
  (with [(pytest.raises RuntimeError)]
    (<- _ (with_handlers [prepared-handlers-fail] (answer-with serving (ServiceRequest "GET" "/readyz" b""))))))
