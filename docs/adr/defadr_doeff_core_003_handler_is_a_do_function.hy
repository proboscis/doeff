;;; Executable ADR: handler は @handler を付けた def で、@handler がその def を @do 関数にする(handler の body はいつも @do 関数)。
;;; @handler の下の @do は deprecated。handler() は def と @do 関数でない物を、with_handlers と stacked_handlers は handler でない物を、
;;; 組む所で TypeError にして断り、印の無い物を中で handler にする fallback を持たない。
;;;
;;; 出自 = 持ち主(利用者)の決定 2026-10-20 JST。13:07〜13:12 に「@do 関数でない物を handler として受けない」と決め(0.7.0 は素の関数を
;;; handler にしていた)、16:26 に「@handler だけで def を書けるようにし、@handler @do は deprecated」と決めた(原文は :problem の fact)。
;;;
;;; 書くその場面の助言は doeff の外(defjevrule)が持つ(R6)。doeff の側には、作る所と組む所の断り(R1・R2)・入れ直しの口(R3)・
;;; deprecated の形(R4)・semgrep の 4 規則(R5)と、それらを確かめる deftest と pytest を置く。
;;;
;;; 戻し方: 16:26 の決定の前の形(handler は @do 関数だけを受ける)に戻すなら、doeff/program.py の _as_do_function を「@do 関数でなければ
;;; 断る」に戻し、.semgrep.yaml の doeff-handler-over-do-is-deprecated を「@do の無い def への @handler を断る」規則に戻して、repo の
;;; @handler の def に @do を足し直す。外すのは持ち主の言葉が在る時だけ。

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import pytest)
(import doeff [Ask Pass Pure Resume handler run with-handlers stacked-handlers])
(import doeff [do :as doeff-do])


;; defk にできない: defk は引数 (effect k) を handler と読んで断る。@handler が def を @do 関数にする事を見せる見本(R1)。
(defn [handler] passes-on [effect k]
  "R1 の見本: @handler だけを付けた def。受けた effect をそのまま外へ渡す。"
  (yield (Pass effect k)))

;; defk にできない: 上と同じ。yield せずに続けの Resume を返す反例の見本(R1 は走る所で断る)。
(defn unrun-answer [effect k]
  "R1 の反例の見本: 続けの Resume を yield せずに返す — 0.7.0 の素の関数の handler の形。"
  (Resume k 1))


