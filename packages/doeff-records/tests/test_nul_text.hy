;; 書きの値の NUL 文字(U+0000)の断り: PostgreSQL の jsonb と text は NUL を持てない(jsonb は 22P05
;; `\u0000 cannot be converted to text`・text は 22021)。前は記録の service が NUL を含む書きを置き場まで渡し、文が落ちて 500 を答えた
;; (書き手は同じ物を送り直し続け、落ちた文の後始末で同じ接続の prepared statement が消えて、ほかの書き手の書きも 26000 で落ちた —
;; 2026-10-30 21:17 UTC から)。今は書きの effect を作る時に断る(ValueError — HTTP の口では 400 malformed)ので、memory の置き場の
;; 模擬も PostgreSQL の置き場も同じ所で断り、置き場の接続には触れない。
(require doeff-hy.macros [deftest val])
(import json)
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects [scheduled inline-compute-handler])
(import doeff_core_effects [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records [AppendEvent AppendEvents EventAppend PutRow PutRows RowWrite PutRowsAppendEvents ExpectAbsent ExpectAny])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records [MemoryStore memory-records-handler])
(import doeff_records._service [RecordsService respond HttpRequest :as ServiceRequest])
(import doeff_records._wire [WRITER-HEADER])
(import tests.interpreters [postgres-connections pg-skip-reason records-handler-for run-sql prepared-fresh-store])
(import doeff_records._pg [drop-records-tables])

(val NUL "\u0000")
;; 本番で落ちた出来事の本文の形(道具の出力の末尾に NUL が付いた文字列)。
(val TAIL (+ "{'fund_jp': 123, 'depth': 0} t 25 DONE\n" NUL))


;; --- 作る時の断り(置き場を問わない)---------------------------------------------------------------------

(deftest test-a-write-carrying-nul-is-refused-when-built
  ;; 出来事の本文・行の値・行の鍵・冪等キーのどこに NUL が在っても、effect を作る時に ValueError で断る(深い所の文字列と object の名も)。
  (for [build [(fn [] (AppendEvent "journal" "k1" {"text" TAIL}))
               (fn [] (AppendEvent "journal" "k1" {"outer" [{"inner" ["ok" TAIL]}]}))
               (fn [] (AppendEvent "journal" "k1" {(+ "name" NUL) 1}))
               (fn [] (AppendEvent "journal" (+ "k" NUL) {"n" 1}))
               (fn [] (EventAppend "journal" "k1" {"text" TAIL}))
               (fn [] (AppendEvents #((EventAppend "journal" "k1" {"text" TAIL}))))
               (fn [] (PutRow "parts" #("p1") {"label" TAIL} (ExpectAbsent)))
               (fn [] (PutRow "parts" #("p1") {"label" {"deep" [TAIL]}} (ExpectAbsent)))
               (fn [] (PutRow "parts" #((+ "p" NUL)) {"label" "a"} (ExpectAbsent)))
               (fn [] (RowWrite "parts" #("p1") {"label" TAIL} (ExpectAny)))
               (fn [] (PutRows #((RowWrite "parts" #("p1") {"label" TAIL} (ExpectAny)))))
               (fn [] (PutRowsAppendEvents #((RowWrite "parts" #("p1") {"label" "a"} (ExpectAny)))
                                           #((EventAppend "journal" "k1" {"text" TAIL}))))]]
    (with [raised (pytest.raises ValueError)]
      (build))
    (assert (in "NUL" (str raised.value)) (str raised.value))))


(deftest test-text-without-nul-is-still-accepted
  ;; 断るのは NUL だけ — ほかの制御文字・U+FFFD(書き手が NUL を置き換える字)・空の文字列は今までどおり通る。
  (val store (MemoryStore LAW-SCHEMA))
  (val body {"text" "t 25 DONE\n\t\u0001�" "empty" "" "nested" [{"k" "v"}]})
  (val answer (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)]
                                             (AppendEvent "journal" "k1" body)))))
  (assert (= (. answer sequence) 1) answer))


;; --- HTTP の口(memory と PostgreSQL の置き場の上)--------------------------------------------------------

(defn request [#^ str operation #^ dict body]
  (ServiceRequest "POST" (+ "/v1/records/" operation) (.encode (json.dumps body) "utf-8") :writer MAKER))


(val NUL-REQUESTS
  [(request "append-events" {"appends" [{"stream" "journal" "idempotencyKey" "k1" "body" {"text" TAIL}}]})
   (request "append-event" {"stream" "journal" "idempotencyKey" "k1" "body" {"text" TAIL}})
   (request "put-row" {"table" "parts" "key" ["p1"] "value" {"label" TAIL} "expect" {"kind" "absent"}})
   (request "put-rows" {"writes" [{"table" "parts" "key" ["p1"] "value" {"label" TAIL} "expect" {"kind" "any"}}]})])

(val CLEAN-REQUESTS
  [(request "append-events" {"appends" [{"stream" "journal" "idempotencyKey" "k2" "body" {"text" "DONE"}}]})
   (request "put-row" {"table" "parts" "key" ["p2"] "value" {"label" "a"} "expect" {"kind" "absent"}})
   (request "read-row" {"table" "parts" "key" ["p2"]})
   (request "read-events" {"stream" "journal"})])


(defn answers-of [service answerers requests]
  "service の口へ requests を順に 1 つずつ渡し、#(status 本文の JSON) の列を返す(本物の口と同じ respond — socket は開かない)。"
  (lfor one requests
        :setv answer (run (scheduled (with_handlers (+ [(sim-time-handler :clock (SimClock)) inline-compute-handler] answerers)
                                                    (respond service one))))
        #(answer.status (json.loads answer.body))))


(defn check-nul-refused-then-clean-served [service answerers]
  (for [#(status body) (answers-of service answerers NUL-REQUESTS)]
    (assert (= #(status (get body "error")) #(400 "malformed")) #(status body)))
  (setv clean (answers-of service answerers CLEAN-REQUESTS))
  (assert (all (gfor #(status _) clean (= status 200))) clean)
  (setv #(_ _ row events) clean)
  (assert (= (get row 1 "value" "label") "a") row)
  ;; 断った書きは 1 つも積まれていない — 列に在るのは NUL の無い追記 1 つだけ。
  (assert (= (lfor item (get events 1 "items") (get item "idempotencyKey")) ["k2"]) events))


(deftest test-the-memory-service-refuses-nul-with-400-and-keeps-serving
  (val store (MemoryStore LAW-SCHEMA))
  (check-nul-refused-then-clean-served (RecordsService LAW-SCHEMA (fn [writer] (memory-records-handler store writer))) []))


(deftest test-the-postgres-service-refuses-nul-with-400-and-the-same-connection-keeps-serving
  ;; 接続の貸し出しを 1 本にし、NUL の書きを断った後の書きと読みが同じ接続を通る形にする(前は 22P05 の 500 の後、同じ接続の
  ;; prepared statement doeff_pq_0 が消えて 26000 で落ちた)。
  (when (pg-skip-reason) (pytest.skip (pg-skip-reason)))
  (val connections (postgres-connections :size 1))
  (val store (run-sql connections (prepared-fresh-store False)))
  (try
    (check-nul-refused-then-clean-served (RecordsService LAW-SCHEMA (records-handler-for store)) [(postgres-sql-handler connections)])
    (finally
      (run-sql connections (drop-records-tables store))
      (.close connections))))
