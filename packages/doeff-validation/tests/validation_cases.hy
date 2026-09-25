;;; test_validate_hy.py が使う Hy の検査の例(validate / check のマクロを defk の中で使う)。

(require doeff-hy.macros [defk <- validate check])
(import enum [Enum])
(import dataclasses [dataclass])
(import types [NoneType])
(import doeff-core-effects [Ask])


(defclass Reason [Enum]
  (setv KEY "key" PHASE "phase" SEATS "seats" KIND "kind"))


(defk count-seats [node]
  "テスト用の問い合わせ: node の席の数を env から読む(defk の呼び出しの引数の例)。"
  {:pre [(: node str)] :post [(: % int)]}
  (<- n (Ask (+ "seats-" node)))
  n)


(defk all-independent [key expected-key phase]
  "独立した check を並べ、平たい形と括弧の形を混ぜる。"
  {:pre [(: key str) (: expected-key str) (: phase str)] :post [(: % NoneType)]}
  (! (validate
       (check = key expected-key :reason Reason.KEY)
       (check (= phase "pending") :reason Reason.PHASE)
       (check in "turn" #("turn" "summarize") :reason Reason.KIND))))


(defk effectful-arguments [node]
  "(! …) の付いた引数は項目の中で実行する — 効果の式と defk の呼び出しの 2 つ。"
  {:pre [(: node str)] :post [(: % NoneType)]}
  (<- _ (validate
          (check = (! (Ask "seats-a")) 0 :reason Reason.SEATS)
          (check = (! (count-seats node)) 0 :reason Reason.SEATS))))


(defk failing-evaluation []
  "(! …) の式が例外で落ちても、その項目の失敗として集め、他の項目は走る。"
  {:pre [] :post [(: % NoneType)]}
  (! (validate
       (check = (! (Ask "missing")) 0 :reason Reason.SEATS)
       (check = 1 2 :reason Reason.KEY))))


(defk unmarked-program-argument []
  "印の無い引数は、Program の値でも実行せずにそのまま比べる。"
  {:pre [] :post [(: % NoneType)]}
  (setv effect (Ask "seats-a"))
  (! (validate
       (check is effect effect)
       (check = effect 3 :reason Reason.SEATS))))


(defk phase-checks [phase]
  "検査のまとまりの使い回し: helper が自分の validate を持つ。"
  {:pre [(: phase str)] :post [(: % NoneType)]}
  (! (validate
       (check = phase "pending" :reason Reason.PHASE)
       (check = 1 2 :reason Reason.KEY))))


(defk nested [phase]
  "外の validate に helper の Program を並べる — 中の失敗は 1 件として足される。"
  {:pre [(: phase str)] :post [(: % NoneType)]}
  (! (validate
       (phase-checks phase)
       (check = 5 6 :reason Reason.SEATS))))


(defclass [(dataclass :frozen True)] Box []
  "short-circuit の例で使う、大きさを持つ値。"
  #^ int size)


(defk short-circuit [x]
  "and の括弧の形は分解しない(短絡の意味を保つ)— x が None なら右側は評価しない。"
  {:pre [(: x (| Box None))] :post [(: % NoneType)]}
  (! (validate
       (check (and (is-not x None) (> x.size 0)) :reason Reason.SEATS))))


(defk bind-seat [key expected-key phase node]
  "契約の :pre に check を並べる — 型の検査は assert のまま、check は全部を評価して集める。"
  {:pre [(: key str) (: expected-key str) (: phase str) (: node str)
         (check = key expected-key :reason Reason.KEY)
         (check = phase "pending" :reason Reason.PHASE)
         (check > (! (count-seats node)) 0 :reason Reason.SEATS)]
   :post [(: % str)
          (check != % "" :reason Reason.KIND)]}
  (+ key "@" node))


(defk empty-result [x]
  "契約の :post の check が戻りの値(%)を見る。"
  {:pre [(: x str)]
   :post [(: % str) (check != % "" :reason Reason.KIND)]}
  "")


(defk legacy-contract [x]
  "check を使わない既存の契約は、今までどおり assert(最初の失敗で止まる)。"
  {:pre [(: x int) (> x 0)] :post [(: % int)]}
  x)
