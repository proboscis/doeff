;;; 乱数の本物の答え手 os-random-handler(agora-redesign #1544)— RandomBytes に、os の乱数(os.urandom)で答える。
;;; os.urandom に触るのはこの module だけ(fake の seeded_random.hy は触らない)。
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "random" :role "foundation"})
(import os)
(import doeff_core_effects.random_effects [RandomBytes])


(defhandler os-random-handler
  "RandomBytes に、os.urandom の count 個の byte で答える。"
  (RandomBytes [count]
    (resume (os.urandom count))))
