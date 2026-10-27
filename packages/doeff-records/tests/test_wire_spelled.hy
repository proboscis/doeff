;;; 記録の service の答えの本文(wire.answer-text)が、行と変更の値を置き場の保存の綴りのまま継ぎ(値を解かず・凍らせず・戻さず・綴り
;;; 直さない)、前の綴り(encode-answer の object を json.dumps で綴った物)と JSON として同じ本文を返す事(values.hy の頭の註の保存の綴り)。
;;; 前は欄を絞らない一覧と変更の読みのたびに、変わっていない行も 1 行ごとに json.loads・凍らせ・thaw-json・json.dumps を回していた。
;;; - memory の置き場と PostgreSQL の置き場の両方で、一覧(欄を絞らない・絞る・上から読む)と変更の読みの答えの新旧の本文が JSON として同じ。
;;; - 欄を絞らない一覧と変更の読みの行は保存の綴りを持ち、PostgreSQL の置き場の行は答えを綴った後も値を解いていない(欄 value が無い)。
;;; - 綴りだけを持つ行は、値を読まれた時に 1 度だけ解き、値から作った行と等しい(等しさ・repr・match は前のまま)。
(require doeff-hy.macros [deftest defk <- val])
(import json)
(import pytest)
(import doeff [run])
(import doeff_hy.frozen [FrozenMap thaw-json])
(import doeff_records.values [ExpectAbsent ExpectVersion Row RowChanged Page Changes Written WatchCursor stored-row stored-change
                              stored-text])
(import doeff_records.effects [ListRows WatchChanges PutRow])
(import doeff_records.laws [MAKER LawHarness as-writer])
(import doeff_records.wire [encode-answer answer-text])
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
