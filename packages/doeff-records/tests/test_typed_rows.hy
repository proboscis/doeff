;; 表ごとの行の型で読み書きする層(typed.hy)— 呼び手は欄 → 値の写像を見ずに、行の型(素の defrecord)で読み書きする。
;; 同じ筋書きを memory と PostgreSQL の handler の両方で回す(層は handler を足さないので、どの組の上でも同じ答え)。
(require doeff-hy.macros [deftest <- val])
(import dataclasses [dataclass replace])
(import enum [StrEnum])
(import doeff_hy.frozen [FrozenMap])
(import doeff_hy.wire_codec :as wire-codec)
(import doeff_hy.wire_malformed [Malformed MalformedField])
(import doeff_records.values [ExpectAbsent ExpectVersion ExpectAny Refused Missing WatchCursor Changes Row Written WrittenRows])
(import doeff_records.effects [WatchChanges ReadRow PutRow PutRows RowWrite])
(import doeff_records.typed [RowType RowMalformed TypedRow TypedPage TypedWritten TypedConflict TypedRowChanged read-typed list-typed
                             put-typed typed-change typed-row fields-of-value value-of-fields row-value])
(import doeff_records.laws [MAKER PAINTER])
(import tests.interpreters [LawSetup])


(defclass [(dataclass :frozen True)] Part []
  "表 parts の行の型(LAW-SCHEMA の欄ちょうど)。"
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

(defclass [(dataclass :frozen True)] NewerPart [Part]
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


(defclass [(dataclass :frozen True)] Lazy []
  "解き手を組む時期の検だけが使う行の型(他の検が先に組まないよう、この検の外では使わない)。"
  (#^ str id))


(deftest test-a-row-type-builds-its-codec-at-the-first-mapping-and-keeps-it
  ;; 表の行の型は使い手の module の読み込みの時に作られる(使い手の画面の検で 29 表)。作る時に解き手を組むと、
  ;; 検が写さない表の分まで読み込みの固定費に載る(ki-dd5358f7eb46)。作る時は組まず、最初の写しで 1 度だけ組んで型に覚える。
  (val row-type (RowType "lazy" Lazy))
  (assert (not-in "__doeff_row_codec__" (vars Lazy)))
  (assert (= (fields-of-value row-type (Lazy "l")) {"id" "l"}))
  (val built (get (vars Lazy) "__doeff_row_codec__"))
  (assert (= (value-of-fields row-type (FrozenMap {"id" "l"})) (Lazy "l")))
  (assert (is row-type.codec built (wire-codec.row-codec-of Lazy))))


(defclass Shade [StrEnum]
  "入れ子の行の型の欄の値の綴り(StrEnum の値で書いて読む)。"
  (setv LIGHT "light" DARK "dark"))

(defclass [(dataclass :frozen True)] Swatch []
  "行の型の中の素の defrecord(入れ子の欄も Python の欄の名で書いて読む)。"
  (#^ str hex)
  (setv #^ Shade shade Shade.LIGHT))

(defclass [(dataclass :frozen True)] Palette []
  "入れ子・配列・数・既定値の欄を持つ行の型(行の解き手の strict の読みと全部の欄の書きの検)。"
  (#^ str id)
  (#^ int count)
  (setv #^ float weight 0.0)
  (setv #^ (get tuple #(Swatch ...)) swatches #())
  (setv #^ (| Swatch None) main None)
  (setv #^ bool shared False))

(val PALETTES (RowType "palettes" Palette))


(deftest test-the-write-image-keeps-default-fields-and-nested-rows-whole
  ;; 行の書きは欄ごとの差分なので、既定値と等しい欄も書く(省くと前の行の値が残る)。入れ子の素の defrecord も全部の欄・StrEnum は
  ;; 値の綴り・tuple は配列。読み戻すと同じ値。
  (val palette (Palette "p" 2 :swatches #((Swatch "#fff") (Swatch "#000" Shade.DARK))))
  (val image (fields-of-value PALETTES palette))
  (assert (= image {"id" "p" "count" 2 "weight" 0.0 "swatches" #({"hex" "#fff" "shade" "light"} {"hex" "#000" "shade" "dark"})
                    "main" None "shared" False})
          image)
  (assert (= (value-of-fields PALETTES image) palette)))


(deftest test-a-row-is-read-strictly-and-a-mismatch-is-malformed
  ;; 欄ごとの判じ方は defwire と同じ strict: 文字列の数・真偽の数・小数の整数は断る(Malformed — 場所は Python の欄の名と配列の位置)。
  ;; 整数は float の欄に入り、配列は tuple の欄に入る。知らない欄は読み捨てる(欄を足した新しい書き手の行を古い読み手が読める)。
  (assert (= (row-value PALETTES (FrozenMap {"id" "p" "count" 1 "weight" 2 "swatches" #({"hex" "#fff" "extra" 1}) "shared" False "newer" "x"}))
             (Palette "p" 1 :weight 2.0 :swatches #((Swatch "#fff"))))
          "整数は float の欄・知らない欄は読み捨て")
  (assert (= (row-value PALETTES (FrozenMap {"id" "p" "count" "1" "weight" 1.0 "shared" 1 "swatches" #({"hex" "#fff" "shade" "LIGHT"})}))
             (Malformed :wire-type "Palette" :fields #((MalformedField :field "count" :reason "Input should be a valid integer [int_type]")
                                    (MalformedField :field "swatches.0.shade" :reason "Input should be 'light' or 'dark' [enum]")
                                    (MalformedField :field "shared" :reason "Input should be a valid boolean [bool_type]")))))
  ;; 置き場に無い欄は None と読む(書きで None の欄は消えるため): None を許さない欄が置き場に無い行は、既定値が在ってもその欄の型の
  ;; 誤り(以前の pydantic の読みと同じ — 書き手は全部の欄を書くので、行の型より古い書き手の行だけが当たる)。
  (assert (= (row-value PALETTES (FrozenMap {"id" "p" "count" 1 "swatches" #() "shared" False}))
             (Malformed :wire-type "Palette" :fields #((MalformedField :field "weight" :reason "Input should be a valid number [float_type]"))))))


(deftest test-reading-a-malformed-row-raises-row-malformed-with-the-same-malformed
  ;; 行を型へ読む関数(value-of-fields・typed-row ほか)は、合わない行で RowMalformed(ValueError)を投げ、row-value と同じ Malformed を運ぶ。
  (val fields (FrozenMap {"id" "p" "count" 1.5}))
  (for [read [(fn [] (value-of-fields PALETTES fields)) (fn [] (typed-row PALETTES (Row #("p") fields 3)))]]
    (try
      (read)
      (raise (AssertionError "RowMalformed が投げられていない"))
      (except [error RowMalformed]
        (assert (isinstance error ValueError))
        (assert (= #(error.table error.malformed) #("palettes" (row-value PALETTES fields))) error)
        (assert (in "count" (str error)) (str error))))))


(deftest test-a-row-type-must-be-a-plain-record
  ;; 行の型は素の defrecord だけ(dataclass でない型は作る時に断る)。
  (try
    (RowType "x" str)
    (raise (AssertionError "TypeError が投げられていない"))
    (except [TypeError])))


(deftest test-typed-rows-round-trip-through-the-generic-effects
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (defn as-maker [program] (harness.as-writer MAKER program))
  (<- born (as-maker (put-typed PARTS #("p1") (Part :id "p1" :label "a" :note "n") (ExpectAbsent))))
  (assert (= born (TypedWritten 1 (Part :id "p1" :label "a" :note "n" :state "open") born.position)) born)
  (<- read (as-maker (read-typed PARTS #("p1"))))
  (assert (and (isinstance read TypedRow) (isinstance read.value Part) (= read.version 1)) read)
  ;; 塗る書き手が欄 color を変えた像を書く。
  (<- painted (harness.as-writer PAINTER (put-typed PARTS #("p1") (replace read.value :color "red")
                                                    (ExpectVersion 1))))
  (assert (and (isinstance painted TypedWritten) (= painted.value.color "red") (= painted.value.label "a")) painted)
  ;; 値が None の欄は消える。
  (<- cleared (as-maker (put-typed PARTS #("p1") (replace painted.value :note None) (ExpectVersion 2))))
  (assert (and (isinstance cleared TypedWritten) (is cleared.value.note None) (= cleared.version 3)) cleared)
  ;; 置き場は書き手の名で断らない(#2994 で書き手の宣言による実行時の確かめを外した): 塗る書き手が、作る書き手の書いた欄
  ;; label を変える像も、作る書き手の書きと同じに置ける。古い版は今の行を行の型で返す。
  (<- relabeled (harness.as-writer PAINTER (put-typed PARTS #("p1") (replace cleared.value :label "z")
                                                      (ExpectVersion 3))))
  (assert (= relabeled (TypedWritten 4 (replace cleared.value :label "z") relabeled.position)) relabeled)
  (<- stale (as-maker (put-typed PARTS #("p1") cleared.value (ExpectVersion 1))))
  (assert (and (isinstance stale TypedConflict) (isinstance stale.current TypedRow) (= stale.current.version 4)) stale)
  (<- nothing (as-maker (read-typed PARTS #("p-none"))))
  (assert (= nothing (Missing)))
  (<- ticket (as-maker (put-typed TICKETS #("g1" "t1") (Ticket "g1" "t1" :owner "o1") (ExpectAny))))
  (assert (= ticket (TypedWritten 1 (Ticket "g1" "t1" :state "open" :owner "o1") ticket.position)) ticket)
  (<- page (as-maker (list-typed PARTS :where (FrozenMap {"color" "red"}))))
  (assert (and (isinstance page TypedPage) (= (lfor row page.rows row.key) [#("p1")])
               (isinstance (. (get page.rows 0) value) Part))
          page)
  ;; 値の組の絞り(where-any)も同じ読みで渡せる — 組のどれかに等しい行を行の型で返す。
  (<- any-page (as-maker (list-typed PARTS :where-any (FrozenMap {"color" #("blue" "red")}))))
  (assert (and (isinstance any-page TypedPage) (= (lfor row any-page.rows row.key) [#("p1")])
               (isinstance (. (get any-page.rows 0) value) Part))
          any-page)
  (<- none-page (as-maker (list-typed PARTS :where (FrozenMap {"label" "z"}) :where-any (FrozenMap {"color" #("blue")}))))
  (assert (and (isinstance none-page TypedPage) (= none-page.rows #())) none-page)
  (<- changes (as-maker (WatchChanges #("parts") (WatchCursor page.epoch 0))))
  (assert (isinstance changes Changes) changes)
  (setv typed (lfor change changes.items (typed-change PARTS change)))
  (assert (and typed (all (gfor change typed (isinstance change TypedRowChanged)))) typed)
  (assert (= (lfor change typed change.at) (lfor change changes.items change.at)) "確定の刻 at を行の型へ運ぶ")
  (assert (= (. (get typed -1) value) relabeled.value))
  ;; 行の型の書きの答えも、その書きの変更の位置(Written.position)を運ぶ — 変更の列の番号と置き場の版が同じ。
  (assert (= (lfor written [born painted cleared relabeled] written.position)
             (lfor change changes.items (WatchCursor page.epoch change.sequence)))
          #(born painted cleared relabeled changes)))


(deftest test-a-row-type-with-a-new-none-field-writes-to-a-store-that-does-not-declare-it
  {:interpreters ["memory" "pg" "http-memory" "http-pg"]}
  ;; 書き手が行の型に既定値 None の欄 method を足し、置き場の宣言(LAW-SCHEMA の parts)はまだ method を知らない形。書きの像は
  ;; 欄を全部(None も)送るが、宣言の外の None は判定の前に落ちるので断られず、行に method は載らない。
  (<- harness (LawSetup))
  (<- born (harness.as-writer MAKER (put-typed NEWER-PARTS #("n1") (NewerPart :id "n1" :label "a") (ExpectAbsent))))
  (assert (= born (TypedWritten 1 (NewerPart :id "n1" :label "a" :state "open") born.position)) born)
  (<- relabeled (harness.as-writer MAKER (put-typed NEWER-PARTS #("n1") (replace born.value :label "b")
                                                    (ExpectVersion 1))))
  (assert (= relabeled (TypedWritten 2 (NewerPart :id "n1" :label "b" :state "open") relabeled.position)) relabeled)
  (<- stored (harness.as-writer MAKER (ReadRow "parts" #("n1"))))
  (assert (= stored (Row #("n1") (FrozenMap {"id" "n1" "label" "b" "state" "open"}) 2)) stored)
  ;; 同じ行を、method を持つ新しい行の型と、持たない古い行の型(Part — method を知らない)の両方で読める。
  (<- newer (harness.as-writer MAKER (read-typed NEWER-PARTS #("n1"))))
  (<- older (harness.as-writer MAKER (read-typed PARTS #("n1"))))
  (assert (= #(newer older) #((TypedRow #("n1") (NewerPart :id "n1" :label "b" :state "open") 2)
                              (TypedRow #("n1") (Part :id "n1" :label "b" :state "open") 2)))
          #(newer older))
  ;; method に値を入れた像は今どおり断る(理由の文に欄の名)。
  (<- valued (harness.as-writer MAKER (put-typed NEWER-PARTS #("n1") (replace newer.value :method "pane")
                                                 (ExpectVersion 2))))
  (assert (and (isinstance valued Refused) (in "method" valued.reason)) valued)
  ;; 宣言の外の None だけの書きは、空の差分の書き(変わる欄が無い書き)と同じ答え: 行の値を変えずに版が 1 進む。
  (<- only-none (harness.as-writer MAKER (PutRow "parts" #("n1") (FrozenMap {"method" None}) (ExpectVersion 2))))
  (<- nothing (harness.as-writer MAKER (PutRow "parts" #("n1") (FrozenMap) (ExpectVersion 3))))
  (val unchanged (FrozenMap {"id" "n1" "label" "b" "state" "open"}))
  (assert (= #(only-none nothing) #((Written 3 unchanged only-none.position) (Written 4 unchanged nothing.position))) #(only-none nothing))
  ;; 束の書き(PutRows)も同じ: 新しい行の型の像(method = None)を在る行と生まれる行へ 1 束で書ける。
  (<- bundle (harness.as-writer MAKER
               (PutRows #((RowWrite "parts" #("n1") (fields-of-value NEWER-PARTS (NewerPart :id "n1" :label "c" :state "open"))
                                    (ExpectVersion 4))
                          (RowWrite "parts" #("n2") (fields-of-value NEWER-PARTS (NewerPart :id "n2" :label "d"))
                                    (ExpectAbsent))))))
  (assert (= bundle (WrittenRows #((Written 5 (FrozenMap {"id" "n1" "label" "c" "state" "open"}) (. (get bundle.items 0) position))
                                   (Written 1 (FrozenMap {"id" "n2" "label" "d" "state" "open"}) (. (get bundle.items 1) position)))))
          bundle))
