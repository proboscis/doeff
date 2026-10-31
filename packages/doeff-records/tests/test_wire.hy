;; wire の綴り: 公開 effect と答えの全部の形が JSON を往復して元と等しい・契約の形でない JSON は WireMalformed で断る(答えの知らない鍵は
;; 断らずに読み捨てる — 版の違いの検は test_wire_versions.hy)。
(require doeff-hy.macros [deftest val])
(import json)
(import doeff [run])
(import doeff_hy._frozen [FrozenMap])
(import doeff_records [ExpectAbsent ExpectVersion ExpectAny WatchCursor ListCursor Row Missing Page Written RowChanged RowRemoved Changes Appended Event Events Conflict Refused NotIndexed Reset WrittenRows RowsConflict RowsRefused StreamEnd StreamEmpty StreamTail StreamTailEmpty EventsMoved EventsQuiet EventAbsent EventRetired AppendedEvents BatchRead Count WrittenAndAppended AppendRefused])
(import doeff_records [ReadRow ListRows PutRow PutRows RowWrite WatchChanges WatchEvents AppendEvent ReadEvents ReadStreamEnd ReadEventByKey AppendEvents EventAppend ReadBatch CountRows PutRowsAppendEvents]
        doeff_records._effects [WHERE-ANY-LIMIT LIST-LIMIT-MAX READ-BATCH-MAX])
(import doeff_records [encode-request decode-request decode-answer]
        doeff_records._wire [WireRequest WireMalformed ANSWER-KINDS WATCH-MAX-SECONDS encode-answer answer-for-request])

