;;; DOEFF175 の検体の宣言 — defeffect が答えを 成功・不在・失敗・値 に分けて宣言した effect と、宣言の無い effect。
;;; 検体 branches.hy が app.effects から import して使う。この file は実行しない(読むだけの検体)。
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
  "行を読む(不在 = Missing・失敗 = Unreachable・値 = Conflict)。"
  {:fields [(: key str)]
   :answer (| Row Missing Unreachable Conflict)
   :absent [Missing]
   :failure [Unreachable]
   :value [Conflict]
   :tags {:context "sample" :role "intent"}})

(defeffect PostRow
  "行を送る(不在 = Gone・失敗 = Refused)。Missing は宣言していない。"
  {:fields [(: id str)]
   :answer (| Accepted Refused Gone)
   :absent [Gone]
   :failure [Refused]
   :tags {:context "sample" :role "intent"}})

(defeffect PlainRead
  "宣言の無い読み(答えの型は ReadRow と同じ union — <- は答えをそのまま束ねる)。"
  {:fields [(: key str)]
   :answer (| Row Missing Unreachable)
   :tags {:context "sample" :role "intent"}})