(defadr ADR-DOE-CORE-003
  :title "handler は @handler を付けた def で、@handler がその def を @do 関数にする。@handler の下の @do は deprecated。handler() は def と @do 関数でない物を、with_handlers と stacked_handlers は handler でない物を、組む所で TypeError にして断る。VM が持つ handler の入れ直しは reinstaller が受け持つ"
  :status "accepted"
  :scope ["doeff/program.py (handler・with_handlers・stacked_handlers・_install_raw・_as_do_function)"
          "doeff/handler_utils.py (reinstaller)"
          ".semgrep.yaml (doeff-handler-over-do-is-deprecated・doeff-handler-yields-its-control・doeff-handler-is-not-a-lambda・doeff-handler-stack-takes-only-handlers)"
          "docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy"]
  :problem
    [(fact
       "持ち主の決定 2026-10-20 JST(公開の記事の審査の書 lt-6063P3QCSAX5TXD28ADKYQWYQZ への注釈・一字も変えない)。13:07「with-handlersが勝手に中で@handlerをつけるみたいなフォールバックは禁止したい。型の一貫性の観点から、@handlerで正しく制作されていないものがハンドラとして入ってきたら直ちにエラーを出したいし、linterでも禁止したい。@doがついていない関数をdoeffは基本的に受け付けるべきではない」。13:08「もしこれが徹底されていないのなら、apiが意図と違っているのでp0で修正してから記事を書く必要がある」。13:10「ハンドラハ@doがついたkleisliでなければならない。クレイスリだと伝わらないので@do関数とかでいい」。13:11「@do関数じゃなきゃだめ」。13:12「@handlerかつ@do以外の関数をwith-handlersに渡したら例外をだす必要がある」"
       :evidence "持ち主の内部の追跡に在る注釈 5 つ(2026-10-20 13:07〜13:12 JST)")
     (fact
       "持ち主の決定 2026-10-20 JST(審査の書 lt-0HY3ZZ0W9DKBYNHNWEGREYB2DF への注釈と書ごとの差し戻し・一字も変えない)。16:23「どういうこと？return yield xする理由はなに？？」。16:26「全部@handler@doは冗長なので、@handlerだけを許可して、@handler def...と書けるようにして、@handler@doはdeprecatedにしよう」"
       :evidence "注釈 lt-K73FTW9A1RFN2KY5TBAEGK0YWK・差し戻し lt-M9EAYJHW3AFYJN0BBPW79YV9VM")
     (fact
       "0.7.0 の handler() は、docstring で「a @do function, a plain function, a bound method, a functools.partial or a callable instance」を受けると書き、素の関数を、返した DoExpr を VM が走らせる受け手として handler にした。yield を含む素の def を handler にすると、走る時に「handler must return DoExpr: expected DoExpr or EffectBase, got: <class 'generator'>」の RuntimeError になる。with_handlers は印の無い物を中で handler() に通す fallback を持った。"
       :evidence "PyPI の doeff 0.7.0 の doeff/program.py の docstring と、0.7.0 で @handler だけを付けた yield の def を走らせた結果(2026-10-20)")
     (fact
       "try_handler・local_handler・listen_handler・Spawn・doeff-traverse は、GetHandlers(k) で読んだ内側の handler を handler() で入れ直していた。VM が持つのは生の受け手(@do 関数・defhandler の節の data・Rust の scheduler の SchedulerPrompt)で、@do 関数とは限らない。"
       :evidence "packages/doeff-core-effects/doeff_core_effects/handlers.py・packages/doeff-traverse/doeff_traverse/handlers.py の _program_handler(h)")]
  :context
    [(interpretation
       "handler の body を @do 関数の 1 つの形にすると、型と読み手の前提が揃う。body の中で yield した物は走り、yield の値はその結果。return した値は handle した塊の答えになる。だから続けは return (yield Resume(k, v)) と書き、Resume(k, v) を返すだけでは走らない。")
     (interpretation
       "@handler が def を @do 関数にするので、@do を重ねて書く事は何も足さない。@do 関数を handler() に渡す形(Kleisli としても呼ぶ @do 関数・partial・bound method)は、同じ物を指すので受け続ける。")
     (interpretation
       "入れ直しは、一度入った handler をもう一度入れるだけで、新しい handler を作る事ではない。新しく作る口(handler())と入れ直しの口(reinstaller)を分ければ、作る口だけを厳しくできる。")]
  :decision
    [(rule R1 "handler(f) は、f が def(lambda でない素の関数)なら @do 関数にして handler にする。f が @do 関数か、その partial か bound method なら、そのまま handler にする。既に印の付いた installer はそのまま返す。それ以外(lambda・素の method の bound method・callable の instance・builtin)は、作る所で TypeError「<名> is not a handler body」で断る。(effect, k) を取れない物と、どの effect にも当たらない注記は、前と同じく組む所で断る。yield の無い def が制御の式か effect を yield せずに返したら、走る時に TypeError で断り、return (yield …) の形を示す。")
     (rule R2 "with_handlers と stacked_handlers は、印 _doeff_is_handler_fn の付いた handler(@handler の def・defhandler・stacked_handlers の束)だけを取り、それ以外を組む所で TypeError「<名> is not a handler」で断る。中で handler() に通さない。")
     (rule R3 "GetHandlers(k) で読んだ handler の入れ直しは doeff.handler_utils.reinstaller が受け持ち、VM の WithHandler で直に入れる。新しい handler を作るのに使わない。")
     (rule R4 "@handler の下に @do を書く形は deprecated。走る時は受け続け(PyPI の 0.7.0 の利用者の code と、毎日 PyPI の版で走らせる examples/testing_without_mocks と publications/zenn-use-cases-v0 の例のため)、repo の中では semgrep が断る。その 2 つの dir は、@handler だけの形を持つ版が PyPI に出た時に書き替える。")
     (rule R5 ".semgrep.yaml の 4 規則が、試験の外で同じ形を書く所で断る: @handler の下の @do・@handler の def で yield しない制御の式の return・handler 類への lambda・同じ scope の @handler の無い def の handler の列への受け渡し。試験は断りを確かめるために断られる形をわざと作るので外す。")
     (rule R6 "書くその場面の助言は doeff の外(defjevrule)が持つ。doeff の側には R1〜R5 とその確かめだけを置く。")]
  :laws
    [(law handler-is-a-do-function
       :statement "handler(f) ⇒ (is_def(f) ∧ body = do(f)) ∨ is_do_function(f) ∨ marked(f) ∨ TypeError ∧ with_handlers(hs, p) ⇒ ∀h∈hs. marked(h) ∨ TypeError"
       :counterexamples
         [(counterexample "with_handlers([lambda effect, k: ...], prog) が中で handler() に通され、lambda が handler として走る")
          (counterexample "@handler の def が return Resume(k, v) と書き、Resume が答えの値になって続けが走らないまま塊が終わる")
          (counterexample "入れ直しのために handler() を緩め、新しく作る口でも callable の instance が通る")
          (counterexample "@handler を付けた def を、@do の無い Kleisli でない物として別の約束で走らせる")]
       :enforced-by ["docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy::test-adr-doe-core-003-handler-makes-a-def-a-do-function"
                     "docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy::test-adr-doe-core-003-handler-stacks-take-only-handlers"
                     "tests/test_handler_is_a_do_function.py"
                     "tests/test_with_handlers_refuses_unmarked_program_function.py"
                     "defsemgrep doeff-handler-over-do-is-deprecated・doeff-handler-yields-its-control・doeff-handler-is-not-a-lambda・doeff-handler-stack-takes-only-handlers"]
       :wiring "配線済み(2026-10-20)— この file の deftest と defsemgrep、tests の 2 file。書く場面の助言は defjevrule(R6)")]
  :enforcement
    [(deftest test-adr-doe-core-003-handler-makes-a-def-a-do-function
       ;; R1: def を @do 関数にして handler にし、@do 関数はそのまま受ける。lambda と builtin は作る所で、yield せずに返した Resume は走る所で断る。
       (assert (is (getattr passes-on "_doeff_is_handler_fn" False) True))
       (assert (is (handler passes-on) passes-on))
       (assert (= (run (passes-on (Pure "ok"))) "ok"))
       (val do-passes (handler (doeff-do (fn [effect k] (yield (Pass effect k))))))
       (assert (= (run (do-passes (Pure "ok"))) "ok"))
       (with [(pytest.raises TypeError :match "print is not a handler body")]
         (handler print))
       (with [(pytest.raises TypeError :match "is not a handler body")]
         (handler (fn [effect k] (yield (Pass effect k)))))
       (with [(pytest.raises TypeError :match "unrun_answer returned Resume")]
         (run ((handler unrun-answer) ((doeff-do (fn [] (yield (Ask "key")))))))))
     (deftest test-adr-doe-core-003-handler-stacks-take-only-handlers
       ;; R2: with_handlers と stacked_handlers は印の無い物(素の def・@handler の無い @do 関数・lambda)を断り、handler を取る。
       (val passes (doeff-do (fn [effect k] (yield (Pass effect k)))))
       (with [(pytest.raises TypeError :match "is not a handler")]
         (with-handlers [passes] (Pure "ok")))
       (with [(pytest.raises TypeError :match "is not a handler")]
         (with-handlers [(fn [effect k] (Pass effect k))] (Pure "ok")))
       (with [(pytest.raises TypeError :match "is not a handler")]
         (stacked-handlers passes))
       (assert (= (run (with-handlers [(handler passes)] (Pure "ok"))) "ok"))
       (assert (= (run ((stacked-handlers (handler passes)) (Pure "ok"))) "ok")))
     (defsemgrep handler-over-do-is-deprecated
       "doeff-handler-over-do-is-deprecated"
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_handlers.py"
         "source" "@handler\n@do\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}]
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_handlers.py"
         "source" "@handler\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}
        {"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_non_tail.py"
         "source" "@handler\n@do(non_tail=True)\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}
        {"relative-path" "examples/testing_without_mocks/example_pypi.py"
         "source" "@handler\n@do\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}])
     (defsemgrep handler-yields-its-control
       "doeff-handler-yields-its-control"
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_handlers.py"
         "source" "@handler\ndef answer(effect, k):\n    return Resume(k, 1)\n"}]
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_handlers.py"
         "source" "@handler\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}])
     (defsemgrep handler-is-not-a-lambda
       "doeff-handler-is-not-a-lambda"
       [{"relative-path" "examples/example_stack.py"
         "source" "program = with_handlers([lambda effect, k: Resume(k, 1)], body())\n"}
        {"relative-path" "examples/example_installer.py"
         "source" "program = handler(lambda effect, k: Resume(k, 1))(body())\n"}]
       [{"relative-path" "examples/example_stack.py"
         "source" "program = with_handlers([answer], body())\n"}])
     (defsemgrep handler-stack-takes-only-handlers
       "doeff-handler-stack-takes-only-handlers"
       [{"relative-path" "examples/example_stack.py"
         "source" "def answer(effect, k):\n    return (yield Resume(k, 1))\n\nprogram = with_handlers([answer], body())\n"}
        {"relative-path" "examples/example_bundle.py"
         "source" "def answer(effect, k):\n    return (yield Resume(k, 1))\n\nbundle = stacked_handlers(answer)\n"}]
       [{"relative-path" "examples/example_stack.py"
         "source" "@handler\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n\nprogram = with_handlers([answer], body())\n"}
        {"relative-path" "examples/example_installer.py"
         "source" "def answer(effect, k):\n    return (yield Resume(k, 1))\n\nprogram = with_handlers([handler(answer)], body())\n"}])]
  :plans ["docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy"
          "doeff/program.py (_is_do_function・_as_do_function・_yields_its_control・_refuse_unmarked — handler・with_handlers・stacked_handlers)"
          "doeff/handler_utils.py (reinstaller)"
          "packages/doeff-core-effects/doeff_core_effects/handlers.py・packages/doeff-traverse/doeff_traverse/handlers.py (入れ直しを reinstaller に)"
          "tests/test_handler_is_a_do_function.py・tests/test_with_handlers_refuses_unmarked_program_function.py"
          ".semgrep.yaml (4 規則)"])
