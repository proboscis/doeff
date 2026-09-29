;;; 乱数の検(agora-redesign #1544)。契約(本物 os-random-handler と fake seeded-random-handler が同じ deftest を通る)と、fake だけの性質。
;;; 解釈器の組み立ては random_contract_handlers.hy。
;;;
;;;   契約  RandomBytes の答えは長さ count の bytes で、続けた 2 回の呼びは違う値(本物は確率 2^-128 で一致しうるが無視できる)
;;;   fake  同じ種・同じ呼びの順なら run をまたいで同じ値、違う種なら違う値(模擬の名乗りが毎回同じになる)
(require doeff-hy.macros [deftest defk <-])
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.random_effects [RandomBytes])
(import doeff_core_effects.seeded_random [seeded-random-handler])


(deftest test-a-random-bytes-answer-the-asked-length-and-differ-per-call
  {:interpreters ["os-random" "seeded-random"]}
  (<- first bytes (RandomBytes 16))
  (<- second bytes (RandomBytes 16))
  (<- empty bytes (RandomBytes 0))
  (<- long bytes (RandomBytes 100))
  (assert (= (len first) 16) first)
  (assert (!= first second) (.format "続けた 2 回が同じ値 {!r}" first))
  (assert (= empty b"") empty)
  (assert (= (len long) 100) long))


(defk first-draw-of [seed]
  {:pre [(: seed int)] :post [(: % bytes)] :tags {:context "random-test" :role "foundation"}}
  "種 seed の fake の答え手で新しい run を回し、1 回目の答えを得るため(run をまたいだ再現を見る)。"
  (run (with_handlers [(state) (seeded-random-handler seed)] (RandomBytes 4))))


(deftest test-the-seeded-random-repeats-per-seed-and-order
  (<- same-a bytes (first-draw-of 7))
  (<- same-b bytes (first-draw-of 7))
  (<- other bytes (first-draw-of 8))
  (assert (= same-a same-b) "同じ種の 1 回目が run ごとに違う")
  (assert (!= same-a other) "違う種の 1 回目が同じ"))
