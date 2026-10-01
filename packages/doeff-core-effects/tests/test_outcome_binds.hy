;;; 答えの宣言と <- の変換(ADR-DOE-CORE-EFFECTS-003 段 2 — defeffect の :absent / :failure / :value と
;;; doeff_core_effects.outcomes.open-bind)。
;;;
;;;   * 宣言を持つ effect の <- は、成功と値を束ね、不在を Absent に、失敗を Raise(答え) に変えて呼び手のスコープで出す
;;;   * 同じ束ねの行は、呼び手が置いた境目の handler で意味を変えない(x はいつも成功の型)
;;;   * 宣言の無い effect の <- は今までと同じ物を yield し、同じ答えを束ねる(Missing も None も Nothing もそのまま)
;;;   * <- の :absent <失敗> は、その束ねの中で出た不在(直にも奥にも)を Raise(失敗) にし、失敗は不在の時にだけ作る
;;;   * <- は Result / Maybe の値を開く(Err の中の Python の例外は例外のまま)
;;;   * absent-as は字面の中の <- の不在だけを既定値で再開し、奥の不在と直に書いた Absent では既定値でスコープを終える
;;;   * Absent / Raise を再開する handler は誤りになる(黙って続かない)

(require doeff-hy.macros [deftest defk deff defeffect defhandler <- val do! on-raise absent-as])

(import dataclasses [dataclass])
(import hy)
(import pytest)
(import doeff [run EffectBase DoExpr K])
(import doeff.program [Resume Pass])
(import doeff.result [Nothing Some])
(import doeff_vm [Call Err Ok WithHandler])
(import doeff_core_effects.effects [Absent Raise])
(import doeff_core_effects.outcomes [maybe result open-bind Outcomes])
(import doeff_core_effects [outcomes])
(import unittest [mock])


