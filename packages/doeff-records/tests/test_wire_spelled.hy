;;; 記録の service の答えの本文(wire.answer-text)が、行と変更の値を置き場の保存の綴りのまま継ぎ(値を解かず・凍らせず・戻さず・綴り
;;; 直さない)、前の綴り(encode-answer の object を json.dumps で綴った物)と JSON として同じ本文を返す事(_values.hy の頭の註の保存の綴り)。
;;; 前は欄を絞らない一覧と変更の読みのたびに、変わっていない行も 1 行ごとに json.loads・凍らせ・thaw-json・json.dumps を回していた。
;;; - memory の置き場と PostgreSQL の置き場の両方で、一覧(欄を絞らない・絞る・上から読む)と変更の読みの答えの新旧の本文が JSON として同じ。
;;; - 欄を絞らない一覧と変更の読みの行は保存の綴りを持ち、PostgreSQL の置き場の行は答えを綴った後も値を解いていない(欄 value が無い)。
;;; - 綴りだけを持つ行は、値を読まれた時に 1 度だけ解き、値から作った行と等しい(等しさ・repr・match は前のまま)。
;;; - PostgreSQL の置き場が読んだ出来事(pg.events-of)は payload の綴りを持ち、答えの本文を綴った後も payload を解いていない(欄 body が
;;;   無い)。欄を読まれた時に 1 度だけ解き、値から作った出来事と等しい。前は出来事の頁を読むたびに、1 件ごとに payload を解き・本文を深く
;;;   凍らせ・戻し・Python の再帰で綴り直していた(1 頁 1,000 件・1 件 600 byte で読み解きと綴りが CPU 約 0.1 秒)。
(require doeff-hy.macros [deftest defk <- val])
(import json)
(import pytest)
(import doeff [run])
(import doeff_hy._frozen [FrozenMap thaw-json])
(import doeff_records [ExpectAbsent ExpectVersion Row RowChanged Page Changes Written WatchCursor Event Events]
        doeff_records._values [stored-row stored-change stored-text])
(import doeff_records._pg [events-of])
(import doeff_records [ListRows WatchChanges PutRow])
(import doeff_records._laws [MAKER LawHarness as-writer])
(import doeff_records._wire [encode-answer answer-text])
(import tests.interpreters [build-interpreter session-dsn PG-DSN-VARIABLE pg-skip-reason])

(val PG-DSN (session-dsn PG-DSN-VARIABLE))
;; 綴りの境を試す値(ASCII の外の字・引用符と逆斜線・小数・入れ子の object と array)。
(val ODD-LABEL "日本語 \"引用\" \\ é\t")
(val NESTED-GRANT {"z" 1 "a" [1 {"b" "c"} 2.5] "m" {"k" [True False]}})


