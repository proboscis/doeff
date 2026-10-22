;;; DOEFF175 の検体 — 別の module の同じ名の型(app.effects.Missing とは別の型。宣言した方だけが不在の型)。
(require doeff-hy.record [defrecord])


(defrecord Missing
  #^ str what)