(setv ROW (Row #("g1" "t1") {"group" "g1" "id" "t1" "nested" {"a" [1 2.5 True None "x"]}} 3))

(setv REQUESTS
  [(ReadRow "parts" #("p1"))
   (ListRows "parts")
   (ListRows "parts" :where {"color" "red"} :fields #("color") :cursor (ListCursor 2 "[\"p1\"]") :limit 5)
   ;; 上から読む読み(descending-by — 鍵 descendingBy は付けた時だけ送る)。
   (ListRows "parts" :where {"color" "red"} :fields #("color") :limit 3 :descending-by "note")
   ;; 上から読む読みの続き(位置の鍵 afterValue は上から読む読みの位置の時だけ送る — int8 を越える整数も)。
   (ListRows "parts" :where {"color" "red"} :limit 3 :descending-by "note" :cursor (ListCursor 2 "[\"p1\"]" -7))
   (ListRows "parts" :limit 3 :descending-by "note" :cursor (ListCursor 2 "[\"p1\"]" (** 10 30)))
   ;; 値の組の絞り(where-any — 鍵 whereAny は付けた時だけ送る)。値は JSON の値なら何でも(数・真偽・null・object)。
   (ListRows "parts" :where {"label" "a"} :where-any {"color" ["red" 1 2.5 True None {"x" [1]}] "id" ["p1"]}
             :fields #("color") :cursor (ListCursor 2 "[\"p1\"]") :limit 5)
   (ListRows "parts" :where-any {"color" ["red" "blue"]} :limit 3 :descending-by "note")
   ;; 昇順の読み(ascending-by — 鍵 ascendingBy は付けた時だけ送る)と、その続き。
   (ListRows "parts" :where {"color" "red"} :limit 3 :ascending-by "note")
   (ListRows "parts" :limit 3 :ascending-by "note" :cursor (ListCursor 2 "[\"p1\"]" (** 10 30)))
   ;; 件数(鍵 where・whereAny・atLeast は使う時だけ送る)。
   (CountRows "parts")
   (CountRows "parts" :where {"color" "red"} :where-any {"label" ["a" "b"]} :at-least #("note" -5))
   (PutRow "parts" #("p1") {"label" "a" "color" None} (ExpectAbsent))
   (PutRow "parts" #("p1") {"grant" "yes"} (ExpectVersion 4))
   (PutRow "parts" #("p1") {} (ExpectAny))
   (WatchChanges #("parts" "tickets") (WatchCursor 1 7) :timeout 2.5 :limit 3)
   (WatchChanges #("parts") (WatchCursor 1 7) :streams #("journal" "pulses"))
   (AppendEvent "journal" "k1" {"n" [1 {"m" None}]})
   (AppendEvent "journal" "k2" "a string body")
   (ReadEvents "journal" :after 4 :limit 2)
   ;; 冪等キーの頭で引く読み(key-head — 鍵 keyHead は付けた時だけ送る)。
   (ReadEvents "trails" :after 4 :limit 2 :key-head "a")
   (ReadStreamEnd "journal")
   (WatchEvents "journal" :after 4 :timeout 2.5)
   (ReadEventByKey "journal" "k1")
   (PutRows #((RowWrite "parts" #("p1") {"label" "a" "color" None} (ExpectVersion 2))
              (RowWrite "tickets" #("g1" "t1") {"owner" "o1"} (ExpectAbsent))
              (RowWrite "parts" #("p2") {} (ExpectAny))))
   ;; 追記の束— 列をまたぐ束・文字列の本文。
   (AppendEvents #((EventAppend "pairs" "cause:r1" {"n" [1 {"m" None}]})
                   (EventAppend "journal" "k1" "a string body")))
   ;; 読みの束— 表をまたぐ鍵の読みと、値の組・位置・上から読む読みの一覧。
   (ReadBatch #((ReadRow "parts" #("p1"))
                (ListRows "tickets" :where-any {"owner" ["o1" "o2"]} :fields #("owner") :limit 50)
                (ReadRow "tickets" #("g1" "t1"))
                (ListRows "parts" :where {"color" "red"} :cursor (ListCursor 2 "[\"p1\"]") :limit 5)
                (ListRows "parts" :limit 3 :descending-by "note")))
   ;; 行と追記の束— 束の項は put-rows・append-events と同じ綴り(冪等キーは見出しで運ぶので本文に無い)。
   (PutRowsAppendEvents #((RowWrite "parts" #("p1") {"label" "a" "color" None} (ExpectVersion 2))
                          (RowWrite "tickets" #("g1" "t1") {"owner" "o1"} (ExpectAbsent)))
                        #((EventAppend "pairs" "cause:r1" {"n" [1 {"m" None}]})
                          (EventAppend "journal" "k1" "a string body")))])

;; 答え → それを答えてよい操作。
(setv ANSWERS
  [#("read-row" ROW)
   #("read-row" (Missing))
   #("list-rows" (Page #(ROW) (ListCursor 1 "[\"g1\",\"t1\"]") 1 9))
   #("list-rows" (Page #(ROW) (ListCursor 1 "[\"g1\",\"t1\"]" 1760000000000) 1 9))
   #("list-rows" (Page #() None 2 0))
   #("list-rows" (Reset 3 0))
   #("list-rows" (NotIndexed #("note")))
   #("put-row" (Written 2 {"id" "p1"}))
   ;; 書きの変更の位置を持つ答え(鍵 position — 省ける鍵)。
   #("put-row" (Written 2 {"id" "p1"} (WatchCursor 3 41)))
   ;; 冪等キーの送り直しの答え(鍵 replayed — 真の時だけ載る省ける鍵)。
   #("put-row" (Written 2 {"id" "p1"} (WatchCursor 3 41) :replayed True))
   #("put-row" (Conflict ROW))
   #("put-row" (Conflict (Missing)))
   #("put-row" (Refused "書き手でない"))
   #("watch-changes" (Changes #((RowChanged "parts" #("p1") 1 {"id" "p1"} 5 1700000000123) (RowRemoved "tickets" #("g1" "t1") 6))
                              (WatchCursor 1 6) #()))
   #("watch-changes" (Changes #() (WatchCursor 1 6) #((StreamTail :stream "journal" :sequence 4 :at 1700000000123)
                                                      (StreamTailEmpty :stream "pulses"))))
   #("watch-changes" (Reset 2 41))
   #("append-event" (Appended 7 False))
   #("append-event" (Appended 7 True))
   #("append-event" (Refused "別の本文"))
   #("read-events" (Events #((Event "journal" 1 "k1" {"n" 1} "maker" 1000)) 1))
   #("read-events" (NotIndexed #("idempotencyKey")))
   #("count-rows" (Count 0))
   #("count-rows" (Count 12))
   #("count-rows" (NotIndexed #("note")))
   #("read-stream-end" (StreamEnd 7))
   #("read-stream-end" (StreamEmpty))
   #("watch-events" (EventsMoved))
   #("watch-events" (EventsQuiet))
   #("read-event-by-key" (Event "journal" 4 "k1" {"n" [1 {"m" None}]} "maker" 1000))
   #("read-event-by-key" (EventAbsent))
   #("read-event-by-key" (EventRetired 3))
   ;; 行と一緒に積んだ出来事(鍵 position — 位置を持つ時だけ載る省ける鍵)。
   #("read-events" (Events #((Event "journal" 2 "k2" {"n" 2} "maker" 1000 (WatchCursor 3 43))) 2))
   #("read-event-by-key" (Event "journal" 2 "k2" {"n" 2} "maker" 1000 (WatchCursor 3 43)))
   #("put-rows" (WrittenRows #((Written 2 {"id" "p1"}) (Written 1 {"group" "g1" "id" "t1"}))))
   #("put-rows" (WrittenRows #((Written 2 {"id" "p1"} (WatchCursor 3 42)) (Written 1 {"group" "g1" "id" "t1"} (WatchCursor 3 43)))))
   #("put-rows" (WrittenRows #((Written 2 {"id" "p1"} (WatchCursor 3 42) :replayed True)
                               (Written 1 {"group" "g1" "id" "t1"} (WatchCursor 3 43) :replayed True))
                             :replayed True))
   #("put-rows" (RowsConflict 1 "tickets" #("g1" "t1") ROW))
   #("put-rows" (RowsConflict 0 "parts" #("p1") (Missing)))
   #("put-rows" (RowsRefused 2 "parts" #("p2") "書き手でない"))
   #("append-events" (AppendedEvents #((Appended 7 False) (Refused "別の本文") (Appended 3 True))))
   #("put-rows-append-events" (WrittenAndAppended (WrittenRows #((Written 2 {"id" "p1"} (WatchCursor 3 42))
                                                                 (Written 1 {"group" "g1" "id" "t1"} (WatchCursor 3 43))))
                                                  #((Appended 7 False) (Appended 3 True))))
   #("put-rows-append-events" (WrittenAndAppended (WrittenRows #((Written 2 {"id" "p1"} (WatchCursor 3 42) :replayed True)) :replayed True)
                                                  #((Appended 7 True)) :replayed True))
   #("put-rows-append-events" (RowsConflict 0 "parts" #("p1") (Missing)))
   #("put-rows-append-events" (RowsRefused 0 "parts" #("p1") "行の書きの冪等キー 'k' は別の本文の書きで既に使われた"))
   #("put-rows-append-events" (AppendRefused 1 "journal" "k1" "別の本文"))
   #("read-batch" (BatchRead #(ROW (Missing) (Page #(ROW) (ListCursor 1 "[\"g1\",\"t1\"]") 1 9) (Reset 3 0) (NotIndexed #("note")))))])


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


(deftest test-a-wait-longer-than-the-limit-is-read-as-the-limit
  ;; long-poll の取り決め(#3074): 待ちの要求の秒は 1 回の要求の上限 WATCH-MAX-SECONDS で切って読む(service はその秒までしか待たない)。
  ;; 上限より短い待ちはそのまま。
  (for [#(operation body) [#("watch-changes" {"tables" ["parts"] "cursor" {"epoch" 1 "sequence" 0} "timeout" 600.0})
                           #("watch-events" {"stream" "journal" "after" 0 "timeout" 600.0})]]
    (setv decoded (run (decode-request (WireRequest operation body))))
    (assert (= decoded.effect.timeout WATCH-MAX-SECONDS) decoded))
  (setv short (run (decode-request (WireRequest "watch-events" {"stream" "journal" "after" 0 "timeout" 1.5}))))
  (assert (= short.effect.timeout 1.5) short))


(deftest test-malformed-json-is-refused-not-defaulted
  (for [#(operation body) [#("read-row" {"table" "parts"})
                           #("read-row" {"table" "parts" "key" ["p1"] "extra" 1})
                           #("read-row" {"table" "parts" "key" "p1"})
                           #("read-row" {"table" "Parts!" "key" ["p1"]})
                           #("list-rows" {"table" "parts" "limit" True})
                           #("list-rows" {"table" "parts" "limit" 0})
                           ;; 上から読む読み: 欄の名は文字列・上から読む読みの位置は afterValue(整数)を持つ位置だけ・鍵の順の読みの位置は
                           ;; afterValue を持たない。
                           #("list-rows" {"table" "parts" "descendingBy" None})
                           #("list-rows" {"table" "parts" "descendingBy" "Note!"})
                           #("list-rows" {"table" "parts" "descendingBy" "note" "cursor" {"epoch" 1 "afterKey" "[\"p1\"]"}})
                           #("list-rows" {"table" "parts" "descendingBy" "note" "cursor" {"epoch" 1 "afterKey" "[\"p1\"]" "afterValue" 2.5}})
                           #("list-rows" {"table" "parts" "descendingBy" "note" "cursor" {"epoch" 1 "afterKey" "[\"p1\"]" "afterValue" True}})
                           #("list-rows" {"table" "parts" "descendingBy" "note" "cursor" {"epoch" 1 "afterKey" "[\"p1\"]" "afterValue" None}})
                           #("list-rows" {"table" "parts" "cursor" {"epoch" 1 "afterKey" "[\"p1\"]" "afterValue" 3}})
                           ;; 値の組の絞り: 絞らない時は鍵ごと無い(空の object は綴りの外)・組は空でない配列・欄の名の形・where と同じ欄は置けない・
                           ;; 値の数の上限。
                           #("list-rows" {"table" "parts" "whereAny" {}})
                           #("list-rows" {"table" "parts" "whereAny" None})
                           #("list-rows" {"table" "parts" "whereAny" ["color"]})
                           #("list-rows" {"table" "parts" "whereAny" {"color" []}})
                           #("list-rows" {"table" "parts" "whereAny" {"color" "red"}})
                           #("list-rows" {"table" "parts" "whereAny" {"Color!" ["red"]}})
                           #("list-rows" {"table" "parts" "where" {"color" "red"} "whereAny" {"color" ["red"]}})
                           #("list-rows" {"table" "parts" "whereAny" {"color" (list (range (+ WHERE-ANY-LIMIT 1)))}})
                           #("put-row" {"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "version"}})
                           #("put-row" {"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "version" "version" 0}})
                           #("put-row" {"table" "parts" "key" ["p1"] "value" [] "expect" {"kind" "any"}})
                           ;; 承認の印は廃止(契約で deprecated)— 値のある印は効かない承認を黙って捨てず断る。
                           #("put-row" {"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "any"} "approval" {"token" "t"}})
                           #("watch-changes" {"tables" [] "cursor" {"epoch" 1 "sequence" 0}})
                           #("watch-changes" {"tables" ["parts"] "cursor" {"epoch" 1}})
                           ;; 末尾を名指す列(#3718): 名指さない時は鍵ごと無い(空の配列は綴りの外)・文字列の配列・列の名の形・同じ列は 1 度だけ。
                           #("watch-changes" {"tables" ["parts"] "cursor" {"epoch" 1 "sequence" 0} "streams" []})
                           #("watch-changes" {"tables" ["parts"] "cursor" {"epoch" 1 "sequence" 0} "streams" "journal"})
                           #("watch-changes" {"tables" ["parts"] "cursor" {"epoch" 1 "sequence" 0} "streams" ["Journal!"]})
                           #("watch-changes" {"tables" ["parts"] "cursor" {"epoch" 1 "sequence" 0} "streams" ["journal" "journal"]})
                           #("append-event" {"stream" "journal" "idempotencyKey" "" "body" 1})
                           #("read-events" {"stream" "journal" "after" -1})
                           ;; 冪等キーの頭で引く読み: 頭は空でない文字列(引かない時は鍵ごと無い)。
                           #("read-events" {"stream" "trails" "keyHead" None})
                           #("read-events" {"stream" "trails" "keyHead" ""})
                           #("read-events" {"stream" "trails" "keyHead" 3})
                           #("read-stream-end" {})
                           #("read-stream-end" {"stream" "journal" "after" 0})
                           #("read-stream-end" {"stream" "Journal!"})
                           ;; 冪等キーの読み(#3750): 2 つの欄が必須・空の鍵と列の名の形の外は断る。
                           #("read-event-by-key" {"stream" "journal"})
                           #("read-event-by-key" {"stream" "journal" "idempotencyKey" ""})
                           #("read-event-by-key" {"stream" "Journal!" "idempotencyKey" "k1"})
                           #("read-event-by-key" {"stream" "journal" "idempotencyKey" "k1" "after" 0})
                           ;; 列の待ち(#3074)は 3 つの欄が必須 — 足りない欄を既定に倒さない・負の after と待ちの秒は断る。
                           #("watch-events" {"stream" "journal" "after" 0})
                           #("watch-events" {"stream" "journal" "timeout" 1.0})
                           #("watch-events" {"stream" "journal" "after" -1 "timeout" 1.0})
                           #("watch-events" {"stream" "journal" "after" 0 "timeout" -1.0})
                           #("put-rows" {"writes" []})
                           #("put-rows" {"writes" {"table" "parts"}})
                           #("put-rows" {"writes" [{"table" "parts" "key" ["p1"] "value" {}}]})
                           #("put-rows" {"writes" [{"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "any"} "approval" None}]})
                           ;; 同じ表の同じ鍵が束に 2 度 — 作る時に断る(黙って後の書きで上書きしない)。
                           #("put-rows" {"writes" [{"table" "parts" "key" ["p1"] "value" {"label" "a"} "expect" {"kind" "any"}}
                                                   {"table" "parts" "key" ["p1"] "value" {"label" "b"} "expect" {"kind" "any"}}]})
                           #("put-rows" {"writes" [] "extra" 1})
                           ;; 追記の束: 空の束・配列でない束・欄の足りない追記・同じ列の同じ冪等キーが 2 度・知らない鍵は断る。
                           #("append-events" {"appends" []})
                           #("append-events" {"appends" {"stream" "journal"}})
                           #("append-events" {"appends" [{"stream" "journal" "idempotencyKey" "k1"}]})
                           #("append-events" {"appends" [{"stream" "journal" "idempotencyKey" "k1" "body" 1}
                                                         {"stream" "journal" "idempotencyKey" "k1" "body" 2}]})
                           #("append-events" {"appends" [] "extra" 1})
                           ;; 読みの束: 空の束・配列でない束・欄の足りない読み・束に載せない操作(書き・束の中の束)・項の本文の形の誤り・
                           ;; 読みの数と一覧の limit の和の上限・知らない鍵は断る。
                           #("read-batch" {"reads" []})
                           #("read-batch" {"reads" {"operation" "read-row"}})
                           #("read-batch" {"reads" [{"operation" "read-row"}]})
                           #("read-batch" {"reads" [{"operation" "put-row" "body" {"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "any"}}}]})
                           #("read-batch" {"reads" [{"operation" "read-batch" "body" {"reads" []}}]})
                           #("read-batch" {"reads" [{"operation" "read-row" "body" {"table" "parts"}}]})
                           #("read-batch" {"reads" [{"operation" "read-row" "body" {"table" "parts" "key" ["p1"]} "extra" 1}]})
                           #("read-batch" {"reads" (lfor n (range (+ READ-BATCH-MAX 1)) {"operation" "read-row" "body" {"table" "parts" "key" [(str n)]}})})
                           #("read-batch" {"reads" (lfor n (range 2) {"operation" "list-rows" "body" {"table" "parts" "limit" LIST-LIMIT-MAX}})})
                           #("read-batch" {"reads" [] "extra" 1})
                           ;; 行と追記の束: 2 つの鍵が必須・空の束・同じ行や同じ追記が 2 度・冪等キーを本文に置く・知らない鍵は断る。
                           #("put-rows-append-events" {"writes" [{"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "any"}}]})
                           #("put-rows-append-events" {"appends" [{"stream" "journal" "idempotencyKey" "k1" "body" 1}]})
                           #("put-rows-append-events" {"writes" [] "appends" [{"stream" "journal" "idempotencyKey" "k1" "body" 1}]})
                           #("put-rows-append-events" {"writes" [{"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "any"}}] "appends" []})
                           #("put-rows-append-events" {"writes" [{"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "any"}}
                                                                 {"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "any"}}]
                                                       "appends" [{"stream" "journal" "idempotencyKey" "k1" "body" 1}]})
                           #("put-rows-append-events" {"writes" [{"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "any"}}]
                                                       "appends" [{"stream" "journal" "idempotencyKey" "k1" "body" 1}
                                                                  {"stream" "journal" "idempotencyKey" "k1" "body" 2}]})
                           #("put-rows-append-events" {"writes" [{"table" "parts" "key" ["p1"] "value" {} "expect" {"kind" "any"}}]
                                                       "appends" [{"stream" "journal" "idempotencyKey" "k1" "body" 1}]
                                                       "idempotencyKey" "w1"})
                           #("drop-table" {})
                           #("read-row" ["not" "an" "object"])]]
    (try
      (run (decode-request (WireRequest operation body)))
      (assert False (.format "形の違う要求を読んだ: {} {!r}" operation body))
      (except [WireMalformed] None)))
  (for [#(operation body) [#("read-row" {"kind" "written" "version" 1 "value" {}})
                           #("put-row" {"kind" "written" "version" 1})
                           #("put-row" {"kind" "conflict" "current" {"kind" "refused" "reason" "x"}})
                           #("list-rows" {"kind" "page" "rows" {} "nextCursor" None "epoch" 1 "sequence" 0})
                           #("put-rows" {"kind" "written" "version" 1 "value" {}})
                           #("put-rows" {"kind" "writtenRows" "items" [{"kind" "missing"}]})
                           #("put-rows" {"kind" "rowsConflict" "index" 0 "table" "parts" "key" ["p1"]
                                         "current" {"kind" "refused" "reason" "x"}})
                           #("put-rows" {"kind" "rowsRefused" "index" "0" "table" "parts" "key" ["p1"] "reason" "x"})
                           ;; 追記の束の答えの items は appended | refused の列だけ。
                           #("append-events" {"kind" "appended" "sequence" 1 "replayed" False})
                           #("append-events" {"kind" "appendedEvents" "items" [{"kind" "missing"}]})
                           #("append-events" {"kind" "appendedEvents" "items" {"kind" "appended"}})
                           ;; 行と追記の束の答え: rows は writtenRows・appends は appended の列だけ(refused は載らない — 断りは appendRefused)。
                           #("put-rows-append-events" {"kind" "writtenRows" "items" []})
                           #("put-rows-append-events" {"kind" "writtenAndAppended" "rows" {"kind" "missing"}
                                                       "appends" [{"kind" "appended" "sequence" 1 "replayed" False}]})
                           #("put-rows-append-events" {"kind" "writtenAndAppended" "rows" {"kind" "writtenRows" "items" []}
                                                       "appends" [{"kind" "refused" "reason" "x"}]})
                           #("put-rows-append-events" {"kind" "appendRefused" "index" 0 "stream" "journal" "reason" "x"})
                           ;; 確定の刻 at の無い変更は、刻を黙って既定に倒さず断る。
                           #("watch-changes" {"kind" "changes" "cursor" {"epoch" 1 "sequence" 5}
                                              "items" [{"kind" "rowChanged" "table" "parts" "key" ["p1"] "version" 1
                                                        "value" {} "sequence" 5}]})
                           ;; 読める最も古い位置 floor の無い Reset は、floor を黙って 0 に倒さず断る。
                           #("watch-changes" {"kind" "reset" "epoch" 2})
                           #("list-rows" {"kind" "reset" "epoch" 2 "floor" "0"})
                           ;; 名指した列の末尾(#3718): 空の tails は綴りの外(名指さない答えは鍵ごと無い)・刻の無い末尾・知らない kind・
                           ;; 0 の番号は断る(空の列は streamTailEmpty で運ぶ)。
                           #("watch-changes" {"kind" "changes" "items" [] "cursor" {"epoch" 1 "sequence" 5} "tails" []})
                           #("watch-changes" {"kind" "changes" "items" [] "cursor" {"epoch" 1 "sequence" 5}
                                              "tails" [{"kind" "streamTail" "stream" "journal" "sequence" 4}]})
                           #("watch-changes" {"kind" "changes" "items" [] "cursor" {"epoch" 1 "sequence" 5}
                                              "tails" [{"kind" "streamEnd" "sequence" 4 "at" 1}]})
                           #("watch-changes" {"kind" "changes" "items" [] "cursor" {"epoch" 1 "sequence" 5}
                                              "tails" [{"kind" "streamTail" "stream" "journal" "sequence" 0 "at" 1}]})
                           #("watch-changes" {"kind" "changes" "items" [] "cursor" {"epoch" 1 "sequence" 5}
                                              "tails" [{"kind" "streamTailEmpty"}]})
                           ;; 列の末尾の番号の無い streamEnd・0 や真偽値の番号は断る(空の列は streamEmpty で運ぶ — 0 に倒さない)。
                           #("read-stream-end" {"kind" "streamEnd"})
                           #("read-stream-end" {"kind" "streamEnd" "sequence" 0})
                           #("read-stream-end" {"kind" "streamEnd" "sequence" True})
                           #("read-stream-end" {"kind" "events" "items" [] "lastSequence" 0})
                           ;; 列の待ちの答えは eventsMoved | eventsQuiet だけ。
                           #("watch-events" {"kind" "events" "items" [] "lastSequence" 0})
                           ;; 冪等キーの読みの答え(#3750): 出来事は欄 event の入れ子だけ・消えた番号は 1 以上・他の操作の答えの kind は断る。
                           #("read-event-by-key" {"kind" "event" "stream" "journal" "sequence" 1 "idempotencyKey" "k1" "body" 1
                                                  "writer" "maker" "at" 1})
                           #("read-event-by-key" {"kind" "eventRetired"})
                           #("read-event-by-key" {"kind" "eventRetired" "sequence" 0})
                           #("read-event-by-key" {"kind" "missing"})]]
    (try
      (run (decode-answer operation body))
      (assert False (.format "形の違う答えを読んだ: {} {!r}" operation body))
      (except [WireMalformed] None))))


