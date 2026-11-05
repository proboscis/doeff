;;; Random effect -- asks for a fixed number of random bytes. A foundation vocabulary with no business terms.
;;;
;;;   RandomBytes  asks for count random bytes. Answer = bytes of length count.
;;;
;;; Why: if per-startup identifiers (token prefixes, requestId collision avoidance) are made directly with os.urandom, simulations cannot replace them with fixed values, and tests
;;; reach real randomness. The program does not know where randomness comes from and just asks through this effect. Converting to hex or other spellings is up to the asker.
;;;
;;; Handlers: os-random-handler (os_random.hy -- real, os.urandom) and seeded-random-handler (seeded_random.hy -- values determined by seed and call order).
(require doeff-hy.macros [defeffect])


(defeffect RandomBytes
  "Ask for count random bytes (see header note). Answer = bytes of length count."
  {:fields [(: count int)]
   :answer bytes
   :tags {:context "random" :role "foundation"}})
