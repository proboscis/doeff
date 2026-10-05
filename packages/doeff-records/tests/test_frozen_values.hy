;; 行の値・書きの差分・出来事の本文は、作った後に変えられない(凍らせた写像)— 反例: 呼び手が答えの dict を書き換えると
;; 置き場の行まで変わる・effect を作った後に元の dict を書き換えると撃つ書きが変わる。
(require doeff-hy.macros [deftest <-])
(import copy)
(import dataclasses)
(import pickle)
(import doeff_hy.frozen [FrozenMap thaw-json])
(import doeff_records.values [RecordsSchema Row Written RowChanged ExpectAbsent FieldDecl TableDecl])
(import doeff_records.effects [PutRow ListRows AppendEvent ReadRow RowWrite])
(import doeff_records.admission [canonical-json])
(import doeff_records.laws [LAW-SCHEMA MAKER])
(import tests.interpreters [LawSetup])


(defn refuses? [thunk exception]
  (try (thunk) False (except [exception] True)))


(deftest test-values-are-deeply-frozen-at-construction
  (setv source {"id" "p1" "tags" ["a" "b"] "meta" {"n" 1}}
        row (Row #("p1") source 1))
  (assert (isinstance row.value FrozenMap))
  (assert (isinstance (get row.value "meta") FrozenMap))
  (assert (= (get row.value "tags") #("a" "b")))
  (assert (= row.value {"id" "p1" "tags" #("a" "b") "meta" {"n" 1}}) "凍らせた写像は同じ組の dict と等しい")
  (.append (get source "tags") "c")
  (setv (get source "id") "changed")
  (assert (= (get row.value "id") "p1") "元の dict を書き換えても行は変わらない")
  (assert (= (get row.value "tags") #("a" "b")))
  (assert (refuses? (fn [] (setv (get row.value "id") "x")) TypeError) "行の値に書けない")
  (assert (isinstance (hash row) int) "凍らせた行は hash できる")
  (for [built [(Written 1 {"a" 1}) (RowChanged "parts" #("p1") 1 {"a" 1} 1 1000) (PutRow "parts" #("p1") {"a" 1} (ExpectAbsent))
               (RowWrite "parts" #("p1") {"a" 1} (ExpectAbsent))
               (ListRows "parts" :where {"color" "red"})]]
    (assert (isinstance (or (getattr built "value" None) (getattr built "where" None)) FrozenMap) built))
  (setv event (AppendEvent "journal" "k1" {"list" [1 {"x" 2}]}))
  (assert (= event.body {"list" #(1 {"x" 2})}))
  (assert (isinstance (get (get event.body "list") 1) FrozenMap))
  (assert (= (canonical-json event.body) "{\"list\":[1,{\"x\":2}]}") "JSON の境界では dict / list へ戻して綴る")
  (assert (= (thaw-json row.value) {"id" "p1" "tags" ["a" "b"] "meta" {"n" 1}})))


(deftest test-schema-and-field-declarations-are-frozen
  (setv decl (LAW-SCHEMA.table "parts"))
  (assert (isinstance LAW-SCHEMA.tables FrozenMap))
  (assert (isinstance LAW-SCHEMA.streams FrozenMap))
  (assert (decl.declares "note"))
  (assert (not (decl.declares "size")))
  (assert (= (decl.field-names) #("id" "label" "color" "state" "note" "grant")))
  (assert (refuses? (fn [] (setv (get LAW-SCHEMA.tables "other") decl)) TypeError) "宣言の写像に書けない")
  (assert (refuses? (fn [] (RecordsSchema :tables {"parts" (LAW-SCHEMA.table "tickets")})) ValueError) "名の食い違い"))


(deftest test-the-field-index-answers-like-scanning-every-field
  ;; #2670 根 E: 表の宣言は欄の名の索引を宣言 1 つに 1 度だけ作り、declares はそれで答える(書きの判断が行ごと・欄ごとに欄の tuple を
  ;; 全部なめ直さない)。失敗ケース = 索引の答えが欄の tuple を全部なめた答えと違う(宣言の内・外のどの名でも)・作り直した宣言
  ;; (dataclasses.replace)や pickle と copy から戻した宣言が古い索引を引く。
  (for [decl (.values LAW-SCHEMA.tables)]
    (for [name (+ (decl.field-names) #("undeclared-x"))]
      (setv scanned (next (gfor f decl.fields :if (= f.name name) f) None))
      (assert (= (decl.declares name) (is-not scanned None)) #(decl.name name))))
  ;; 作り直した宣言は新しい欄で引き、元の宣言は元の欄のまま。
  (setv parts (LAW-SCHEMA.table "parts")
        grown (dataclasses.replace parts :fields (+ parts.fields #((FieldDecl :name "extra")))))
  (assert (grown.declares "extra") grown)
  (assert (not (parts.declares "extra")) parts)
  ;; pickle・copy から戻した宣言も同じに引き、索引を持つ前の版で pickle した宣言(索引の無い中身)も引ける。索引は等しさに入らない。
  (for [back [(pickle.loads (pickle.dumps parts)) (copy.deepcopy parts) (copy.copy parts)]]
    (assert (and (back.declares "color") (not (back.declares "extra"))) back)
    (assert (= back parts) back))
  (setv older (.__new__ TableDecl TableDecl))
  (.__setstate__ older (dfor [k v] (.items (vars parts)) :if (!= k "_by_name") k v))
  (assert (and (older.declares "color") (not (older.declares "extra"))) older)
  (assert (= older parts) older))


(deftest test-a-read-row-cannot-change-the-stored-row
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (<- written (harness.as-writer MAKER (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))))
  (<- first (harness.as-writer MAKER (ReadRow "parts" #("p1"))))
  (assert (refuses? (fn [] (setv (get first.value "label") "tampered")) TypeError))
  (assert (refuses? (fn [] (setv (get written.value "label") "tampered")) TypeError))
  (<- again (harness.as-writer MAKER (ReadRow "parts" #("p1"))))
  (assert (= (get again.value "label") "a")))


(deftest test-a-row-is-shared-by-deepcopy-and-a-row-with-a-non-text-key-is-copied
  ;; 行の値は作る時に深く凍り、鍵が文字列と整数だけなら行は中まで変えられない — 置き場を丸ごと写す使い手は行を作り直さない(#2670)。
  ;; 失敗ケース: 鍵に書き換えられる値を持つ行まで共有すると、写しの鍵を書き換えた時に元へ届く — そういう行は鍵を深く写す。
  (setv row (Row #("a" 1) {"id" "a" "n" [1 2]} 3))
  (assert (is (copy.deepcopy row) row))
  (assert (is (. (copy.deepcopy {"r" row}) ["r"]) row))
  (setv inner ["k"]
        odd (Row #(inner) {"id" "x"} 1)
        copied (copy.deepcopy odd))
  (assert (is-not copied odd))
  (assert (= copied odd))
  (assert (is-not (get copied.key 0) inner))
  (assert (is copied.value odd.value)))