(deftest test-a-list-without-value-sets-keeps-the-body-of-the-version-before
  ;; 値の組の絞りを使わない list-rows の本文は、鍵 whereAny を足す前と同じ(古い記録の service は知らない鍵を 400 で断る — 組を使わない
  ;; client は service の版を問わず通る)。組を使う要求だけが鍵 whereAny を持ち、値の組は JSON の配列で運ぶ。
  (setv plain (run (encode-request (ListRows "parts" :where {"color" "red"} :fields #("color") :cursor (ListCursor 2 "[\"p1\"]") :limit 5))))
  (assert (= #(plain.operation plain.body)
             #("list-rows" {"table" "parts" "where" {"color" "red"} "fields" ["color"] "cursor" {"epoch" 2 "afterKey" "[\"p1\"]"} "limit" 5}))
          plain)
  (setv top (run (encode-request (ListRows "parts" :limit 3 :descending-by "note"))))
  (assert (= top.body {"table" "parts" "where" {} "fields" None "cursor" None "limit" 3 "descendingBy" "note"}) top)
  (setv any-of (run (encode-request (ListRows "parts" :where-any {"color" ["red" 1]} :limit 3))))
  (assert (= any-of.body {"table" "parts" "where" {} "fields" None "cursor" None "limit" 3 "whereAny" {"color" ["red" 1]}}) any-of))


(deftest test-a-put-row-from-an-older-client-with-a-null-approval-is-still-read
  ;; 前の版の client は put-row の本文に "approval": null を添える — 記録の service と client の版が揃うまで読み飛ばす
  ;; (契約 record-service.json で deprecated・次の版で鍵ごと消す)。今の client は鍵を送らない。
  (setv body {"table" "parts" "key" ["p1"] "value" {"label" "a"} "expect" {"kind" "any"}})
  (assert (= (. (run (decode-request (WireRequest "put-row" (| body {"approval" None})))) effect)
             (PutRow "parts" #("p1") {"label" "a"} (ExpectAny))))
  (assert (not-in "approval" (. (run (encode-request (PutRow "parts" #("p1") {"label" "a"} (ExpectAny)))) body))))


(deftest test-a-list-above-the-limit-cap-is-refused-by-its-type
  ;; ListRows 1 回の limit は上限 LIST-LIMIT-MAX まで(ADR-DOE-RECORDS-002 R6 (f) — 1 要求の計算の量を行の数で縛る)。上限ちょうどは
  ;; wire を往復する。反例: 前の形は limit に上限が無く、大きな limit の 1 回の読みが置き場の行の数だけ計算の pool の thread を塞いだ。
  (setv at-cap (ListRows "parts" :limit LIST-LIMIT-MAX))
  (setv sent (run (encode-request at-cap)))
  (assert (= (. (run (decode-request (WireRequest sent.operation (through-json sent.body)))) effect) at-cap))
  ;; 型が作る時に断る — memory の置き場の模擬と検も、PostgreSQL の置き場も、client も同じ所で断る(続きの位置で頁に割る事を名指す)。
  (try
    (ListRows "parts" :limit (+ LIST-LIMIT-MAX 1))
    (assert False "上限を越えた一覧の読みを作った")
    (except [refused ValueError]
      (assert (in "next_cursor" (str refused)) refused)))
  ;; service の読みは上限を越えた本文を WireMalformed で断る(口は 400 malformed で答える)。上から読む読みも値の組の読みも同じ。
  (for [body [{"table" "parts" "limit" (+ LIST-LIMIT-MAX 1)}
              {"table" "parts" "limit" (* LIST-LIMIT-MAX 20) "descendingBy" "note"}
              {"table" "parts" "limit" (+ LIST-LIMIT-MAX 1) "whereAny" {"color" ["red"]}}]]
    (try
      (run (decode-request (WireRequest "list-rows" body)))
      (assert False (.format "上限を越えた一覧の読みを読んだ: {!r}" body))
      (except [refused WireMalformed]
        (assert (in (str LIST-LIMIT-MAX) (str refused)) refused)))))


(deftest test-a-batch-answer-must-match-the-reads-it-answers
  ;; 読みの束: client は束の答えを、撃った束の読みと照らす — 項の数が違う・i 番目が i 番目の読みの答えの型でない・
  ;; 束の答えでない答えは、黙って読まずに WireMalformed で名指す(service の綴りの誤り)。合う答えはそのまま返す。
  (setv batch (ReadBatch #((ReadRow "parts" #("p1")) (ListRows "parts"))))
  (setv fitting (BatchRead #(ROW (Page #() None 1 0))))
  (assert (= (run (answer-for-request batch fitting)) fitting))
  (for [wrong [(BatchRead #(ROW))
               (BatchRead #((Page #() None 1 0) ROW))
               (BatchRead #(ROW (Missing)))
               (Missing)]]
    (try
      (run (answer-for-request batch wrong))
      (assert False (.format "束に合わない答えを通した: {!r}" wrong))
      (except [refused WireMalformed]
        None)))
  ;; 束の作りの上限: 読みの数 READ-BATCH-MAX と一覧の limit の和 LIST-LIMIT-MAX はちょうどなら作れて、越えれば作る時に断る。
  (val most-reads (ReadBatch (tuple (gfor n (range READ-BATCH-MAX) (ReadRow "parts" #((str n)))))))
  (val most-rows (ReadBatch #((ListRows "parts" :limit (- LIST-LIMIT-MAX 1)) (ListRows "tickets" :limit 1))))
  (assert (and (= (len most-reads.reads) READ-BATCH-MAX) (= (len most-rows.reads) 2)))
  (for [make [(fn [] (ReadBatch (tuple (gfor n (range (+ READ-BATCH-MAX 1)) (ReadRow "parts" #((str n)))))))
              (fn [] (ReadBatch #((ListRows "parts" :limit LIST-LIMIT-MAX) (ListRows "tickets" :limit 1))))]]
    (try
      (make)
      (assert False "上限の外の束を作った")
      (except [refused ValueError]
        None)))
  (try
    (ReadBatch #((PutRow "parts" #("p1") {} (ExpectAny))))
    (assert False "書きを載せた束を作った")
    (except [refused TypeError]
      None))
  (try
    (ReadBatch #())
    (assert False "空の束を作った")
    (except [refused TypeError]
      None)))


(deftest test-an-event-names-its-rows-position-only-when-it-has-one
  ;; 出来事の鍵 position は行と一緒に積んだ出来事の時だけ載る(省ける鍵 — 位置の無い出来事は前の版と同じ綴り)。
  ;; 形の違う position は既定へ倒さず断る。
  (setv plain (run (encode-answer (Event "journal" 1 "k1" {"n" 1} "maker" 1000)))
        placed (run (encode-answer (Event "journal" 2 "k2" {"n" 2} "maker" 1000 (WatchCursor 3 43)))))
  (assert (= (get plain "event") {"stream" "journal" "sequence" 1 "idempotencyKey" "k1" "body" {"n" 1} "writer" "maker" "at" 1000}) plain)
  (assert (= (get placed "event" "position") {"epoch" 3 "sequence" 43}) placed)
  (for [position [{"epoch" 3} {"epoch" "3" "sequence" 43} None]]
    (try
      (run (decode-answer "read-event-by-key" {"kind" "event" "event" (| (get plain "event") {"position" position})}))
      (assert False (.format "形の違う出来事の位置を読んだ: {!r}" position))
      (except [WireMalformed] None))))
