;;; 乱数の fake の答え手 seeded-random-handler(agora-redesign #1544)— RandomBytes に、種とこの run の中の呼びの順で決まった byte で
;;; 答える。模擬と検で、起動ごとの名乗りを毎回同じ値にし、os の乱数に触れないため。
;;;
;;; 値の作り方: n 回目(0 始まり)の呼びは sha256("<種>:<n>:<塊>") の digest を塊 0・1・… と並べた頭の count byte。同じ種と同じ呼びの順なら
;;; 同じ値、呼びごとに違う値になる。呼びの数えはこの run の中の状態(session の値)で、別の run とは共有しない。外側に state の handler が要る。
(require doeff-hy.macros [defhandler defk var val])
(import hashlib)
(import doeff_core_effects.random_effects [RandomBytes])


(defk seeded-bytes [seed call count]
  {:pre [(: seed int) (: call int) (>= call 0) (: count int) (>= count 0)]
   :post [(: % bytes) (= (len %) count)]
   :tags {:context "random" :role "foundation"}}
  "種 seed の call 回目の呼びの count 個の byte(頭の註の作り方)。"
  (val blocks (// (+ count 31) 32))
  (val digests (lfor block (range blocks) (.digest (hashlib.sha256 (.encode (.format "{}:{}:{}" seed call block))))))
  (cut (.join b"" digests) count))


(defhandler seeded-random-handler [#^ int seed]
  "RandomBytes に、種 seed とこの run の中の呼びの順で決まった count 個の byte で答える(os の乱数に触れない)。"
  ;; 引数に残す理由: 種は検と模擬ごとに決める台本の値で、Ask で読む設定ではない。
  (session var calls 0)
  (RandomBytes [count]
    (val answer (! (seeded-bytes seed calls count)))
    (:= calls (+ calls 1))
    (resume answer)))
