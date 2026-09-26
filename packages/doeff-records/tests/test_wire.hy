;; wire の綴り: 公開 effect と答えの全部の形が JSON を往復して元と等しい・契約の形でない JSON は WireMalformed で断る。
(require doeff-hy.macros [deftest])
(import json)
(import doeff [run])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [ExpectAbsent ExpectVersion ExpectAny Approval WatchCursor ListCursor Row Missing Page Written
                              RowChanged RowRemoved Changes Appended Event Events Conflict Refused NotIndexed Reset])
(import doeff_records.effects [ReadRow ListRows PutRow WatchChanges AppendEvent ReadEvents])
(import doeff_records.wire [WireRequest WireMalformed ANSWER-KINDS encode-request decode-request encode-answer decode-answer])

(setv ROW (Row #("g1" "t1") {"group" "g1" "id" "t1" "nested" {"a" [1 2.5 True None "x"]}} 3))

(setv REQUESTS
  [(ReadRow "parts" #("p1"))
   (ListRows "parts")
   (ListRows "parts" :where {"color" "red"} :fields #("color") :cursor (ListCursor 2 "[\"p1\"]") :limit 5)
   (PutRow "parts" #("p1") {"label" "a" "color" None} (ExpectAbsent))
   (PutRow "parts" #("p1") {"grant" "yes"} (ExpectVersion 4) :approval (Approval "t"))
   (PutRow "parts" #("p1") {} (ExpectAny))
   (WatchChanges #("parts" "tickets") (WatchCursor 1 7) :timeout 2.5 :limit 3)
   (AppendEvent "journal" "k1" {"n" [1 {"m" None}]})
   (AppendEvent "journal" "k2" "a string body")
   (ReadEvents "journal" :after 4 :limit 2)])

;; 答え → それを答えてよい操作。
(setv ANSWERS
  [#("read-row" ROW)
   #("read-row" (Missing))
   #("list-rows" (Page #(ROW) (ListCursor 1 "[\"g1\",\"t1\"]") 1 9))
   #("list-rows" (Page #() None 2 0))
   #("list-rows" (Reset 3))
   #("list-rows" (NotIndexed #("note")))
   #("put-row" (Written 2 {"id" "p1"}))
   #("put-row" (Conflict ROW))
   #("put-row" (Conflict (Missing)))
   #("put-row" (Refused "書き手でない"))
   #("watch-changes" (Changes #((RowChanged "parts" #("p1") 1 {"id" "p1"} 5) (RowRemoved "tickets" #("g1" "t1") 6))
                              (WatchCursor 1 6)))
   #("watch-changes" (Reset 2))
   #("append-event" (Appended 7))
   #("append-event" (Refused "別の本文"))
   #("read-events" (Events #((Event "journal" 1 "k1" {"n" 1} "maker" 1000)) 1))])


(defn through-json [value]
  "JSON の綴りを 1 度通す(HTTP の境界と同じ)。"
  (json.loads (json.dumps value)))


(deftest test-every-request-survives-the-wire
  (for [effect REQUESTS]
    (setv request (run (encode-request effect))
          decoded (run (decode-request (WireRequest request.operation (through-json request.body)))))
    (assert (= decoded.effect effect) (.format "{!r} → {!r}" effect decoded.effect))))


(deftest test-every-answer-survives-the-wire
  (for [#(operation answer) ANSWERS]
    (setv decoded (run (decode-answer operation (through-json (run (encode-answer answer))))))
    (assert (= decoded answer) (.format "{!r} → {!r}" answer decoded)))
  ;; 表の全部の kind を 1 回以上通した。
  (assert (= (set (gfor #(operation answer) ANSWERS (get (run (encode-answer answer)) "kind")))
             (set (gfor kinds (.values ANSWER-KINDS) kind kinds kind)))))


(deftest test-malformed-json-is-refused-not-defaulted
  (for [#(operation body) [#("read-row" {"table" "parts"})
                           #("read-row" {"table" "parts" "key" ["p1"] "extra" 1})
                           #("read-row" {"table" "parts" "key" "p1"})
                           #("read-row" {"table" "Parts!" "key" ["p1"]})
                           #("list-rows" {"table" "parts" "limit" True})
                           #("list-rows" {"table" "parts" "limit" 0})
                           #("put-row" {"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "version"}})
                           #("put-row" {"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "version" "version" 0}})
                           #("put-row" {"table" "parts" "key" ["p1"] "value" [] "expect" {"kind" "any"}})
                           #("watch-changes" {"tables" [] "cursor" {"epoch" 1 "sequence" 0}})
                           #("watch-changes" {"tables" ["parts"] "cursor" {"epoch" 1}})
                           #("append-event" {"stream" "journal" "idempotencyKey" "" "body" 1})
                           #("read-events" {"stream" "journal" "after" -1})
                           #("drop-table" {})
                           #("read-row" ["not" "an" "object"])]]
    (try
      (run (decode-request (WireRequest operation body)))
      (assert False (.format "形の違う要求を読んだ: {} {!r}" operation body))
      (except [WireMalformed] None)))
  (for [#(operation body) [#("read-row" {"kind" "written" "version" 1 "value" {}})
                           #("put-row" {"kind" "written" "version" 1})
                           #("put-row" {"kind" "conflict" "current" {"kind" "refused" "reason" "x"}})
                           #("list-rows" {"kind" "page" "rows" {} "nextCursor" None "epoch" 1 "sequence" 0})]]
    (try
      (run (decode-answer operation body))
      (assert False (.format "形の違う答えを読んだ: {} {!r}" operation body))
      (except [WireMalformed] None))))
