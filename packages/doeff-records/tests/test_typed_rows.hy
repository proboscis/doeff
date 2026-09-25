;; 表ごとの行の型で読み書きする層(typed.hy)— 呼び手は欄 → 値の写像を見ずに、pydantic の model / dataclass で読み書きする。
;; 同じ筋書きを memory と PostgreSQL の handler の両方で回す(層は handler を足さないので、どの組の上でも同じ答え)。
(require doeff-hy.macros [deftest <-])
(import dataclasses [dataclass])
(import doeff_hy.frozen [FrozenMap])
(import pydantic [BaseModel ConfigDict])
(import doeff_records.values [ExpectAbsent ExpectVersion ExpectAny Refused Missing WatchCursor Changes])
(import doeff_records.effects [WatchChanges])
(import doeff_records.typed [RowType TypedRow TypedPage TypedWritten TypedConflict TypedRowChanged read-typed list-typed put-typed
                             typed-change fields-of-value])
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

(setv PARTS (RowType "parts" Part)
      TICKETS (RowType "tickets" Ticket))


(deftest test-the-write-image-lists-every-field-and-none-removes
  (assert (= (fields-of-value PARTS (Part :id "p1" :label "a"))
             {"id" "p1" "label" "a" "color" None "state" None "note" None "grant" None}))
  (assert (= (fields-of-value TICKETS (Ticket "g" "t" :owner "o")) {"group" "g" "id" "t" "state" None "owner" "o"})))


(deftest test-typed-rows-round-trip-through-the-generic-effects
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (defn as-maker [program] (harness.as-writer MAKER program))
  (<- born (as-maker (put-typed PARTS #("p1") (Part :id "p1" :label "a" :note "n") (ExpectAbsent))))
  (assert (= born (TypedWritten 1 (Part :id "p1" :label "a" :note "n" :state "open"))) born)
  (<- read (as-maker (read-typed PARTS #("p1"))))
  (assert (and (isinstance read TypedRow) (isinstance read.value Part) (= read.version 1)) read)
  ;; 塗る書き手は自分の欄だけを変えた像を書く(値の変わらない欄は名簿で照らさない)。
  (<- painted (harness.as-writer PAINTER (put-typed PARTS #("p1") (.model-copy read.value :update {"color" "red"})
                                                    (ExpectVersion 1))))
  (assert (and (isinstance painted TypedWritten) (= painted.value.color "red") (= painted.value.label "a")) painted)
  ;; 値が None の欄は消える。
  (<- cleared (as-maker (put-typed PARTS #("p1") (.model-copy painted.value :update {"note" None}) (ExpectVersion 2))))
  (assert (and (isinstance cleared TypedWritten) (is cleared.value.note None) (= cleared.version 3)) cleared)
  ;; 他人の欄を変える像は断る・古い版は今の行を行の型で返す。
  (<- refused (harness.as-writer PAINTER (put-typed PARTS #("p1") (.model-copy cleared.value :update {"label" "z"})
                                                    (ExpectVersion 3))))
  (assert (isinstance refused Refused) refused)
  (<- stale (as-maker (put-typed PARTS #("p1") cleared.value (ExpectVersion 1))))
  (assert (and (isinstance stale TypedConflict) (isinstance stale.current TypedRow) (= stale.current.version 3)) stale)
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
  (assert (= (. (get typed -1) value) cleared.value)))
