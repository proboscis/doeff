;;; The real answerer for randomness, os-random-handler -- answers RandomBytes with the OS randomness (os.urandom).
;;; This module is the only one that touches os.urandom (the fake seeded_random.hy does not).
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "random" :role "foundation"})
(import os)
(import doeff_core_effects._random_effects [RandomBytes])


(defhandler os-random-handler
  "Answer RandomBytes with count bytes from os.urandom."
  (RandomBytes [count]
    (resume (os.urandom count))))
