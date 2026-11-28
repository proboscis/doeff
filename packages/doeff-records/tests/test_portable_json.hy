;; 書きの値の数の断り(_admission.hy の check-portable-json): Rust の serde_json が読めないか別の値に読む数(NaN・Infinity・-Infinity・
;; -0.0・i64 の下限から u64 の上限の外の整数)を、書きの effect を作る時に断る(ValueError — HTTP の口では 400 malformed)。
;; 普通の数(小さな浮動小数・大きな浮動小数・i64::MIN..=u64::MAX の整数)は今までどおり通り、正規の綴りも変わらない。
(require doeff-hy.macros [deftest val])
(import json)
(import pytest)
(import doeff [run with_handlers])
(import doeff_core_effects [scheduled inline-compute-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_records [AppendEvent EventAppend PutRow RowWrite ExpectAbsent ExpectAny])
(import doeff_records._admission [canonical-json check-portable-json])
(import doeff_records._laws [LAW-SCHEMA MAKER])
(import doeff_records [MemoryStore memory-records-handler])
(import doeff_records._service [RecordsService respond HttpRequest :as ServiceRequest])

(val I64-MIN (- (** 2 63)))
(val U64-MAX (- (** 2 64) 1))

(val ACCEPTED [1e-5 1e-6 1e16 0.0 0 -0 1.5 -2.5 I64-MIN U64-MAX (- (** 2 63) 1) (** 2 63) True False None])
(val REFUSED [(float "nan") (float "inf") (float "-inf") -0.0 (** 2 64) (+ (** 2 64) 1) (- I64-MIN 1) (** 10 30)])


;; --- 検めの関数 -----------------------------------------------------------------------------------------------

(deftest test-portable-numbers-pass-the-check
  (for [number ACCEPTED]
    (check-portable-json {"n" number "deep" [{"m" number}]} "値")))


(deftest test-non-portable-numbers-are-refused-even-when-nested
  (for [number REFUSED]
    (for [value [{"n" number} {"deep" [{"m" [1 number]}]} [number]]]
      (with [raised (pytest.raises ValueError)]
        (check-portable-json value "値"))
      (assert (in "値" (str raised.value)) (str raised.value)))))


(deftest test-the-canonical-spelling-of-accepted-numbers-is-unchanged
  (assert (= (canonical-json {"a" 1e-5 "b" 1e16 "c" U64-MAX "d" I64-MIN})
             (+ "{\"a\":1e-05,\"b\":1e+16,\"c\":" (str U64-MAX) ",\"d\":" (str I64-MIN) "}"))))


;; --- 作る時の断り(置き場を問わない)---------------------------------------------------------------------

(deftest test-a-write-carrying-a-non-portable-number-is-refused-when-built
  (for [number REFUSED]
    (for [build [(fn [] (AppendEvent "journal" "k1" {"n" number}))
                 (fn [] (EventAppend "journal" "k1" {"outer" [{"n" number}]}))
                 (fn [] (PutRow "parts" #("p1") {"label" number} (ExpectAbsent)))
                 (fn [] (RowWrite "parts" #("p1") {"label" {"deep" [number]}} (ExpectAny)))]]
      (with [raised (pytest.raises ValueError)]
        (build)))))


;; --- HTTP の口(memory の置き場の上)---------------------------------------------------------------------

(defn request [#^ str operation #^ str body]
  "本文を綴りのまま渡す — NaN・Infinity は Python の json.dumps が綴りの外の語のまま書く形を、書き手が送る形として使う。"
  (ServiceRequest "POST" (+ "/v1/records/" operation) (.encode body "utf-8") :writer MAKER))


(defn answers-of [service requests]
  (lfor one requests
        :setv answer (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler]
                                                    (respond service one))))
        #(answer.status (json.loads answer.body))))


(deftest test-the-service-refuses-non-portable-numbers-with-400-and-keeps-serving
  (val store (MemoryStore LAW-SCHEMA))
  (val service (RecordsService LAW-SCHEMA (fn [writer] (memory-records-handler store writer))))
  (val spelled ["NaN" "Infinity" "-Infinity" "-0.0" (str (** 2 64)) (str (- I64-MIN 1))])
  (val refused
    (answers-of service
                (+ (lfor #(index word) (enumerate spelled)
                         (request "append-event" (.format "{{\"stream\":\"journal\",\"idempotencyKey\":\"bad{}\",\"body\":{{\"n\":{}}}}}" index word)))
                   (lfor word spelled
                         (request "put-row" (.format "{{\"table\":\"parts\",\"key\":[\"p1\"],\"value\":{{\"label\":{}}},\"expect\":{{\"kind\":\"absent\"}}}}" word))))))
  (for [#(status body) refused]
    (assert (= #(status (get body "error")) #(400 "malformed")) #(status body)))
  (val bounds (.format "[1e-05,1e-06,1e+16,{},{}]" I64-MIN U64-MAX))
  (val clean
    (answers-of service
                [(request "append-event" (+ "{\"stream\":\"journal\",\"idempotencyKey\":\"ok\",\"body\":{\"n\":" bounds "}}"))
                 (request "put-row" (+ "{\"table\":\"parts\",\"key\":[\"p2\"],\"value\":{\"label\":" bounds "},\"expect\":{\"kind\":\"absent\"}}"))
                 (request "read-row" "{\"table\":\"parts\",\"key\":[\"p2\"]}")
                 (request "read-events" "{\"stream\":\"journal\"}")]))
  (assert (all (gfor #(status _) clean (= status 200))) clean)
  (setv #(_ _ row events) clean)
  (assert (= (get row 1 "value" "label") [1e-5 1e-6 1e16 I64-MIN U64-MAX]) row)
  ;; 断った書きは 1 つも積まれていない — 列に在るのは通った追記 1 つだけ。
  (assert (= (lfor item (get events 1 "items") (get item "idempotencyKey")) ["ok"]) events))