(defk spelled-answers [harness]
  {:pre [(: harness LawHarness)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "行を書いて、一覧(欄を絞らない・絞る・上から読む)と変更の読みの答えを集めるため。"
  (<- start (as-writer harness MAKER (ListRows "parts")))
  (<- w1 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" ODD-LABEL "color" "red" "note" 7 "grant" NESTED-GRANT})
                                          (ExpectAbsent))))
  (<- w2 (as-writer harness MAKER (PutRow "parts" #("p2") (FrozenMap {"label" "b" "color" "blue" "note" 3}) (ExpectAbsent))))
  (<- w3 (as-writer harness MAKER (PutRow "parts" #("p1") (FrozenMap {"label" "é2"}) (ExpectVersion 1))))
  (assert (all (gfor w #(w1 w2 w3) (isinstance w Written))) #(w1 w2 w3))
  (<- whole (as-writer harness MAKER (ListRows "parts")))
  (<- narrowed (as-writer harness MAKER (ListRows "parts" :fields #("label"))))
  (<- top (as-writer harness MAKER (ListRows "parts" :descending-by "note" :limit 1)))
  (<- changes (as-writer harness MAKER (WatchChanges #("parts") (WatchCursor start.epoch start.sequence))))
  #(whole narrowed top changes))


(defn answers-on [#^ str name]
  "解釈器 name の置き場で spelled-answers を回した答え。"
  (setv built (build-interpreter name))
  (try
    (built.run (spelled-answers (built.harness)))
    (finally (built.close))))


(defn assert-same-json [answers]
  "答えごとに、新しい本文(answer-text)と前の本文(encode-answer の json.dumps)が JSON として同じ事を確かめる。"
  (for [answer answers]
    (setv spelled (run (answer-text answer)))
    (setv plain (json.dumps (run (encode-answer answer)) :ensure-ascii False :separators #("," ":")))
    (assert (= (json.loads spelled) (json.loads plain)) #(spelled plain))))


(defn carried-rows [answers]
  "欄を絞らない一覧と変更の読みの答えが運ぶ行と変更(保存の綴りを継ぐ物)。"
  (setv #(whole _ _ changes) answers)
  (assert (and (isinstance whole Page) (isinstance changes Changes)) answers)
  (assert (and (= (len whole.rows) 2) (= (len changes.items) 3)) answers)
  (+ whole.rows changes.items))


(deftest test-memory-answers-carry-the-written-spelling
  (val answers (answers-on "memory"))
  (assert-same-json answers)
  (for [holder (carried-rows answers)]
    (assert (= (json.loads (stored-text holder)) (thaw-json holder.value)) holder)))


(deftest test-postgres-answers-carry-the-stored-payload-undecoded
  {:skip-if (not PG-DSN) :skip-reason (pg-skip-reason)}
  (val answers (answers-on "pg"))
  ;; 欄を絞らない一覧と変更の読みの行は、本文を綴った後も値を解いていない(欄 value が置かれていない)。
  (for [answer answers] (run (answer-text answer)))
  (for [holder (carried-rows answers)]
    (assert (is-not (stored-text holder) None) holder)
    (assert (not-in "value" (vars holder)) holder))
  (assert-same-json answers))


(deftest test-a-stored-row-decodes-once-when-read
  (val text "{\"id\":\"p1\",\"label\":\"日本語\",\"n\":[1,{\"b\":2}]}")
  (val row (stored-row #("p1") text 3))
  (assert (not-in "value" (vars row)))
  (val made (Row #("p1") (FrozenMap {"id" "p1" "label" "日本語" "n" [1 {"b" 2}]}) 3))
  (assert (= row made))
  (assert (is row.value row.value))
  (assert (= (repr row) (repr made)))
  (assert (= (stored-text row) text))
  (val change (stored-change "parts" #("p1") 3 text 9 100))
  (assert (= change (RowChanged "parts" #("p1") 3 made.value 9 100)))
  (with [(pytest.raises AttributeError)] (. (Row #("p1") (FrozenMap {"id" "p1"}) 1) missing-name)))


(deftest test-a-read-event-carries-its-stored-payload-undecoded
  ;; append_rows の行(seq at payload)3 つ: 行と一緒に積んだ出来事(position 付き)・本文が object でない出来事・本文の中に
  ;; "position" の字を持つ出来事(位置は無い)。payload は置き場の書きと同じ正規の綴り(鍵の昇順)。
  (val body {"label" ODD-LABEL "grant" NESTED-GRANT})
  (val spell (fn [payload] (json.dumps payload :ensure-ascii False :separators #("," ":") :sort-keys True)))
  (val records [#(7 1700000000000 (spell {"idempotencyKey" "k1" "writer" "maker" "body" body "position" {"epoch" 2 "sequence" 9}}))
                #(8 1700000000001 (spell {"idempotencyKey" "k2" "writer" "maker" "body" [1 "二" None 2.5]}))
                #(9 1700000000002 (spell {"idempotencyKey" "k3" "writer" "maker" "body" {"position" "left"}}))])
  (val events (run (events-of "moves" records)))
  (val answer (Events events 9))
  ;; 答えの本文を綴っても、出来事は payload を解いていない(欄 body が置かれていない — 位置の無い出来事は冪等キーも書き手も置かれていない)。
  (val spelled (run (answer-text answer)))
  (for [event events]
    (assert (= (stored-text event) (get (get records (- event.sequence 7)) 2)) event)
    (assert (not-in "body" (vars event)) event))
  (assert (not-in "idempotency_key" (vars (get events 1))) (get events 1))
  (assert (not-in "writer" (vars (get events 1))) (get events 1))
  ;; 本文は前の綴り(encode-answer の object を json.dumps で綴った物)と JSON として同じ。
  (val plain (json.dumps (run (encode-answer answer)) :ensure-ascii False :separators #("," ":")))
  (assert (= (json.loads spelled) (json.loads plain)) #(spelled plain))
  ;; 欄は読まれた時に 1 度だけ解き、値から作った出来事と等しい(等しさ・repr は前のまま — repr は鍵の順も写すので、比べる相手の本文は
  ;; payload と同じ鍵の昇順で作る)。
  (assert (= (get events 0) (Event "moves" 7 "k1" body "maker" 1700000000000 (WatchCursor 2 9))))
  (val made #((Event "moves" 7 "k1" (json.loads (spell body)) "maker" 1700000000000 (WatchCursor 2 9))
              (Event "moves" 8 "k2" [1 "二" None 2.5] "maker" 1700000000001)
              (Event "moves" 9 "k3" {"position" "left"} "maker" 1700000000002)))
  (assert (= events made))
  (assert (= (repr events) (repr made)))
  (assert (is (. (get events 0) body) (. (get events 0) body)))
  (assert (isinstance (. (get events 0) body) FrozenMap))
  (assert (= (run (answer-text answer)) spelled))
  (with [(pytest.raises AttributeError)] (. (get events 0) missing-name))
  (with [(pytest.raises AttributeError)] (. (get made 0) missing-name)))
