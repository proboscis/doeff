;;; Fixture for DOEFF175 — a type with the same name in a different module (distinct from app.effects.Missing; only the declared one is the absent type).
(require doeff-hy.record [defrecord])


(defrecord Missing
  #^ str what)
