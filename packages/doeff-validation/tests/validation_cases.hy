;;; Hy validation examples used by test_validate_hy.py (using the validate / check macros inside defk).

(require doeff-hy.macros [defk <- validate check])
(import enum [Enum])
(import dataclasses [dataclass])
(import types [NoneType])
(import doeff-core-effects [Ask])


(defclass Reason [Enum]
  (setv KEY "key" PHASE "phase" SEATS "seats" KIND "kind"))


(defk count-seats [node]
  "Test query: read the number of seats of a node from env (an example of defk call arguments)."
  {:pre [(: node str)] :post [(: % int)]}
  (<- n (Ask (+ "seats-" node)))
  n)


(defk all-independent [key expected-key phase]
  "Line up independent checks, mixing the flat form and the parenthesized form."
  {:pre [(: key str) (: expected-key str) (: phase str)] :post [(: % NoneType)]}
  (! (validate
       (check = key expected-key :reason Reason.KEY)
       (check (= phase "pending") :reason Reason.PHASE)
       (check in "turn" #("turn" "summarize") :reason Reason.KIND))))


(defk effectful-arguments [node]
  "An argument marked with (! …) is executed inside the item — two kinds: an effect expression and a defk call."
  {:pre [(: node str)] :post [(: % NoneType)]}
  (<- _ (validate
          (check = (! (Ask "seats-a")) 0 :reason Reason.SEATS)
          (check = (! (count-seats node)) 0 :reason Reason.SEATS))))


(defk failing-evaluation []
  "Even if a (! …) expression fails with an exception, it is collected as that item's failure and the other items run."
  {:pre [] :post [(: % NoneType)]}
  (! (validate
       (check = (! (Ask "missing")) 0 :reason Reason.SEATS)
       (check = 1 2 :reason Reason.KEY))))


(defk unmarked-program-argument []
  "An argument without the mark is compared as-is, without executing it, even if its value is a Program."
  {:pre [] :post [(: % NoneType)]}
  (setv effect (Ask "seats-a"))
  (! (validate
       (check is effect effect)
       (check = effect 3 :reason Reason.SEATS))))


(defk phase-checks [phase]
  "Reusing a group of checks: the helper has its own validate."
  {:pre [(: phase str)] :post [(: % NoneType)]}
  (! (validate
       (check = phase "pending" :reason Reason.PHASE)
       (check = 1 2 :reason Reason.KEY))))


(defk nested [phase]
  "Line up the helper Program in the outer validate — a failure inside is added as one failure."
  {:pre [(: phase str)] :post [(: % NoneType)]}
  (! (validate
       (phase-checks phase)
       (check = 5 6 :reason Reason.SEATS))))


(defclass [(dataclass :frozen True)] Box []
  "A sized value used in the short-circuit example."
  #^ int size)


(defk short-circuit [x]
  "The parenthesized form of and is not decomposed (preserving short-circuit semantics) — if x is None the right side is not evaluated."
  {:pre [(: x (| Box None))] :post [(: % NoneType)]}
  (! (validate
       (check (and (is-not x None) (> x.size 0)) :reason Reason.SEATS))))


(defk bind-seat [key expected-key phase node]
  "Line up checks in the contract :pre — type checks stay as assert, while check evaluates everything and collects."
  {:pre [(: key str) (: expected-key str) (: phase str) (: node str)
         (check = key expected-key :reason Reason.KEY)
         (check = phase "pending" :reason Reason.PHASE)
         (check > (! (count-seats node)) 0 :reason Reason.SEATS)]
   :post [(: % str)
          (check != % "" :reason Reason.KIND)]}
  (+ key "@" node))


(defk empty-result [x]
  "The check in the contract :post looks at the return value (%)."
  {:pre [(: x str)]
   :post [(: % str) (check != % "" :reason Reason.KIND)]}
  "")


(defk legacy-contract [x]
  "An existing contract that does not use check stays assert as before (stops at the first failure)."
  {:pre [(: x int) (> x 0)] :post [(: % int)]}
  x)
