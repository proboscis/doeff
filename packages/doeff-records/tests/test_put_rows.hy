;; PutRows の束を作る時の検め: 同じ表の同じ鍵が 2 度出る束・空の束・RowWrite でない要素は作る時に断る(撃つ前の組み立ての誤り)。
;; 束の書きの意味(全部か 0・変更の列の順・HTTP の口越しで同じ答え)は laws.law-put-rows-is-all-or-nothing を test_laws /
;; test_parity_memory_pg が memory・PostgreSQL・HTTP の口越しで回す。
(require doeff-hy.macros [deftest defk val <-])
(import collections.abc [Callable])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [ExpectAbsent ExpectAny ExpectVersion])
(import doeff_records.effects [PutRow PutRows RowWrite])


(defk refuses? [thunk exception]
  {:pre [(: thunk Callable) (: exception (| type tuple))] :post [(: % bool)]}
  "thunk を呼ぶと exception が上がるか(作る時の断りの検め)。"
  (try (thunk) False (except [exception] True)))


(deftest test-a-batch-with-the-same-row-twice-is-refused-at-construction
  (val first (RowWrite "parts" #("p1") {"label" "a"} (ExpectAbsent)))
  (val again (RowWrite "parts" #("p1") {"label" "b"} (ExpectAny)))
  (<- twice (refuses? (fn [] (PutRows #(first again))) ValueError))
  (assert twice "同じ表の同じ鍵が 2 度")
  ;; 同じ鍵でも表が違えば別の行・同じ表でも鍵が違えば別の行。
  (assert (= (len (. (PutRows #(first (RowWrite "tickets" #("p1") {} (ExpectAny)))) writes)) 2))
  (assert (= (len (. (PutRows #(first (RowWrite "parts" #("p1" "x") {} (ExpectAny)))) writes)) 2)))


(deftest test-a-batch-must-be-a-non-empty-tuple-of-row-writes
  (<- empty (refuses? (fn [] (PutRows #())) TypeError))
  (assert empty "空の束")
  (<- listed (refuses? (fn [] (PutRows [(RowWrite "parts" #("p1") {} (ExpectAny))])) TypeError))
  (assert listed "list の束(tuple だけ)")
  (<- effect-inside (refuses? (fn [] (PutRows #((PutRow "parts" #("p1") {} (ExpectAny))))) TypeError))
  (assert effect-inside "PutRow の effect を束に入れる"))


(deftest test-a-row-write-is-checked-like-a-put-row
  ;; RowWrite の欄の検めは PutRow と同じ(表の名・鍵・欄の名・期待)で、値は深く凍らせる。
  (val cases [#("Parts!" #("p1") {} (ExpectAny))
              #("parts" #() {} (ExpectAny))
              #("parts" #("p1") {"bad name" 1} (ExpectAny))
              #("parts" #("p1") {} "any")])
  (val verdicts [])
  (for [#(table key value expect) cases]
    (.append verdicts #((! (refuses? (fn [] (RowWrite table key value expect)) #(TypeError ValueError)))
                        (! (refuses? (fn [] (PutRow table key value expect)) #(TypeError ValueError))))))
  (assert (= verdicts (lfor _ cases #(True True))) (repr verdicts))
  (val write (RowWrite "parts" #("p1") {"meta" {"n" [1 2]}} (ExpectVersion 3)))
  (assert (isinstance write.value FrozenMap))
  (assert (= (get (get write.value "meta") "n") #(1 2))))
