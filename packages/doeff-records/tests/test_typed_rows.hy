;; 表ごとの行の型で読み書きする層(typed.hy)— 呼び手は欄 → 値の写像を見ずに、pydantic の model / dataclass で読み書きする。
;; 同じ筋書きを memory と PostgreSQL の handler の両方で回す(層は handler を足さないので、どの組の上でも同じ答え)。
(require doeff-hy.macros [deftest <- val])
(import dataclasses [dataclass])
(import doeff_hy.frozen [FrozenMap])
(import pydantic [BaseModel ConfigDict])
(import doeff_records.values [ExpectAbsent ExpectVersion ExpectAny Refused Missing WatchCursor Changes Row Written WrittenRows])
(import doeff_records.effects [WatchChanges ReadRow PutRow PutRows RowWrite])
(import doeff_records.typed [RowType TypedRow TypedPage TypedWritten TypedConflict TypedRowChanged read-typed list-typed put-typed
                             typed-change fields-of-value value-of-fields])
(import doeff_records.laws [MAKER PAINTER])
(import tests.interpreters [LawSetup])


(defclass Part [BaseModel]
  "表 parts の行の型(LAW-SCHEMA の欄ちょうど)。"
  (setv model-config (ConfigDict :frozen True :extra "forbid"))
  (#^ str id)
  (setv #^ (| str None) label None)
  (setv #^ (| str None) color None)
  (setv #^ (| str None) state None)
  (setv #^ (| str None) note None)
  (setv #^ (| str None) grant None))

(defclass [(dataclass :frozen True)] Ticket []
  "表 tickets の行の型(dataclass でもよい)。"
  (#^ str group)
  (#^ str id)
  (setv #^ (| str None) state None)
  (setv #^ (| str None) owner None))

(defclass [(dataclass :frozen True)] Strict []
  "既定値の無い None を許す欄を持つ行の型(書きで None の欄は消えるので、読みは無い欄を None と読む)。"
  (#^ str id)
  (#^ (| str None) note))

(defclass NewerPart [Part]
  "表 parts の行の型に、置き場の宣言(LAW-SCHEMA)に無い欄 method(既定値 None)を足した新しい版 — 書き手が置き場の宣言より先に
   行の型へ欄を足した形。"
  (setv #^ (| str None) method None))

(setv PARTS (RowType "parts" Part)
      TICKETS (RowType "tickets" Ticket))
(val NEWER-PARTS (RowType "parts" NewerPart))


(deftest test-the-write-image-lists-every-field-and-none-removes
  (assert (= (fields-of-value PARTS (Part :id "p1" :label "a"))
             {"id" "p1" "label" "a" "color" None "state" None "note" None "grant" None}))
  (assert (= (fields-of-value TICKETS (Ticket "g" "t" :owner "o")) {"group" "g" "id" "t" "state" None "owner" "o"})))


(deftest test-a-field-removed-by-a-none-write-reads-back-as-none
  ;; 値 None の書きは欄を消す — 既定値の無い None を許す欄も、置き場に無ければ None と読んで行の型に戻る。
  (assert (= (value-of-fields (RowType "strict" Strict) (FrozenMap {"id" "s1"})) (Strict "s1" None)))
  (assert (= (value-of-fields (RowType "strict" Strict) (FrozenMap {"id" "s1" "note" "n"})) (Strict "s1" "n"))))


(deftest test-typed-rows-round-trip-through-the-generic-effects
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (defn as-maker [program] (harness.as-writer MAKER program))
  (<- born (as-maker (put-typed PARTS #("p1") (Part :id "p1" :label "a" :note "n") (ExpectAbsent))))
  (assert (= born (TypedWritten 1 (Part :id "p1" :label "a" :note "n" :state "open"))) born)
  (<- read (as-maker (read-typed PARTS #("p1"))))
  (assert (and (isinstance read TypedRow) (isinstance read.value Part) (= read.version 1)) read)
  ;; 塗る書き手が欄 color を変えた像を書く。
  (<- painted (harness.as-writer PAINTER (put-typed PARTS #("p1") (.model-copy read.value :update {"color" "red"})
                                                    (ExpectVersion 1))))
  (assert (and (isinstance painted TypedWritten) (= painted.value.color "red") (= painted.value.label "a")) painted)
  ;; 値が None の欄は消える。
  (<- cleared (as-maker (put-typed PARTS #("p1") (.model-copy painted.value :update {"note" None}) (ExpectVersion 2))))
  (assert (and (isinstance cleared TypedWritten) (is cleared.value.note None) (= cleared.version 3)) cleared)
  ;; 置き場は書き手の名で断らない(#2994 で書き手の宣言による実行時の確かめを外した): 塗る書き手が、作る書き手の書いた欄
  ;; label を変える像も、作る書き手の書きと同じに置ける。古い版は今の行を行の型で返す。
  (<- relabeled (harness.as-writer PAINTER (put-typed PARTS #("p1") (.model-copy cleared.value :update {"label" "z"})
                                                      (ExpectVersion 3))))
  (assert (= relabeled (TypedWritten 4 (.model-copy cleared.value :update {"label" "z"}))) relabeled)
  (<- stale (as-maker (put-typed PARTS #("p1") cleared.value (ExpectVersion 1))))
  (assert (and (isinstance stale TypedConflict) (isinstance stale.current TypedRow) (= stale.current.version 4)) stale)
  (<- nothing (as-maker (read-typed PARTS #("p-none"))))
  (assert (= nothing (Missing)))
  (<- ticket (as-maker (put-typed TICKETS #("g1" "t1") (Ticket "g1" "t1" :owner "o1") (ExpectAny))))
  (assert (= ticket (TypedWritten 1 (Ticket "g1" "t1" :state "open" :owner "o1"))) ticket)
  (<- page (as-maker (list-typed PARTS :where (FrozenMap {"color" "red"}))))
  (assert (and (isinstance page TypedPage) (= (lfor row page.rows row.key) [#("p1")])
               (isinstance (. (get page.rows 0) value) Part))
          page)
  (<- changes (as-maker (WatchChanges #("parts") (WatchCursor page.epoch 0))))
  (assert (isinstance changes Changes) changes)
  (setv typed (lfor change changes.items (typed-change PARTS change)))
  (assert (and typed (all (gfor change typed (isinstance change TypedRowChanged)))) typed)
  (assert (= (lfor change typed change.at) (lfor change changes.items change.at)) "確定の刻 at を行の型へ運ぶ")
  (assert (= (. (get typed -1) value) relabeled.value)))


(deftest test-a-row-type-with-a-new-none-field-writes-to-a-store-that-does-not-declare-it
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  ;; 書き手が行の型に既定値 None の欄 method を足し、置き場の宣言(LAW-SCHEMA の parts)はまだ method を知らない形。書きの像は
  ;; 欄を全部(None も)送るが、宣言の外の None は判定の前に落ちるので断られず、行に method は載らない。
  (<- harness (LawSetup))
  (<- born (harness.as-writer MAKER (put-typed NEWER-PARTS #("n1") (NewerPart :id "n1" :label "a") (ExpectAbsent))))
  (assert (= born (TypedWritten 1 (NewerPart :id "n1" :label "a" :state "open"))) born)
  (<- relabeled (harness.as-writer MAKER (put-typed NEWER-PARTS #("n1") (.model-copy born.value :update {"label" "b"})
                                                    (ExpectVersion 1))))
  (assert (= relabeled (TypedWritten 2 (NewerPart :id "n1" :label "b" :state "open"))) relabeled)
  (<- stored (harness.as-writer MAKER (ReadRow "parts" #("n1"))))
  (assert (= stored (Row #("n1") (FrozenMap {"id" "n1" "label" "b" "state" "open"}) 2)) stored)
  ;; 同じ行を、method を持つ新しい行の型と、持たない古い行の型(Part — 型の外の欄を許さない)の両方で読める。
  (<- newer (harness.as-writer MAKER (read-typed NEWER-PARTS #("n1"))))
  (<- older (harness.as-writer MAKER (read-typed PARTS #("n1"))))
  (assert (= #(newer older) #((TypedRow #("n1") (NewerPart :id "n1" :label "b" :state "open") 2)
                              (TypedRow #("n1") (Part :id "n1" :label "b" :state "open") 2)))
          #(newer older))
  ;; method に値を入れた像は今どおり断る(理由の文に欄の名)。
  (<- valued (harness.as-writer MAKER (put-typed NEWER-PARTS #("n1") (.model-copy newer.value :update {"method" "pane"})
                                                 (ExpectVersion 2))))
  (assert (and (isinstance valued Refused) (in "method" valued.reason)) valued)
  ;; 宣言の外の None だけの書きは、空の差分の書き(変わる欄が無い書き)と同じ答え: 行の値を変えずに版が 1 進む。
  (<- only-none (harness.as-writer MAKER (PutRow "parts" #("n1") (FrozenMap {"method" None}) (ExpectVersion 2))))
  (<- nothing (harness.as-writer MAKER (PutRow "parts" #("n1") (FrozenMap) (ExpectVersion 3))))
  (val unchanged (FrozenMap {"id" "n1" "label" "b" "state" "open"}))
  (assert (= #(only-none nothing) #((Written 3 unchanged) (Written 4 unchanged))) #(only-none nothing))
  ;; 束の書き(PutRows)も同じ: 新しい行の型の像(method = None)を在る行と生まれる行へ 1 束で書ける。
  (<- bundle (harness.as-writer MAKER
               (PutRows #((RowWrite "parts" #("n1") (fields-of-value NEWER-PARTS (NewerPart :id "n1" :label "c" :state "open"))
                                    (ExpectVersion 4))
                          (RowWrite "parts" #("n2") (fields-of-value NEWER-PARTS (NewerPart :id "n2" :label "d"))
                                    (ExpectAbsent))))))
  (assert (= bundle (WrittenRows #((Written 5 (FrozenMap {"id" "n1" "label" "c" "state" "open"}))
                                   (Written 1 (FrozenMap {"id" "n2" "label" "d" "state" "open"})))))
          bundle))
