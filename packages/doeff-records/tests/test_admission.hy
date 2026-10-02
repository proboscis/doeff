;; 宣言の検めと判断の純関数の反例(handler を通さない)。末尾の 1 本だけ、宣言を途中で差し替えた memory の置き場で判断の答えが
;; 書きの答え(Written)になることを確かめる(公開 effect では宣言の外の欄を持つ行を作れない — 古い行は宣言を差し替える前に書く)。
(require doeff-hy.macros [deftest <- val])
(import dataclasses)
(import doeff [with_handlers])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [FieldDecl TableDecl StreamDecl RecordsSchema KeepFor ByKeySuffix EachEvent Row Missing Conflict Refused NotIndexed UndeclaredTable
                              ExpectAbsent ExpectVersion ExpectAny RowsRefused Written WrittenRows])
(import doeff_records.effects [PutRow PutRows RowWrite ReadRow ListRows])
(import doeff_records.admission [json-equal? key-text judge-expect judge-put judge-put-rows where-refusal row-expired?
                                 Admitted])
(import doeff_records.memory [MemoryStore memory-records-handler])
(import doeff_records.laws [LAW-SCHEMA MAKER])


(defn refuses? [thunk exception]
  (try (thunk) False (except [exception] True)))


(deftest test-a-declaration-that-cannot-hold-is-refused-at-construction
  (setv base {"name" "t" "key_fields" #("id") "fields" #((FieldDecl "id" #("w")) (FieldDecl "state" #("w")))})
  (assert (TableDecl #** base))
  ;; 鍵の欄の書き手が無い(誰も行を作れない)・同じ名の欄が 2 つ・欄の宣言でない物・宣言の外の索引・initial が語彙の外・
  ;; 終端の語が語彙の外・終端の無い KeepFor・鍵の欄を承認の欄にする・表の名の綴りの外。
  (for [broken [{"fields" #((FieldDecl "state" #("w")))}
                {"fields" #((FieldDecl "id" #("w")) (FieldDecl "id" #("v")))}
                {"fields" {"id" #("w")}}
                {"indexes" #("color")}
                {"states" #("open") "initial" "gone"}
                {"states" #("open") "initial" "open" "terminal" #("done")}
                {"states" #("open") "initial" "open" "retention" (KeepFor 10)}
                {"operator-paths" #("id")}
                {"name" "Bad Name"}]]
    (setv args (| base (dfor #(k v) (.items broken) (.replace k "-" "_") v)))
    (assert (refuses? (fn [] (TableDecl #** args)) #(ValueError TypeError)) broken))
  (assert (refuses? (fn [] (StreamDecl "s" #())) ValueError) "誰も積めない追記の列")
  (assert (refuses? (fn [] (StreamDecl "s" #("w") :retention-group (ByKeySuffix ":"))) ValueError) "消えない列の保持の組")
  (assert (refuses? (fn [] (StreamDecl "s" #("w") :retention-group "each")) TypeError) "保持の組の型の外")
  (assert (refuses? (fn [] (ByKeySuffix "")) ValueError) "空の区切り")
  (assert (= (. (StreamDecl "s" #("w")) retention-group) (EachEvent)) "既定は出来事ごと")
  (assert (refuses? (fn [] (FieldDecl "x" #())) ValueError) "誰も書けない欄")
  (assert (refuses? (fn [] (LAW-SCHEMA.table "nope")) UndeclaredTable))
  (assert (refuses? (fn [] (PutRow "parts" #("p1") {} "any")) TypeError))
  (assert (refuses? (fn [] (ListRows "parts" :limit 0)) ValueError)))


(deftest test-json-equality-does-not-confuse-true-with-one
  (assert (json-equal? {"a" [1 2.0]} {"a" [1.0 2]}))
  (assert (not (json-equal? True 1)))
  (assert (not (json-equal? {"a" 1} {"a" 1 "b" None}))))


(deftest test-key-text-is-ascii-and-round-trips
  ;; 頁の順は鍵の綴りの符号点の順(PG では COLLATE "C")。綴りが ASCII だけなので、byte の順と符号点の順が同じになる
  ;; (UTF-8 の多 byte の字も \u の綴りに落ちる)。鍵の部品を前から比べた順とは限らない(順の約束は綴りの順だけ)。
  (import doeff_records.admission [key-from-text])
  (for [key [#("a" "b") #("a") #("日本" "x") #("quote\"" "back\\slash")]]
    (setv text (key-text key))
    (assert (.isascii text) text)
    (assert (= (key-from-text text) key))))


(deftest test-expectation-and-admission-order
  (setv decl (LAW-SCHEMA.table "parts")
        row (Row #("p1") {"id" "p1" "state" "closed"} 3))
  (assert (= (judge-expect (ExpectVersion 2) row) (Conflict row)))
  (assert (= (judge-expect (ExpectVersion 1) None) (Conflict (Missing))))
  (assert (is (judge-expect (ExpectAny) row) None))
  ;; 終端の行は書き手を問う前に断る(誰の書きでも同じ理由)。
  (setv frozen (judge-put decl "stranger" row #("p1") {"label" "x"} :operators LAW-SCHEMA.operators))
  (assert (and (isinstance frozen Refused) (in "終端" frozen.reason)) frozen)
  ;; 生まれる行は initial を持ち、鍵の欄を値に置く。
  (setv born (judge-put decl "maker" None #("p2") {"label" "x"} :operators LAW-SCHEMA.operators))
  (assert (= born (Admitted {"id" "p2" "label" "x" "state" "open"})))
  (assert (= (where-refusal decl {"id" "p1" "color" "red" "note" "n"}) (NotIndexed #("note")))))


(deftest test-operator-paths-are-judged-by-the-writer-principal
  ;; operator の宣言の欄は、欄の書き手かつ operator の主体の書き手だけ。書き手の名は handler の組み立ての値で、
  ;; judge-put の答えは同じ差分でも書き手の名だけで変わる(effect の中身で operator を名乗る口は無い)。
  (setv decl (LAW-SCHEMA.table "parts")
        row (Row #("p1") {"id" "p1" "state" "open"} 1))
  ;; maker は grant の欄の書き手だが operator の主体でない → operator の段で断る。stranger・painter は欄の書き手の段で先に断る。
  (setv maker (judge-put decl "maker" row #("p1") {"grant" "yes"} :operators LAW-SCHEMA.operators))
  (assert (and (isinstance maker Refused) (in "operator の宣言の欄" maker.reason)) maker)
  (for [writer ["stranger" "painter"]]
    (assert (isinstance (judge-put decl writer row #("p1") {"grant" "yes"} :operators LAW-SCHEMA.operators) Refused) writer))
  (assert (= (judge-put decl "overseer" row #("p1") {"grant" "yes"} :operators LAW-SCHEMA.operators)
             (Admitted {"id" "p1" "state" "open" "grant" "yes"})))
  ;; operator の主体でも欄の書き手でなければ断る(何でも書ける主体ではない)・operator の欄の外は主体を問わない。
  (assert (isinstance (judge-put decl "overseer" row #("p1") {"label" "x"} :operators LAW-SCHEMA.operators) Refused))
  (assert (isinstance (judge-put decl "maker" row #("p1") {"label" "x"} :operators LAW-SCHEMA.operators) Admitted))
  ;; operator の一覧が空の置き場では、operator の欄は誰も書けない(安全側の既定)。
  (assert (isinstance (judge-put decl "overseer" row #("p1") {"grant" "yes"} :operators #()) Refused)))


(deftest test-a-schema-whose-operator-path-no-operator-can-write-is-refused-at-construction
  (setv parts (LAW-SCHEMA.table "parts"))
  ;; grant の書き手(maker・overseer)に operator の主体が居ない一覧・空の一覧・綴りの外の名は、宣言の時に止める。
  (for [operators [#("someone-else") #() #("") #("a:b") ["overseer"]]]
    (assert (refuses? (fn [] (RecordsSchema :tables {"parts" parts} :operators operators)) #(ValueError TypeError)) operators))
  (assert (RecordsSchema :tables {"parts" parts} :operators #("overseer"))))


(deftest test-founders-write-a-field-only-when-the-row-is-born
  ;; 誕生の書き手(FieldDecl.founders): 行が無い時だけ欄を書け(operator の宣言の欄でも operator の主体を要さない)、在る行には効かない。
  (setv charters (LAW-SCHEMA.table "charters")
        row (Row #("c1") {"name" "c1" "rule" "r0"} 1))
  (assert (isinstance (judge-put charters "maker" None #("c1") {"rule" "r0"} :operators LAW-SCHEMA.operators) Admitted))
  (setv beyond (judge-put charters "maker" None #("c1") {"rule" "r0" "note" "n"} :operators LAW-SCHEMA.operators))
  (assert (and (isinstance beyond Refused) (in "note" beyond.reason)) beyond)
  (assert (isinstance (judge-put charters "stranger" None #("c1") {"rule" "r0"} :operators LAW-SCHEMA.operators) Refused))
  (setv rewrite (judge-put charters "maker" row #("c1") {"rule" "r1"} :operators LAW-SCHEMA.operators))
  (assert (and (isinstance rewrite Refused) (in "rule" rewrite.reason)) rewrite)
  ;; 同じ値の書き直しは変わる欄が無いので断らない(据え付けの撃ち直し)・operator の主体は在る行を書ける。
  (assert (isinstance (judge-put charters "maker" row #("c1") {"rule" "r0"} :operators LAW-SCHEMA.operators) Admitted))
  (assert (isinstance (judge-put charters "overseer" row #("c1") {"rule" "r1"} :operators LAW-SCHEMA.operators) Admitted))
  ;; founders の綴りの外は宣言の時に止める。
  (for [founders [#("") ["maker"] #(1)]]
    (assert (refuses? (fn [] (FieldDecl "x" #("w") :founders founders)) #(ValueError TypeError)) founders)))


(deftest test-only-terminal-rows-of-keep-for-tables-expire
  (setv tickets (LAW-SCHEMA.table "tickets") parts (LAW-SCHEMA.table "parts"))
  (assert (row-expired? tickets {"state" "done"} 0 60000))
  (assert (not (row-expired? tickets {"state" "done"} 0 59999)))
  (assert (not (row-expired? tickets {"state" "open"} 0 10000000)))
  (assert (not (row-expired? parts {"state" "closed"} 0 10000000))))


;; --- 宣言の外で値が None の欄(行の型に既定値 None の欄を足した書き手が、その欄を宣言していない置き場へ書く形)-----------------

(deftest test-an-undeclared-field-with-none-is-dropped-before-the-write-is-judged
  ;; 行の型の書きの像は欄を全部(None も)送る。宣言に無い欄で値が None の物だけを差分から落とし、残りの差分を今どおりに判じる。
  (val decl (LAW-SCHEMA.table "parts"))
  (val row (Row #("p1") {"id" "p1" "label" "a" "color" "red" "state" "open"} 1))
  ;; 宣言に無い欄 method の None は落ち、他の欄の差分は書かれる(確定する値に method は無い)— 在る行も生まれる行も。
  (assert (= (judge-put decl "maker" row #("p1") {"label" "b" "method" None} :operators LAW-SCHEMA.operators)
             (Admitted {"id" "p1" "label" "b" "color" "red" "state" "open"})))
  (assert (= (judge-put decl "maker" None #("p2") {"label" "b" "method" None} :operators LAW-SCHEMA.operators)
             (Admitted {"id" "p2" "label" "b" "state" "open"})))
  ;; 宣言に無い欄に値があれば今どおり断る(理由の文に欄の名)— 綴りの誤った欄も同じ。
  (val valued (judge-put decl "maker" row #("p1") {"label" "b" "method" "pane"} :operators LAW-SCHEMA.operators))
  (assert (and (isinstance valued Refused) (in "method" valued.reason)) valued)
  (val misspelled (judge-put decl "maker" row #("p1") {"label" "b" "metod" "x"} :operators LAW-SCHEMA.operators))
  (assert (and (isinstance misspelled Refused) (in "metod" misspelled.reason)) misspelled)
  ;; 宣言に在る欄の None は今どおりその欄を消す(宣言の外の None と並んでも)。
  (assert (= (judge-put decl "maker" row #("p1") {"color" None "method" None} :operators LAW-SCHEMA.operators)
             (Admitted {"id" "p1" "label" "a" "state" "open"})))
  ;; 書き手の照らしは変わらない: 落とした後の差分の変わる欄で照らす(painter は color の書き手で、label の書き手でない)。
  (assert (= (judge-put decl "painter" row #("p1") {"color" "blue" "method" None} :operators LAW-SCHEMA.operators)
             (Admitted {"id" "p1" "label" "a" "color" "blue" "state" "open"})))
  (assert (isinstance (judge-put decl "painter" row #("p1") {"label" "z" "method" None} :operators LAW-SCHEMA.operators) Refused)))


(deftest test-a-none-for-a-field-the-declaration-no-longer-has-leaves-the-old-row-field-in-place
  ;; 欄 legacy を宣言から外した後の置き場の古い行(外す前に書いた legacy が残る)。その欄の None は判定の前に落ちるので、宣言の外の
  ;; 欄の書き手を尋ねて例外(UndeclaredField)にならず、legacy は行に残る(消すのは欄を宣言から外す前の書き直し)。
  (val decl (LAW-SCHEMA.table "parts"))
  (val row (Row #("p1") {"id" "p1" "label" "a" "state" "open" "legacy" "x"} 1))
  (assert (= (judge-put decl "maker" row #("p1") {"label" "b" "legacy" None} :operators LAW-SCHEMA.operators)
             (Admitted {"id" "p1" "label" "b" "state" "open" "legacy" "x"})))
  (assert (= (judge-put decl "painter" row #("p1") {"color" "blue" "legacy" None} :operators LAW-SCHEMA.operators)
             (Admitted {"id" "p1" "label" "a" "color" "blue" "state" "open" "legacy" "x"})))
  ;; 値のある legacy は今どおり宣言の外の欄として断る。
  (val valued (judge-put decl "maker" row #("p1") {"legacy" "y"} :operators LAW-SCHEMA.operators))
  (assert (and (isinstance valued Refused) (in "legacy" valued.reason)) valued))


(deftest test-a-write-of-only-undeclared-nones-is-judged-like-a-write-that-changes-nothing
  ;; 宣言の外の None の欄だけの書きは、落とすと空の差分 — 今の「変わる欄が無い書き」と同じ答えにする。書き手と行の有る無しを
  ;; 問わず、空の差分の書きと同じ答え。
  (val decl (LAW-SCHEMA.table "parts"))
  (val row (Row #("p1") {"id" "p1" "label" "a" "state" "open"} 1))
  (val pairs (lfor writer ["maker" "painter" "stranger"] current [row None]
                   #((judge-put decl writer current #("p1") {"method" None} :operators LAW-SCHEMA.operators)
                     (judge-put decl writer current #("p1") {} :operators LAW-SCHEMA.operators))))
  (assert (all (gfor #(only-none empty) pairs (= only-none empty))) pairs)
  ;; その答えの形: 在る行は値を変えずに許す(変わる欄が無いので書き手を照らさない)・生まれる行は鍵の欄の書き手(maker)だけが作る。
  (assert (= (judge-put decl "stranger" row #("p1") {"method" None} :operators LAW-SCHEMA.operators) (Admitted row.value)))
  (assert (= (judge-put decl "maker" None #("p1") {"method" None} :operators LAW-SCHEMA.operators)
             (Admitted {"id" "p1" "state" "open"})))
  (assert (isinstance (judge-put decl "stranger" None #("p1") {"method" None} :operators LAW-SCHEMA.operators) Refused)))


(deftest test-put-rows-drops-undeclared-nones-row-by-row-the-same-way
  ;; 束の書き(judge-put-rows)も行ごとに同じ判定: 宣言に無い欄の None(行の型に足した新しい欄・宣言から外した古い欄)は落ち、
  ;; 値のある宣言の外の欄は断る(束の中の位置と理由の文の欄の名)。
  (val legacy-row (Row #("p2") {"id" "p2" "label" "a" "state" "open" "legacy" "x"} 1))
  (val writes #((RowWrite "parts" #("p1") {"label" "b" "method" None} (ExpectAbsent))
                (RowWrite "parts" #("p2") {"label" "c" "legacy" None} (ExpectVersion 1))))
  (assert (= (judge-put-rows LAW-SCHEMA "maker" writes #(None legacy-row))
             #((Admitted {"id" "p1" "label" "b" "state" "open"})
               (Admitted {"id" "p2" "label" "c" "state" "open" "legacy" "x"}))))
  (val valued (judge-put-rows LAW-SCHEMA "maker" (+ writes #((RowWrite "parts" #("p3") {"method" "pane"} (ExpectAbsent))))
                              #(None legacy-row None)))
  (assert (and (isinstance valued RowsRefused) (= valued.index 2) (in "method" valued.reason)) valued))


(deftest test-a-store-whose-declaration-dropped-a-field-writes-nones-for-it-and-keeps-the-old-field
  ;; 置き場の宣言から欄 legacy を外す前に書いた行(legacy = "x")が残る memory の置き場。宣言を差し替えた後、その欄の None を含む
  ;; 書きは PutRow でも PutRows でも例外にならず Written で版が進み、legacy は行に残る。値のある legacy は今どおり断る。
  (val parts (LAW-SCHEMA.table "parts"))
  (val before (dataclasses.replace LAW-SCHEMA
                                   :tables (.updated LAW-SCHEMA.tables
                                                     {"parts" (dataclasses.replace parts
                                                                                   :fields (+ parts.fields #((FieldDecl "legacy" #(MAKER)))))})))
  (val store (MemoryStore before))
  (val handlers [(sim-time-handler :clock (SimClock)) (memory-records-handler store MAKER)])
  (<- born (with_handlers handlers (PutRow "parts" #("p1") (FrozenMap {"label" "a" "legacy" "x"}) (ExpectAbsent))))
  (assert (= born (Written 1 (FrozenMap {"id" "p1" "label" "a" "legacy" "x" "state" "open"}))) born)
  (setv store.schema LAW-SCHEMA)
  (<- relabeled (with_handlers handlers (PutRow "parts" #("p1") (FrozenMap {"label" "b" "legacy" None}) (ExpectVersion 1))))
  (assert (= relabeled (Written 2 (FrozenMap {"id" "p1" "label" "b" "legacy" "x" "state" "open"}))) relabeled)
  (<- bundled (with_handlers handlers (PutRows #((RowWrite "parts" #("p1") (FrozenMap {"label" "c" "legacy" None}) (ExpectVersion 2))))))
  (assert (= bundled (WrittenRows #((Written 3 (FrozenMap {"id" "p1" "label" "c" "legacy" "x" "state" "open"}))))) bundled)
  (<- valued (with_handlers handlers (PutRow "parts" #("p1") (FrozenMap {"legacy" "y"}) (ExpectVersion 3))))
  (assert (and (isinstance valued Refused) (in "legacy" valued.reason)) valued)
  (<- kept (with_handlers handlers (ReadRow "parts" #("p1"))))
  (assert (= kept (Row #("p1") (FrozenMap {"id" "p1" "label" "c" "legacy" "x" "state" "open"}) 3)) kept))
