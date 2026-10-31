;; 記録の service が 500 internal(要求の task が答えを決める前か、答えを送る途中で落ちた)で答えた時の HTTP の client の失敗ケース
;; (_http_client.hy の頭の表の 500 internal)。
;; 確かめること:
;;   要求の全部(要求と答えの 12 個と変化の待ち 2 つ)が、500 internal を例外でなく答えの値 Unreachable で返す — 結末の不明な答えの値は
;;   Unreachable だけで、どの要求の答えの型にも在る
;;   行の型の書き put-typed と読み read-typed も、500 internal を答えの型(Unreachable)で返す(呼び手は値で読み分ける)
;;   待つ時間の在る client は、500 internal を置き場の止まりと同じく戻りを待って撃ち直し、撃ち直しの書きは 1 回目と同じ冪等キーを送る
;;   400 malformed(client の実装の誤り)は今までどおり WireError を上げる — 実装の誤りの断りを値に紛れさせない
;; 反例 = 前の client(500 を WireError で上げた)は、1 本目と 2 本目の検で WireError を上げて赤になる。
;; 記録の service の代役は、client の送る HttpRequest に台本の答えを送られた順に返す(test_client_metrics.hy と同じ形)。
(require doeff-hy.macros [deftest defhandler defk <- val var])
(import json)
(import typing [get-args])
(import dataclasses [dataclass])
(import doeff [EffectBase run with-handlers])
(import doeff_core_effects [Get Put scheduled state])
(import doeff_core_effects [HttpRequest HttpResponse])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy._frozen [FrozenMap])
(import doeff_records [ExpectAbsent ExpectAny Unreachable Written WatchCursor])
(import doeff_records [ReadRow ListRows PutRow PutRows RowWrite WatchChanges WatchEvents AppendEvent AppendEvents EventAppend ReadEvents
                       ReadStreamEnd ReadEventByKey ReadBatch CountRows PutRowsAppendEvents AwaitRecordsBack])
(import doeff_records [RecordsEndpoint RequestPatience WireError http-records-handler request-patience-handler records-unwaited])
(import doeff_records._typed [RowType read-typed put-typed])
(import doeff_records._wire [PublicEffect IDEMPOTENCY-HEADER])

