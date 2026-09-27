;;; 不在と失敗(ADR-DOE-CORE-EFFECTS-003)の展開の時の補助 — on-raise と absent-as の展開。
;;;
;;; 実行時の意味は doeff_core_effects.outcomes が持ち、ここは形だけを作る:
;;;
;;;   (on-raise body (Conflict d) (WriteConflict :detail d) …)       合う型の Raise だけを業務の答えへ写す(何でも受ける形は断る)
;;;   (absent-as 0 body)                                             Absent を既定値に畳む(既定値でスコープを終える)
;;;
;;; macros.hy は、展開の時に関数の中でこの module を import する(macros.hy を compile している間は deff の
;;; 展開が呼ぶ補助がまだ無いので、deff で書く補助は macros.hy の外に置く)。

(require doeff-hy.macros [deff])
(import hy)
(import hy.models [Expression Symbol Keyword Sequence Object])


;; on-raise のパターンに置けない型の名(何でも受ける形・Python の例外 — R7)。
(setv CATCH-ALL-NAMES #{"_" "object" "Exception" "BaseException"})


(deff head-name [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: form Object)] :post [(: % (| str None))]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "form の頭の symbol の名(式でなければ・頭が symbol でなければ None)— 形で分けるため。"
  (when (and (isinstance form Expression) (> (len form) 0) (isinstance (get form 0) Symbol))
    (str (get form 0))))


(deff source-of [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: form Object)] :post [(: % str)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "誤りの文に出す form の綴り。"
  (.lstrip (hy.repr form) "'"))


(deff performs-effect? [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: form Object)] :post [(: % bool)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "form が効果を出すか(`!`・`<-`・`yield` を含むか — quote の中は数えない)。値の式だけを置く所(`:absent` の失敗・
   on-raise の写し先)を検めるため。"
  (let [head (head-name form)]
    (match form
      (Expression) :if (in head #{"quote" "quasiquote"}) False
      (Expression) :if (in head #{"!" "<-" "yield"}) True
      (Sequence) (any (gfor child form (performs-effect? child)))
      _ False)))


;; ---------------------------------------------------------------------------
;; on-raise(R7)
;; ---------------------------------------------------------------------------

(deff pattern-types [pattern]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: pattern Object)] :post [(: % list)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "on-raise のパターン 1 つが名指す失敗の型の form の list。型のパターン `(型 …)` と、その or `(| (A …) (B …))` だけを受け、
   何でも受ける形(`_`・名前 1 つの capture・object・Exception・BaseException)は SyntaxError(R7)。"
  (let [head (head-name pattern)]
    (match pattern
      (Expression) :if (= head "|")
        (lfor alternative (cut pattern 1 None) :setv types (pattern-types alternative) t types t)
      (Expression) :if (and (is-not head None) (not-in head CATCH-ALL-NAMES))
        [(get pattern 0)]
      _ (raise (SyntaxError
                 (+ "on-raise: パターン " (source-of pattern) " は受けられない — 失敗の型を名指す (型 欄 …) の形だけを"
                    "書く(何でも受ける形・Python の例外は受けない: ADR-DOE-CORE-EFFECTS-003 R7)"))))))


(deff on-raise-form [program clauses]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: program Object) (: clauses tuple)] :post [(: % Expression)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "(on-raise 本文 パターン [:if 番] 写し先 …) の展開(R7)。受けごとに、パターンの型と、パターンに合えば Some(写し先)・
   合わなければ Nothing を返す関数を RaiseCase にする。写し先と番は値の式(効果は使えない)。"
  (let [items (list clauses)
        reason (hy.gensym "reason")
        cases []]
    (when (not items)
      (raise (SyntaxError "on-raise: (on-raise 本文 パターン 写し先 …) — 受けが 1 つも無い")))
    (while items
      (let [pattern (.pop items 0)
            guard (when (and items (isinstance (get items 0) Keyword) (= (str (get items 0)) ":if"))
                    (.pop items 0)
                    (if items (.pop items 0) (raise (SyntaxError (+ "on-raise: :if の番が無い: " (source-of pattern))))))
            mapped (if items (.pop items 0) (raise (SyntaxError (+ "on-raise: パターン " (source-of pattern) " の写し先が無い"))))]
        (for [value [guard mapped]]
          (when (and (is-not value None) (performs-effect? value))
            (raise (SyntaxError (+ "on-raise: 番と写し先は値の式(!・<- は使えない — 効果の要る写しは本文の中で済ませる): "
                                   (source-of value))))))
        (.append cases
          `(_doeff-raise-case
             #(~@(pattern-types pattern))
             (fn [~reason]
               (match ~reason
                 ~pattern ~@(if (is guard None) [] [(Keyword "if") guard]) (_doeff-some ~mapped)
                 _ _doeff-nothing))))))
    `(do (import doeff_core_effects.outcomes [on-raise :as _doeff-on-raise RaiseCase :as _doeff-raise-case])
         (import doeff.result [Some :as _doeff-some Nothing :as _doeff-nothing])
         (_doeff-on-raise ~program ~@cases))))


;; ---------------------------------------------------------------------------
;; absent-as(R8)
;; ---------------------------------------------------------------------------

(deff absent-as-form [default program]  ; defk にできない: macro の展開の時に呼ぶ関数
  {:pre [(: default Object) (: program Object)] :post [(: % Expression)]
   :tags {:context "doeff-hy-outcomes" :role "foundation"}}
  "(absent-as 既定値 本文) の展開(R8)。中で出た Absent を再開せず、既定値でスコープを終える。"
  `(do (import doeff_core_effects.outcomes [absent-as :as _doeff-absent-as])
       (_doeff-absent-as ~default ~program)))
