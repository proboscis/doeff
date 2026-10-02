;; 書き手の名は呼び手の X-Records-Writer の名乗りだけで決まる(writer-of — 無ければ ANONYMOUS・#3008)。
(require doeff-hy.macros [deftest])
(import doeff [run])
(import doeff_records.principals [ANONYMOUS Principal writer-of])


(deftest test-a-declared-writer-name-is-the-writer
  (assert (= (run (writer-of "painter")) (Principal "painter")))
  (assert (= (run (writer-of "  painter ")) (Principal "painter"))))


(deftest test-no-declared-name-is-anonymous
  (assert (= (run (writer-of None)) (Principal ANONYMOUS)))
  (assert (= (run (writer-of "  ")) (Principal ANONYMOUS))))
