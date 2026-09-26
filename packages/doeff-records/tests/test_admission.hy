;; 宣言の検めと判断の純関数の反例(handler を通さない)。
(require doeff-hy.macros [deftest])
(import doeff_records.values [FieldDecl TableDecl StreamDecl RecordsSchema KeepFor Row Missing Conflict Refused NotIndexed UndeclaredTable
                              ExpectAbsent ExpectVersion ExpectAny])
(import doeff_records.effects [PutRow ListRows])
(import doeff_records.admission [json-equal? key-text judge-expect judge-put where-refusal row-expired?
                                 Admitted])
(import doeff_records.laws [LAW-SCHEMA])


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


(deftest test-only-terminal-rows-of-keep-for-tables-expire
  (setv tickets (LAW-SCHEMA.table "tickets") parts (LAW-SCHEMA.table "parts"))
  (assert (row-expired? tickets {"state" "done"} 0 60000))
  (assert (not (row-expired? tickets {"state" "done"} 0 59999)))
  (assert (not (row-expired? tickets {"state" "open"} 0 10000000)))
  (assert (not (row-expired? parts {"state" "closed"} 0 10000000))))
