;;; 不在と失敗の effect と境目の handler(ADR-DOE-CORE-EFFECTS-003 段 1 — doeff_core_effects.effects の Absent / Raise と
;;; doeff_core_effects.outcomes の maybe・result・on-raise・absent-as)。
;;;
;;;   * Absent は説明の文だけ・Raise は理由を持つ・互いに継承しない・Raise の理由に Python の例外は置けない
;;;   * maybe は Absent を Nothing に、result は Raise を Err に畳み、続きを再開しない(後ろの行は走らない)
;;;   * 入れ子の順で Result<Option> と Option<Result> が変わる
;;;   * on-raise は合う型の Raise だけを写し、合わない Raise は外へ渡し、Python の例外は受けない
;;;   * absent-as は奥の不在で既定値のスコープの終わりを返す
;;;   * 受け手の無い Absent / Raise は未処理の effect として止まる(黙って Nothing にしない)

(require doeff-hy.macros [deftest defk <- val on-raise absent-as])

(import dataclasses [dataclass fields])
(import hy)
(import pytest)
(import doeff [run])
(import doeff.result [Nothing Some])
(import doeff_vm [Err Ok UnhandledEffect])
(import doeff_core_effects.effects [Absent Raise Resumption resumption-of Ask])
(import doeff_core_effects.outcomes [maybe result absent-as :as absent-as-fn RaiseCase])


