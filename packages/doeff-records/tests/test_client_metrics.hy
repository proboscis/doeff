;; 記録の client が、送った要求 1 つごとに要求の種 × 結果を計器へ数えること(#2740)。記録の service に届かなかった書きは service の
;; 計器(records_requests_*)には出ないので、client の側でだけ数えられる。数えは endpoint に計器の答え手(meter)を渡した時だけで、
;; 渡さない endpoint は計器の effect を 1 つも出さない(答え手を持たない今の使い手が壊れない)。
;; 記録の service と間の口の代役は、client の送る HttpRequest に台本で答える(届かない窓・status と本文)。
(require doeff-hy.macros [deftest defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import json)
(import dataclasses [dataclass])
(import doeff [EffectBase run with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed HttpFailureKind])
(import doeff_core_effects.meter_effects [MeterSettings MeterSnapshot ReadMeter])
(import doeff_core_effects.memory_meter [memory-meter-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.effects [PutRow ReadRow])
(import doeff_records.values [ExpectAny])
(import doeff_records.wire [CLIENT-ANSWER-METRICS])
(import doeff_records.http_client [RecordsEndpoint WireError http-records-handler zero-client-metrics])
(import doeff_records.http_client [records-unwaited])

;; 台本の答え 1 つ: 届かない(接続できない)か、#(status 本文)。本文 None = JSON でない本文(間の proxy の HTML の代役)。
(val UNREACHABLE "unreachable")
(val WRITTEN #(200 {"kind" "written" "version" 1 "value" {"note" "a"}}))
(val MISSING #(200 {"kind" "missing"}))
(val STORE-UNAVAILABLE #(503 {"error" "store-unavailable" "reason" "置き場に届かない(検)"}))
(val BAD-GATEWAY #(502 None))
(val HTML-401 #(401 None))

(val WRITE (PutRow "notes" #("a") (FrozenMap {"note" "a"}) (ExpectAny)))
(val READ (ReadRow "notes" #("a")))


(defrecord ClientRun
  "台本の上で client を 1 回走らせた答え: answers = 撃った effect ごとの答えの型の名か、上がった例外の型の名(撃った順)・
   counted = 走り終えた後の計器の断面のうち 1 つ以上数えた系列(名 → 数)・names = 断面に在る系列の名(0 の物を含む)。"
  (#^ tuple answers)
  (#^ (get FrozenMap float) counted)
  (#^ frozenset names))


(defk payload-of [body]
  {:pre [(: body (| dict None))] :post [(: % bytes)] :tags {:context "records" :role "foundation"}}
  "台本の本文を答えの byte にするため(None = JSON でない本文)。"
  (if (is body None) b"<html>bad gateway</html>" (.encode (json.dumps body) "utf-8")))


(defhandler scripted-records-http [#^ list replies]
  ;; 引数に残す理由: 検ごとの台本(送られた順の答えの列)。
  "client の送る HttpRequest に、台本の答えを送られた順に 1 つずつ返すため(記録の service と間の口の代役 — 届かない窓と status を作る)。"
  {:tags {:context "records" :role "foundation"}}
  (HttpRequest []
    (val reply (.pop replies 0))
    (if (= reply UNREACHABLE)
        (resume (HttpFailed :url effect.url :detail "接続できない(検の届かない窓)" :kind HttpFailureKind.CONNECT-FAILED))
        (do (<- payload bytes (payload-of (get reply 1)))
            (resume (HttpResponse (get reply 0) {} payload (.decode payload "utf-8" "replace") effect.url 0.0))))))


(defk attempted [ask]
  {:pre [(: ask EffectBase)] :post [(: % str)] :tags {:context "records" :role "foundation"}}
  "公開 effect を 1 つ撃ち、答えの型の名か、上がった例外の型の名を返すため(client は 502・401 などの断りを一般の失敗 WireError にする)。"
  (try
    (do (<- answer ask)
        (. (type answer) __name__))
    (except [problem WireError]
      (. (type problem) __name__))))


(defk attempted-in-order [asks]
  {:pre [(: asks tuple)] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "asks を先頭から順に 1 つずつ撃ち(台本は送られた順に答える)、答えの型の名の tuple を返すため。"
  (if (not asks)
      #()
      (do (<- head str (attempted (get asks 0)))
          (<- rest tuple (attempted-in-order (cut asks 1 None)))
          (+ #(head) rest))))


(defk metered-asks [endpoint asks]
  {:pre [(: endpoint RecordsEndpoint) (: asks tuple)] :post [(: % ClientRun)] :tags {:context "records" :role "foundation"}}
  "client の閉じた系列を 0 で置いてから asks を順に撃ち、答えの型の名と、走り終えた後の計器の断面を返すため。"
  (<- (zero-client-metrics endpoint))
  (<- answers tuple (attempted-in-order asks))
  (<- snapshot MeterSnapshot (ReadMeter))
  (ClientRun :answers answers
             :counted (FrozenMap (gfor #(name count) (.items snapshot.counters) :if (> count 0.0) #(name count)))
             :names (frozenset snapshot.counters)))


(defk metered-client-run [replies asks]
  {:pre [(: replies tuple) (: asks tuple)] :post [(: % ClientRun)] :tags {:context "records" :role "foundation"}}
  "計器を渡した endpoint の client を台本 replies の上で走らせ、ClientRun を run の結果として返すため。client に渡す計器と、断面を
   読む外側の計器は、同じ run の中の同じ答え手(memory-meter-handler の断面は run ごとに 1 つ)。"
  (val endpoint (RecordsEndpoint "http://records.test" :meter (memory-meter-handler (MeterSettings))))
  (run (scheduled (with-handlers [(state) (memory-meter-handler (MeterSettings)) (scripted-records-http (list replies))
                                  records-unwaited (http-records-handler endpoint)]
                                 (metered-asks endpoint asks)))))


(defk unmetered-asks [endpoint asks]
  {:pre [(: endpoint RecordsEndpoint) (: asks tuple)] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "計器を渡さない endpoint で、閉じた系列の 0 置きと asks を撃ち、答えの型の名を返すため(どちらも計器の effect を出さないこと)。"
  (<- (zero-client-metrics endpoint))
  (<- answers tuple (attempted-in-order asks))
  answers)


(deftest test-the-client-counts-each-request-by-kind-and-outcome
  ;; 届かない窓の書き 3 件は unreachable に 3(service の計器には出ない数)。service の 503(置き場に届かない)は 503・間の proxy の
  ;; 502 と 401 は閉じた系列の外なので other で、どちらも一般の失敗 WireError(401 の系列も名の付いた例外も無い — #2986)。読みは read で
  ;; 数える。閉じた系列(種 2 × 結果 7)は全部、断面に在る。
  (<- ran ClientRun (metered-client-run #(UNREACHABLE UNREACHABLE UNREACHABLE WRITTEN STORE-UNAVAILABLE BAD-GATEWAY MISSING HTML-401)
                                        #(WRITE WRITE WRITE WRITE WRITE WRITE READ READ)))
  (assert (= ran.answers #("Unreachable" "Unreachable" "Unreachable" "Written" "Unreachable" "WireError" "Missing" "WireError"))
          ran.answers)
  (assert (= ran.counted (FrozenMap {"records_client_requests_write_unreachable" 3.0 "records_client_requests_write_200" 1.0
                                     "records_client_requests_write_503" 1.0 "records_client_requests_write_other" 1.0
                                     "records_client_requests_read_200" 1.0 "records_client_requests_read_other" 1.0}))
          ran.counted)
  (assert (= ran.names (frozenset CLIENT-ANSWER-METRICS)) ran.names)
  (assert (= (len CLIENT-ANSWER-METRICS) 14) CLIENT-ANSWER-METRICS))


(deftest test-an-endpoint-without-a-meter-emits-no-meter-effect
  ;; 計器を渡さない endpoint(今の使い手の形)は計器の effect を出さない — 計器の答え手を並べずに走り切る(出せば答え手の無い effect で落ちる)。
  (val endpoint (RecordsEndpoint "http://records.test"))
  (val answers (run (scheduled (with-handlers [(state) (scripted-records-http [UNREACHABLE WRITTEN]) records-unwaited (http-records-handler endpoint)]
                                              (unmetered-asks endpoint #(WRITE WRITE))))))
  (assert (= answers #("Unreachable" "Written")) answers))