;; 記録の service が要求の task の落ちで答える本文(_http_server.hy の answer-arrival の 500 internal と同じ綴り)。
(val INTERNAL #(500 {"error" "internal" "reason" "要求の task が答える前に落ちた"}))
(val MALFORMED #(400 {"error" "malformed" "reason" "本文が読めない(検)"}))
(val WRITTEN #(200 {"kind" "written" "version" 1 "value" {"id" "p1" "label" "a"}}))
(val PATIENCE (RequestPatience :seconds 60.0))
;; 代役が送られた要求の見出しを積む状態の鍵。
(val SENT "scripted-records-http/sent")


(defclass [(dataclass :frozen True)] Part []
  "表 parts の行の型(検の行)。"
  (#^ str id)
  (setv #^ (| str None) label None))

(val PARTS (RowType "parts" Part))


(defhandler scripted-records-http [#^ tuple replies]
  ;; 引数に残す理由: 検ごとの台本(送られた順の答えの列)。
  "client の送る HttpRequest に台本の答えを送られた順に 1 つずつ返し、送られた要求の見出しを状態の鍵 SENT に積むため(記録の service の代役)。"
  {:tags {:context "records" :role "foundation"}}
  (HttpRequest []
    (<- seen (Get SENT))
    (val before (if (is seen None) #() seen))
    (<- (Put SENT (+ before #((dict (or effect.headers {}))))))
    (val reply (get replies (len before)))
    (val payload (.encode (json.dumps (get reply 1)) "utf-8"))
    (resume (HttpResponse (get reply 0) {} payload (.decode payload "utf-8") effect.url 0.0))))


(defhandler records-back-at-once
  ;; 置き場の戻りの問いに、すぐ戻ったと答えるため(本番は戻りを知る土台が答える — 検は撃ち直しの形だけを見る)。
  {:tags {:context "records" :role "foundation"}}
  (AwaitRecordsBack [names]
    (resume None)))


(defk answered-and-sent [ask]
  {:pre [(: ask EffectBase)] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "ask を撃ち、#(答え 送られた要求の見出しの列) を返すため。"
  (<- answer ask)
  (<- sent (Get SENT))
  #(answer sent))


(defn scripted-run [replies program [patience None]]
  "台本 replies の上で client を走らせ、program の答えを返すため(patience = 待つ時間・None は待たない records-unwaited)。"
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) records-back-at-once (scripted-records-http replies)
                                  (if (is patience None) records-unwaited (request-patience-handler patience))
                                  (http-records-handler (RecordsEndpoint "http://records.test" :writer "w"))]
                                 program))))


(defk every-ask []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "公開 effect を 1 つずつ作るため(要求と答えの 12 個と変化の待ち 2 つ — 公開 effect の一覧の全部)。"
  (val part (FrozenMap {"id" "p1" "label" "a"}))
  #((ReadRow "parts" #("p1"))
    (ListRows "parts")
    (PutRow "parts" #("p1") part (ExpectAbsent))
    (PutRows #((RowWrite "parts" #("p1") part (ExpectAbsent))))
    (AppendEvent "journal" "k1" {"n" 1})
    (AppendEvents #((EventAppend "journal" "k2" {"n" 2})))
    (PutRowsAppendEvents #((RowWrite "parts" #("p1") part (ExpectAbsent))) #((EventAppend "journal" "k3" {"n" 3})))
    (ReadEvents "journal")
    (ReadStreamEnd "journal")
    (CountRows "parts")
    (ReadEventByKey "journal" "k1")
    (ReadBatch #((ReadRow "parts" #("p1"))))
    (WatchChanges #("parts") (WatchCursor 1 0) :timeout 0.0)
    (WatchEvents "journal" 0 :timeout 0.0)))


(deftest test-the-ask-table-is-every-public-effect
  ;; 表の種類 = 公開 effect の一覧の全部。client に要求を 1 つ足すと、この表に無い物として赤。
  (<- asks tuple (every-ask))
  (assert (= (frozenset (gfor ask asks (type ask))) (frozenset (get-args PublicEffect)))))


(deftest test-every-request-answers-an-internal-failure-with-unreachable
  ;; 500 internal は、どの要求でも例外でなく Unreachable — 撃った操作と service の理由を名指す。
  (<- asks tuple (every-ask))
  (for [ask asks]
    (val answer (scripted-run #(INTERNAL) ask))
    (assert (isinstance answer Unreachable) #(ask answer))
    (assert (in "500 internal" answer.detail) #(ask answer))
    (assert (in "要求の task が答える前に落ちた" answer.detail) #(ask answer))))


(deftest test-typed-reads-and-writes-answer-an-internal-failure-by-type
  ;; 行の型の書きと読み(呼び手が答えの型で読み分ける層)も、500 internal を Unreachable の値で返す。
  (val written (scripted-run #(INTERNAL) (put-typed PARTS #("p1") (Part :id "p1" :label "a") (ExpectAny))))
  (assert (isinstance written Unreachable) written)
  (val read (scripted-run #(INTERNAL) (read-typed PARTS #("p1"))))
  (assert (isinstance read Unreachable) read))


(deftest test-a-patient-client-resends-an-internal-failure-with-the-same-key
  ;; 待つ時間の在る client は、500 internal の後に置き場の戻りを待って撃ち直す — 撃ち直しの書きは 1 回目と同じ冪等キー(置き場が着いた
  ;; 1 回目の答えを送り直せる)。
  (val seen (scripted-run #(INTERNAL WRITTEN) (answered-and-sent (PutRow "parts" #("p1") (FrozenMap {"id" "p1" "label" "a"}) (ExpectAbsent)))
                          PATIENCE))
  (val answer (get seen 0))
  (val sent (get seen 1))
  (assert (isinstance answer Written) answer)
  (assert (= (len sent) 2) sent)
  (val keys (lfor headers sent (.get headers IDEMPOTENCY-HEADER)))
  (assert (and (is-not (get keys 0) None) (= (get keys 0) (get keys 1))) keys))


(deftest test-a-malformed-refusal-still-raises
  ;; 400 malformed(client の実装の誤り — 撃ち直しても同じ断り)は今までどおり WireError を上げる。
  (var raised None)
  (try
    (scripted-run #(MALFORMED) (ReadRow "parts" #("p1")))
    (except [error WireError]
      (:= raised error)))
  (assert (is-not raised None))
  (assert (in "malformed" (str raised)) raised))