(defclass [(dataclass :frozen True)] Row []
  "読めた行(検のための成功の答え)。"
  #^ str value)

(defclass [(dataclass :frozen True)] Missing []
  "行が無い(検のための不在の答え)。"
  #^ str key)

(defclass [(dataclass :frozen True)] Unreachable []
  "置き場に届かない(検のための失敗の答え)。"
  #^ str detail)

(defclass [(dataclass :frozen True)] Stale []
  "版の負け(業務で普通に扱う答え — :value)。"
  #^ int version)

(defclass [(dataclass :frozen True)] Conflict []
  "呼び手が :absent で投げる失敗。"
  #^ str detail)


(defeffect ReadRow
  "答えを 成功・不在・失敗・値 に分けて宣言した読み。"
  {:fields [(: key str)]
   :answer (| Row Missing Unreachable Stale)
   :absent [Missing]
   :failure [Unreachable]
   :value [Stale]
   :tags {:context "outcomes-test" :role "intent"}})

(defeffect PlainRead
  "宣言の無い読み(答えの型は同じ union)。"
  {:fields [(: key str)]
   :answer (| Row Missing Unreachable None)
   :tags {:context "outcomes-test" :role "intent"}})


(val TABLE {"a" (Row "A") "down" (Unreachable "網が落ちた") "old" (Stale 3) "none" None})


(defhandler table-rows
  "表 TABLE から ReadRow と PlainRead に値で答える土台の handler(不在も失敗も答えの値)。"
  {:tags {:context "outcomes-test" :role "foundation"}}
  (ReadRow [key] (resume (.get TABLE key (Missing key))))
  (PlainRead [key] (resume (.get TABLE key (Missing key)))))


(defk read-value [key]
  {:pre [(: key str)] :post [(: % (| str Stale))]
   :tags {:context "outcomes-test" :role "program"}}
  "宣言を持つ ReadRow を 1 つ束ねる — x は成功の型か値の型だけ。"
  (<- row (ReadRow key))
  (if (isinstance row Row) row.value row))


(defk read-value-bang [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "! も <- と同じく開く。"
  (+ "got " (. (! (ReadRow key)) value)))


(deftest test-declaration-splits-the-answer
  (val outcomes ReadRow.__doeff_outcomes__)
  (assert (isinstance outcomes Outcomes))
  (assert (= #(outcomes.success outcomes.absent outcomes.failure outcomes.value)
             #(#(Row) #(Missing) #(Unreachable) #(Stale)))
          outcomes)
  (assert (is (getattr PlainRead "__doeff_outcomes__" None) None) "宣言しなければ置かない"))


(defk expansion-refusal [source]
  {:pre [(: source str)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "judgment"}}
  "source を展開した時の誤りの文(通れば空の文字列)— 展開の時に断る macro の規則を検で見るため。"
  (try
    (hy.eval (hy.read-many source))
    (return "")
    (except [e hy.errors.HyMacroExpansionError]
      (return (str e)))))


(deftest test-declaration-refuses-types-outside-the-answer
  (val head "(require doeff-hy.macros [defeffect]) (defeffect Bad {:answer (| Row Missing) :tags {:context \"t\" :role \"intent\"} ")
  (assert (in "要素ではない" (! (expansion-refusal (+ head ":absent [Unreachable]})")))))
  (assert (in "両方にある" (! (expansion-refusal (+ head ":absent [Missing] :failure [Missing]})")))))
  (assert (in "list" (! (expansion-refusal (+ head ":absent Missing})"))))))


(deftest test-declared-bind-opens-the-answer
  (<- found (table-rows (maybe (read-value "a"))))
  (<- absent (table-rows (maybe (read-value "zz"))))
  (<- failed (table-rows (result (read-value "down"))))
  (<- stale (table-rows (read-value "old")))
  (assert (= found (Some "A")) found)
  (assert (is absent Nothing) absent)
  (assert (= (repr failed) "Err(Unreachable(detail='網が落ちた'))") failed)
  (assert (= stale (Stale 3)) "値と宣言した答えは束ねる(失敗にしない)")
  (<- banged (table-rows (maybe (read-value-bang "a"))))
  (<- banged-absent (table-rows (maybe (read-value-bang "zz"))))
  (assert (= #(banged banged-absent) #((Some "got A") Nothing)) #(banged banged-absent)))


(deftest test-a-bind-has-one-meaning-under-any-caller
  ;; 同じ read-value の行は、呼び手の maybe / result / on-raise のどれの下でも x = 成功の型(R3・law a-bind-has-one-meaning)
  (<- under-maybe (table-rows (maybe (read-value "a"))))
  (<- under-result (table-rows (result (read-value "a"))))
  (<- under-on-raise (table-rows (on-raise (read-value "a") (Unreachable d) d)))
  (assert (= #(under-maybe (repr under-result) under-on-raise) #((Some "A") "Ok('A')" "A"))
          #(under-maybe under-result under-on-raise)))


(defk read-plain [key]
  {:pre [(: key str)] :post [(: % (| Row Missing Unreachable (type None)))]
   :tags {:context "outcomes-test" :role "program"}}
  "宣言の無い PlainRead を束ねる — 答えをそのまま返す。"
  (<- row (PlainRead key))
  row)


(defclass [(dataclass :frozen True)] Echo [EffectBase]
  "宣言の無い(defclass の)effect — 渡した値そのものを答えにもらう。"
  #^ object value)

(defhandler echo-handler
  "Echo にその値で答える。"
  {:tags {:context "outcomes-test" :role "foundation"}}
  (Echo [value] (resume value)))

(defk bind-each [a b c d e]
  {:pre [(: a Missing) (: b (type None)) (: c (type Nothing)) (: d Err) (: e Unreachable)] :post [(: % tuple)]
   :tags {:context "outcomes-test" :role "program"}}
  "宣言の無い effect の答えの値(Missing・None・Nothing・Err・Unreachable)を 1 つずつ束ねる — 変換されずに同じ物が返る。"
  (<- ga (Echo a))
  (<- gb (Echo b))
  (<- gc (Echo c))
  (<- gd (Echo d))
  (<- ge (Echo e))
  #(ga gb gc gd ge))


(defk bang-in-assignment-target [box]
  {:pre [(: box dict)] :post [(: % dict)]
   :tags {:context "outcomes-test" :role "program"}}
  "! を代入の的の中に書く(`(setv (get (! e) 鍵) 値)`)— 束ねの形が文を持たない式であることを見るため(import の文を
   持つ形では Hy が的を組めずに、この file の compile が落ちる)。"
  (setv (get (! (Echo box)) "k") "v")
  box)


(deftest test-bang-is-an-expression-in-an-assignment-target
  (<- got (echo-handler (bang-in-assignment-target {})))
  (assert (= got {"k" "v"}) got))


(deftest test-undeclared-bind-is-unchanged
  ;; (c) 宣言の無い effect の <- は今までと同じ物を yield し、答えを変えずに束ねる
  (val effect (PlainRead "a"))
  (assert (is (open-bind effect) effect) "宣言の無い effect は同じ物を yield する")
  (val program (read-plain "a"))
  (assert (is (open-bind program) program) "Program も同じ物を yield する")
  (val answers #((Missing "zz") None Nothing (Err "e") (Unreachable "u")))
  (<- got (echo-handler (bind-each #* answers)))
  (assert (all (gfor #(bound given) (zip got answers :strict True) (is bound given))) "答えは同じ物のまま束ねる")
  ;; PlainRead の Missing / Unreachable / None も変換されない(外に Absent / Raise を出さない — 受け手が無くても止まらない)
  (<- plain-missing (table-rows (read-plain "zz")))
  (<- plain-down (table-rows (read-plain "down")))
  (<- plain-none (table-rows (read-plain "none")))
  (assert (= #(plain-missing plain-down plain-none) #((Missing "zz") (Unreachable "網が落ちた") None))
          #(plain-missing plain-down plain-none)))


(deftest test-yielding-defk-bind-skips-the-python-dispatch
  ;; #2449: 効果を出す defk の呼び(Call)を :absent なしで束ねると、open-bind は doeff-vm の中でその呼びそのものを
  ;; 返し、Python の振り分け(_opened)へ入らない。答えは振り分けの答えと同じ物。遅い形 = 束ねごとに
  ;; _open-bind → _opened → _settled を通り、呼びをそのまま返していた(預かり所の契約の例で 13.8k 回)。
  (val program (read-plain "a"))
  (assert (is (outcomes._open-bind program) program) "Python の振り分けの答えも呼びそのもの")
  (with [spy (mock.patch.object outcomes "_opened" :wraps outcomes._opened)]
    (assert (is (open-bind program) program) "束ねは呼びそのものを yield する")
    (assert (= spy.call-count 0) f"効果を出す defk の呼びは Python の振り分けへ入らない: {spy.call-count}")
    (val effect (PlainRead "a"))
    (assert (is (open-bind effect) effect) "ほかの物は今までどおり振り分けへ渡し、同じ物を yield する")
    (assert (= spy.call-count 1) spy.call-count))
  (<- got (table-rows (read-plain "a")))
  (assert (= got (Row "A")) got))


(defk judge-row [x]
  {:pre [(: x int)] :post [(: % bool)]
   :tags {:context "outcomes-test" :role "program"}}
  "効果を出さない判断(本体に yield が無い)— 画面の行ごとの判断の形(agora-redesign #844)。"
  (> x 3))

(defk count-judged [xs]
  {:pre [(: xs list)] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "判断を <- で束ねて数え、最後に ! でも束ねる。"
  (var n 0)
  (for [x xs]
    (<- ok (judge-row x))
    (when ok (:= n (+ n 1))))
  (if (! (judge-row 9)) n 0))

(defk judge-then-read [x]
  {:pre [(: x int)] :post [(: % (| Row Missing Unreachable (type None)))]
   :tags {:context "outcomes-test" :role "program"}}
  "効果を出さない判断の後に、効果を出す defk を束ねる。"
  (<- ok (judge-row x))
  (<- row (read-plain (if ok "a" "zz")))
  row)


(deftest test-effect-free-defk-bind-runs-in-place
  ;; #844: 効果を出さない defk を <- / ! で束ねると、その場で呼んで答えを束ね、VM へ yield しない。
  ;; 遅い形 = 束ねごとに Program を VM へ yield し、VM が関数を呼んで答えを send で返していた(1 回 数 µs)。
  (val steps ((. (count-judged [1 5 7]) function function) [1 5 7]))
  (with [stopped (pytest.raises StopIteration)]
    (next steps))
  (assert (= (. stopped value value) 2) "束ねは 1 度も yield せずに終わる")
  (assert (= (. (open-bind (judge-row 5)) value) True) "open-bind は答えの Pure を返す")
  ;; 効果を出す defk の束ねは今までどおり VM へ yield する(効果を出さない束ねの後でも)
  (val reads ((. (judge-then-read 5) function function) 5))
  (assert (isinstance (next reads) Call) "効果を出す defk の呼びは VM へ渡す")
  ;; VM で走らせた答えも同じ
  (<- judged (count-judged [1 5 7]))
  (assert (= judged 2) judged))


(deftest test-effect-free-defk-bind-raises-where-it-binds
  ;; 束ねる所で呼ぶので、契約の破れ(:pre)はその束ねの位置から上がる(VM を通した時と同じ位置)
  (with [(pytest.raises BaseException :match "judge-row")]
    (run (count-judged [1 "x"]))))


(defclass [(dataclass :frozen True)] Shaped [EffectBase]
  "欄の名が組み込みの名(type・len・list)の effect(束ねの式の衛生の検)。"
  #^ str type
  #^ int len
  #^ list list)

(defhandler shadowing-handler
  "引数の名が組み込みの名の腕と、<- で束ねる腕が同じ handler に在る — Python は type を handler の関数全体の局所の名と見る。"
  {:tags {:context "outcomes-test" :role "foundation"}}
  (Shaped [type len list] (resume #(type len list)))
  (Echo [value]
    (<- ok (judge-row value))
    (resume ok)))

(defk judge-shadowed [type len list]
  {:pre [(: type str) (: len int) (: list tuple)] :post [(: % tuple)]
   :tags {:context "outcomes-test" :role "program"}}
  "引数の名が組み込みの名の defk の中で束ねる。"
  (<- ok (judge-row len))
  #(type ok list))

(defk echo-and-shape []
  {:pre [] :post [(: % tuple)]
   :tags {:context "outcomes-test" :role "program"}}
  "Echo と Shaped を出す。"
  (<- judged (Echo 5))
  (<- shaped (Shaped "t" 2 [1]))
  #(judged shaped))


(deftest test-bind-does-not-read-the-callers-names
  ;; L562 の後始末: 束ねの式が組み込みの type を名で引いていたので、引数に type を持つ腕の在る handler
  ;; (agora-controllers の kanban-board-records の Relate)で他の腕の <- が UnboundLocalError で落ちた
  (<- got (shadowing-handler (echo-and-shape)))
  (assert (= got #(True #("t" 2 [1]))) got)
  (<- shadowed (judge-shadowed "t" 5 #(1)))
  (assert (= shadowed #("t" True #(1))) shadowed))


;; :absent の失敗を作った鍵を記す(:absent の失敗が不在の時にだけ作られることを見るため)。失敗は値の式(! / <- を書けない)なので、
;; 作る所は関数にせず :absent の式の中に書く — open-bind が不在の時にだけその式を評価する。
(val FAILURES-BUILT [])

(defk deep-read [key]
  {:pre [(: key str)] :post [(: % (| str Stale))]
   :tags {:context "outcomes-test" :role "program"}}
  "奥で不在を出す defk(read-value を呼ぶだけ)。"
  (<- v (read-value key))
  v)

(defk read-or-conflict [key]
  {:pre [(: key str)] :post [(: % (| str Stale))]
   :tags {:context "outcomes-test" :role "program"}}
  "(e) :absent で、この束ねの不在(奥の defk の中で出た物も)を Conflict の失敗として投げる。"
  (<- v (deep-read key) :absent (do (.append FAILURES-BUILT key) (Conflict (+ key " の行が無い"))))
  v)

(defk nothing-or-conflict []
  {:pre [] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "Maybe の値 Nothing を :absent つきで開く。"
  (<- v Nothing :absent (do (.append FAILURES-BUILT "nothing") (Conflict "nothing の行が無い")))
  v)


(deftest test-absent-option-maps-this-bind-to-a-failure
  (.clear FAILURES-BUILT)
  (<- found (table-rows (result (read-or-conflict "a"))))
  (assert (= (repr found) "Ok('A')") found)
  (assert (= FAILURES-BUILT []) "成功の時は失敗の値を作らない")
  (<- mapped (table-rows (result (read-or-conflict "zz"))))
  (assert (= (repr mapped) "Err(Conflict(detail='zz の行が無い'))") mapped)
  (<- down (table-rows (result (read-or-conflict "down"))))
  (assert (= (repr down) "Err(Unreachable(detail='網が落ちた'))") "失敗はそのまま(:absent は不在だけを写す)")
  (<- from-nothing (result (nothing-or-conflict)))
  (assert (= (repr from-nothing) "Err(Conflict(detail='nothing の行が無い'))") from-nothing)
  (assert (in "値の式" (! (expansion-refusal "(require doeff-hy.macros [<-]) (<- x (f) :absent (! (g)))"))))
  (assert (in "知らない鍵" (! (expansion-refusal "(require doeff-hy.macros [<-]) (<- x (f) :absnt 1)")))))


(defk open-value [value]
  {:pre [(: value (| Ok Err Some (type Nothing)))] :post [(: % int)]
   :tags {:context "outcomes-test" :role "program"}}
  "Result / Maybe の値を <- で開く。"
  (<- x value)
  (+ x 1))


(deftest test-bind-opens-result-and-option-values
  (<- from-ok (result (open-value (Ok 1))))
  (<- from-err (result (open-value (Err "理由"))))
  (<- from-some (maybe (open-value (Some 2))))
  (<- from-nothing (maybe (open-value Nothing)))
  (assert (= (repr #(from-ok from-err from-some from-nothing)) "(Ok(2), Err('理由'), Some(3), Nothing)")
          #(from-ok from-err from-some from-nothing))
  ;; Err の中の Python の例外(Try が畳んだ実装の誤り)は Raise にせず例外のまま上げる(R11)
  (with [(pytest.raises ValueError :match "実装")]
    (run (result (open-value (Err (ValueError "実装の誤り")))))))


(defk direct-default [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "字面の中の <- の不在は既定値で再開し、続きが走る。"
  (<- v (absent-as (Row "既定") (do! (<- row (ReadRow key)) (+ "続いた: " row.value))))
  v)

(defk deep-default [key]
  {:pre [(: key str)] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "呼んだ defk の奥の不在は再開せず、既定値でスコープを終える。"
  (<- v (absent-as "既定" (do! (<- got (read-value key)) (+ "続いた: " got))))
  v)

(defk written-absent-default []
  {:pre [] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "直に書いた (<- (Absent …)) は再開せず、既定値でスコープを終える。"
  (<- v (absent-as "既定" (do! (<- (Absent "書いた不在")) "続いた")))
  v)


(deftest test-absent-as-resumes-only-direct-binds
  (<- direct (table-rows (direct-default "zz")))
  (<- deep (table-rows (deep-default "zz")))
  (<- written (written-absent-default))
  (<- present (table-rows (direct-default "a")))
  (assert (= #(direct deep written present) #("続いた: 既定" "既定" "既定" "続いた: A"))
          #(direct deep written present)))


(deff resume-anything [effect k]  ; defk にできない: defk は引数 (effect k) を handler と読んで断り、defhandler は Raise / Absent の再開を断る — 規則に反する handler の反例は素の handler 関数でしか書けない
  {:pre [(: effect EffectBase) (: k K)] :post [(: % DoExpr)]
   :tags {:context "outcomes-test" :role "foundation"}}
  "Raise / Absent を再開してしまう handler(規則に反する反例)— 開く側がそれを誤りにすることを見るため。"
  (if (isinstance effect #(Raise Absent))
      (Resume k "続けてしまった")
      (Pass effect k)))


(defk raises-then-continues []
  {:pre [] :post [(: % str)]
   :tags {:context "outcomes-test" :role "program"}}
  "Raise を出す(再開されたら誤り)。"
  (<- (Raise (Conflict "c")))
  "続いた")


(deftest test-resuming-raise-or-absent-is-an-error
  (with [(pytest.raises RuntimeError :match "Raise が再開された")]
    (run (WithHandler resume-anything (raises-then-continues))))
  (with [(pytest.raises RuntimeError :match "Absent が再開された")]
    (run (WithHandler resume-anything (table-rows (read-value "zz"))))))
