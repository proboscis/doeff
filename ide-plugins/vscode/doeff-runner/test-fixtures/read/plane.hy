;;; 定義を読む面(agora-redesign #910)のテストの見本 — 入れ子でない定義を 1 枚ずつのカードにし、kind と :tags の軸で絞る。
(require doeff-hy.macros [defk deff defrecord deftest <- val])

(val LIMIT 3)


(defrecord Row
  "読んだ 1 行。"
  (#^ str key)
  (#^ str text))


(defk fetch-row [key]
  {:pre [(: key str)] :post [(: % Row)]
   :effects [ReadInput] :tags {:context "messaging" :role "program" :owner "input"}}
  "鍵の行を読むため。"
  (<- row Row (ReadInput key))
  row)


(deff row-text [row]
  {:pre [(: row Row)] :post [(: % str)] :tags {:context "messaging" :role "judgment"}}
  "行の文字を返すため。"
  row.text)


(defk shout [key]
  {:pre [(: key str)] :post [(: % str)]
   :effects [ReadInput] :tags {:context "screen" :role "judgment" :owner "screen"}}
  "行を大きな字で見せるため。"
  (<- row Row (fetch-row key))
  (.upper (row-text row)))


(deftest test-row-text-is-the-text
  (assert (= (row-text (Row :key "k" :text "t")) "t")))
