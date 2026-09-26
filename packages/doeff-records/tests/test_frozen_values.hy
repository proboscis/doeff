;; 行の値・書きの差分・出来事の本文は、作った後に変えられない(凍らせた写像)— 反例: 呼び手が答えの dict を書き換えると
;; 置き場の行まで変わる・effect を作った後に元の dict を書き換えると撃つ書きが変わる。
(require doeff-hy.macros [deftest <-])
(import doeff_hy.frozen [FrozenMap thaw-json])
(import doeff_records.values [RecordsSchema Row Written RowChanged ExpectAbsent UndeclaredField])
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
  (assert (= (decl.writers-of "color") #("maker" "painter")))
  (assert (decl.declares "note"))
  (assert (not (decl.declares "size")))
  (assert (= (decl.field-names) #("id" "label" "color" "state" "note" "grant")))
  (assert (refuses? (fn [] (decl.writers-of "size")) UndeclaredField))
  (assert (refuses? (fn [] (setv (get LAW-SCHEMA.tables "other") decl)) TypeError) "宣言の写像に書けない")
  (assert (refuses? (fn [] (RecordsSchema :tables {"parts" (LAW-SCHEMA.table "tickets")})) ValueError) "名の食い違い"))


(deftest test-a-read-row-cannot-change-the-stored-row
  {:interpreters ["memory" "pg"]}
  (<- harness (LawSetup))
  (<- written (harness.as-writer MAKER (PutRow "parts" #("p1") {"label" "a"} (ExpectAbsent))))
  (<- first (harness.as-writer MAKER (ReadRow "parts" #("p1"))))
  (assert (refuses? (fn [] (setv (get first.value "label") "tampered")) TypeError))
  (assert (refuses? (fn [] (setv (get written.value "label") "tampered")) TypeError))
  (<- again (harness.as-writer MAKER (ReadRow "parts" #("p1"))))
  (assert (= (get again.value "label") "a")))
