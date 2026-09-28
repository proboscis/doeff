;;; 呼び出しの木(agora-redesign #910 U15)のテストの見本 — plane.hy の定義を file を跨いで呼び、重複(row-text を 2 度)・
;;; 循環(ping ↔ pong)・deftest を含む。
(require doeff-hy.macros [defk deftest <-])
(import pkg.plane [shout describe-row Row])


(defk show-both [key row]
  {:pre [(: key str) (: row Row)] :post [(: % str)]
   :tags {:context "screen" :role "program"}}
  "2 つの見せ方を並べるため(重複の見本)。"
  (<- a str (shout key))
  (<- b (| str None) (describe-row row "[" "]" 10))
  (+ a (or b "")))


(defk ping [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "screen" :role "judgment"}}
  "往復の見本(循環)。"
  (<- m int (pong n))
  m)


(defk pong [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "screen" :role "judgment"}}
  "往復の見本(循環)。"
  (<- m int (ping n))
  m)


(deftest test-show-both-joins
  (<- text str (show-both "k" (Row :key "k" :text "t")))
  (assert (isinstance text str)))
