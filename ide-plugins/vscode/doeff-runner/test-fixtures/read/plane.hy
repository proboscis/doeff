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


(deff row-text [row]  ; defk にできない: 定義を読む面の検(src/test/read/model.test.ts)が種類 deff のカードの見本として読む定義 — 種類が deff であること自体が検の材料
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


(defk describe-row [row prefix suffix width]
  {:pre [(: row (| Row None)) (: prefix str) (: suffix str) (: width int)] :post [(: % (| str None))]
   :tags {:context "screen" :role "judgment"}}
  "行を枠つきの文字にするため。行が無ければ None。"
  (when (is row None)
    (return None))
  (+ prefix (.ljust (row-text row) width) suffix))