(defclass [(dataclass :frozen True)] Conflict []
  "版の負け(検のための失敗の理由)。"
  #^ str detail)

(defclass [(dataclass :frozen True)] Unreachable []
  "置き場に届かない(検のための失敗の理由)。"
  #^ str detail)


(defk absent-then-note [seen]
  {:pre [(: seen list)] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "Absent を出し、その後ろで seen に印を足す — 受け手が続きを捨てれば印は残らない。"
  (<- (Absent "行が無い"))
  (.append seen "続いた")
  1)

(defk raise-then-note [reason seen]
  {:pre [(: reason (| Conflict Unreachable)) (: seen list)] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "Raise(reason) を出し、その後ろで seen に印を足す。"
  (<- (Raise reason))
  (.append seen "続いた")
  1)

(defk succeeds []
  {:pre [] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "成功する本文。"
  7)

(defk raises-python []
  {:pre [] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "Python の例外を上げる本文(実装の誤りの見本)。"
  (raise (ValueError "実装の誤り")))


(deftest test-absent-and-raise-are-distinct
  (assert (= (lfor f (fields Absent) f.name) ["why"]) "Absent は説明の文だけを持つ")
  (assert (= (lfor f (fields Raise) f.name) ["reason"]) "Raise は理由を持つ")
  (assert (not (issubclass Raise Absent)))
  (assert (not (issubclass Absent Raise)))
  (assert (is (resumption-of Raise) Resumption.NEVER))
  (assert (is (resumption-of Absent) Resumption.ABSENT-AS-ONLY))
  (assert (is (resumption-of Ask) Resumption.REQUIRED) "宣言しない effect は再開が要る")
  (with [(pytest.raises TypeError :match "Python の例外")]
    (Raise (ValueError "x")))
  (with [(pytest.raises TypeError)]
    (Absent "")))


(deftest test-maybe-folds-absent-into-nothing-and-drops-the-rest
  (val seen [])
  (<- folded (maybe (absent-then-note seen)))
  (assert (is folded Nothing) folded)
  (assert (= seen []) "maybe は続きを再開しない")
  (<- kept (maybe (succeeds)))
  (assert (= kept (Some 7)) kept))


(deftest test-result-folds-raise-into-err-and-drops-the-rest
  (val seen [])
  (<- folded (result (raise-then-note (Conflict "負けた") seen)))
  (assert (= (repr folded) "Err(Conflict(detail='負けた'))") folded)
  (assert (= seen []) "result は続きを再開しない")
  (<- kept (result (succeeds)))
  (assert (= (repr kept) "Ok(7)") kept))


(deftest test-nesting-order-chooses-the-shape
  ;; (result (maybe b)) : Result[Option[T]]、(maybe (result b)) : Option[Result[T]](R2)
  (val lost (Unreachable "届かない"))
  (<- r-ok (result (maybe (succeeds))))
  (<- r-none (result (maybe (absent-then-note []))))
  (<- r-err (result (maybe (raise-then-note lost []))))
  ;; Ok / Err は値で比べられない(Rust の型)ので形を repr で比べる
  (assert (= (repr #(r-ok r-none r-err)) "(Ok(Some(7)), Ok(Nothing), Err(Unreachable(detail='届かない')))")
          #(r-ok r-none r-err))
  (<- m-ok (maybe (result (succeeds))))
  (<- m-err (maybe (result (raise-then-note lost []))))
  (<- m-none (maybe (result (absent-then-note []))))
  (assert (= (repr #(m-ok m-err m-none)) "(Some(Ok(7)), Some(Err(Unreachable(detail='届かない'))), Nothing)")
          #(m-ok m-err m-none)))


(deftest test-unhandled-absent-and-raise-stop-loudly
  ;; 受け手の無い Absent / Raise は未処理の effect として止まる(runner は境目の handler を既定で置かない)
  (with [(pytest.raises UnhandledEffect)]
    (run (absent-then-note [])))
  (with [(pytest.raises UnhandledEffect)]
    (run (raise-then-note (Conflict "c") []))))


(defk recovers [reason]
  {:pre [(: reason (| Conflict Unreachable))] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "Conflict だけを業務の答えへ写す(Unreachable は外へ渡る)。"
  (<- answer (on-raise (raise-then-note reason [])
               (Conflict d) (+ "conflict: " d)))
  (str answer))


(deftest test-on-raise-maps-only-matching-reasons
  (<- mapped (recovers (Conflict "版")))
  (assert (= mapped "conflict: 版") mapped)
  (<- passed (result (recovers (Unreachable "網"))))
  (assert (= (repr passed) "Err(Unreachable(detail='網'))") "合わない Raise は外の result へ渡る"))


(defk guarded-recovery [reason]
  {:pre [(: reason Conflict)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "番の外れた受けは次の受けへ進む(形の合わない Raise は外へ渡る)。"
  (<- answer (on-raise (raise-then-note reason [])
               (Conflict d) :if (.startswith d "版") (+ "版: " d)
               (| (Unreachable d) (Conflict d)) (+ "ほか: " d)))
  answer)


(deftest test-on-raise-guards-and-or-patterns
  (<- first (guarded-recovery (Conflict "版の負け")))
  (<- second (guarded-recovery (Conflict "鍵")))
  (assert (= #(first second) #("版: 版の負け" "ほか: 鍵")) #(first second)))


(defk python-error-through-on-raise []
  {:pre [] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "Python の例外を上げる本文を on-raise で包む — 例外は受けない。"
  (<- answer (on-raise (raises-python) (Conflict d) d))
  answer)


(deftest test-on-raise-does-not-catch-python-exceptions
  (with [(pytest.raises ValueError :match "実装の誤り")]
    (run (python-error-through-on-raise)))
  ;; 例外の型を名指す受けは作れない(RaiseCase が断る — 何でも受ける形と Python の例外は受けない: R7)
  (for [bad [object Exception ValueError BaseException]]
    (with [(pytest.raises TypeError)]
      (RaiseCase #(bad) (fn [r] (Some r))))))


(defk expansion-refusal [source]
  {:pre [(: source str)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "judgment"}}
  "source を展開した時の誤りの文(通れば空の文字列)— 展開の時に断る macro の規則を検で見るため。"
  (try
    (hy.eval (hy.read-many source))
    (return "")
    (except [e hy.errors.HyMacroExpansionError]
      (return (str e)))))


(deftest test-on-raise-refuses-catch-all-patterns-at-expansion
  (for [pattern ["_" "reason" "(Exception)" "(object)" "(BaseException e)" "(| (Conflict d) _)"]]
    (assert (in "受けられない" (! (expansion-refusal (+ "(require doeff-hy.macros [on-raise]) (on-raise body " pattern " 0)"))))
            pattern))
  (assert (in "値の式" (! (expansion-refusal "(require doeff-hy.macros [on-raise]) (on-raise body (Conflict d) (! (f d)))")))
          "写し先は値の式(効果を使えない)"))


(defk defaults-when-absent [seen]
  {:pre [(: seen list)] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "奥の defk の不在を absent-as が既定値のスコープの終わりにする。"
  (<- n (absent-as 0 (absent-then-note seen)))
  n)


(deftest test-absent-as-ends-the-scope-with-the-default-for-deep-absence
  (val seen [])
  (<- n (defaults-when-absent seen))
  (assert (= n 0) n)
  (assert (= seen []) "奥の不在では続きを再開しない(奥の行に既定値を渡さない)")
  (<- kept (absent-as-fn 0 (succeeds)))
  (assert (= kept 7) kept))
