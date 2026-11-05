;;; Fixture declarations for DOEFF175 — effects whose answers defeffect declares split into success / absent / failure / value, plus an effect with no declaration.
;;; The fixture branches.hy imports and uses these from app.effects. This file is never run (read-only fixture).
(require doeff-hy.macros [defeffect])
(require doeff-hy.record [defrecord])


(defrecord Row
  #^ str value)

(defrecord Missing
  #^ str key)

(defrecord Unreachable
  #^ str detail)

(defrecord Conflict
  #^ str detail)

(defrecord Accepted
  #^ str id)

(defrecord Refused
  #^ str reason)

(defrecord Gone
  #^ str id)


(defeffect ReadRow
  "Read a row (absent = Missing, failure = Unreachable, value = Conflict)."
  {:fields [(: key str)]
   :answer (| Row Missing Unreachable Conflict)
   :absent [Missing]
   :failure [Unreachable]
   :value [Conflict]
   :tags {:context "sample" :role "intent"}})

(defeffect PostRow
  "Send a row (absent = Gone, failure = Refused). Missing is not declared."
  {:fields [(: id str)]
   :answer (| Accepted Refused Gone)
   :absent [Gone]
   :failure [Refused]
   :tags {:context "sample" :role "intent"}})

(defeffect PlainRead
  "An undeclared read (answer type is the same union as ReadRow — <- binds the answer as is)."
  {:fields [(: key str)]
   :answer (| Row Missing Unreachable)
   :tags {:context "sample" :role "intent"}})
