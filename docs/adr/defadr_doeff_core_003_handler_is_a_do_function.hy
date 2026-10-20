;;; Executable ADR: handler は @handler を付けた @do 関数だけ。handler() は @do 関数でない物を、with_handlers と stacked_handlers は
;;; handler でない物を、組む所で TypeError にして断り、素の関数を中で handler にする fallback を持たない。
;;;
;;; 出自 = 持ち主(利用者)の決定 2026-10-20 13:07〜13:12 JST。公開の記事の審査の書への注釈で、0.7.0 が素の関数を handler として
;;; 受ける事を「API が意図と違う」と言い、記事より先に p0 で直すと決めた(原文は :problem の fact)。
;;;
;;; 書くその場面の助言は doeff の外(agent-commons の defjevrule)が持つ(R5)。doeff の側には、組む所の断り(R1・R2)・入れ直しの
;;; 口(R3)・semgrep の 3 規則(R4)と、それらを確かめる deftest と pytest を置く。
;;;
;;; 戻し方: この file と、doeff/program.py の _refuse_non_do・_refuse_unmarked・doeff/handler_utils.py の reinstaller を足した commit を
;;; revert し、`make enforcement-ledger` で台帳を作り直す。外すのは持ち主の言葉が在る時だけ。

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import pytest)
(import doeff [Pass Pure handler run with-handlers stacked-handlers])
(import doeff [do :as doeff-do])


(defadr ADR-DOE-CORE-003
  :title "handler は @handler を付けた @do 関数だけ。handler() は @do 関数でない物を、with_handlers と stacked_handlers は handler でない物を、組む所で TypeError にして断る。VM が持つ handler の入れ直しは reinstaller が受け持つ"
  :status "accepted"
  :scope ["doeff/program.py (handler・with_handlers・stacked_handlers・_install_raw)"
          "doeff/handler_utils.py (reinstaller)"
          ".semgrep.yaml (doeff-handler-requires-do・doeff-handler-is-not-a-lambda・doeff-handler-target-is-a-do-function)"
          "docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy"]
  :problem
    [(fact
       "持ち主の決定 2026-10-20 JST(公開の記事の審査の書 lt-6063P3QCSAX5TXD28ADKYQWYQZ への注釈・一字も変えない)。13:07「with-handlersが勝手に中で@handlerをつけるみたいなフォールバックは禁止したい。型の一貫性の観点から、@handlerで正しく制作されていないものがハンドラとして入ってきたら直ちにエラーを出したいし、linterでも禁止したい。@doがついていない関数をdoeffは基本的に受け付けるべきではない」。13:08「もしこれが徹底されていないのなら、apiが意図と違っているのでp0で修正してから記事を書く必要がある」。13:10「ハンドラハ@doがついたkleisliでなければならない。クレイスリだと伝わらないので@do関数とかでいい」。13:11「@do関数じゃなきゃだめ」。13:12「@handlerかつ@do以外の関数をwith-handlersに渡したら例外をだす必要がある」"
       :evidence "持ち主の内部の追跡に在る注釈 5 つ(2026-10-20 13:07〜13:12 JST)")
     (fact
       "0.7.0 の handler() は、docstring で「a @do function, a plain function, a bound method, a functools.partial or a callable instance」を受けると書き、素の関数を handler にした。with_handlers は「Raw effect dispatchers are normalized through handler」— 印の無い物を中で handler() に通す fallback を持った。"
       :evidence "PyPI の doeff 0.7.0 の doeff/program.py(handler・with_handlers の docstring)")
     (fact
       "直す前の main で、素の関数か印の無い @do 関数を handler に渡す所は、試験の外で約 30 か所(examples・publications・benchmarks・doeff/cli/demo.py・tools/readme_demo)、試験で約 40 か所だった。参考実装の handler(reader・state・writer など)は既に @do 関数を handler() に通していた。"
       :evidence "AST の走査(handler・with_handlers・stacked_handlers の引数と def の decorator)、2026-10-20")
     (fact
       "try_handler・local_handler・listen_handler・Spawn・doeff-traverse は、GetHandlers(k) で読んだ内側の handler を handler() で入れ直していた。VM が持つのは生の受け手(@do 関数・defhandler の節の data・Rust の scheduler の SchedulerPrompt)で、@do 関数とは限らない。"
       :evidence "packages/doeff-core-effects/doeff_core_effects/handlers.py・packages/doeff-traverse/doeff_traverse/handlers.py の _program_handler(h)")]
  :context
    [(interpretation
       "handler の形を 1 つにすると、型と読み手の前提が揃う。素の関数の handler は、返した DoExpr を VM が評価する別の約束で動き、@do 関数とは return の意味が違った(素の関数に @do を足しただけでは Resume が続けにならない)。")
     (interpretation
       "@do 関数の partial と、@do の method を束縛した bound method は、引数を束縛しただけの @do の Kleisli なので handler にできる。callable の instance・lambda・素の関数は @do 関数でない。")
     (interpretation
       "入れ直しは、一度入った handler をもう一度入れるだけで、新しい handler を作る事ではない。新しく作る口(handler())と入れ直しの口(reinstaller)を分ければ、作る口だけを厳しくできる。")]
  :decision
    [(rule R1 "handler(f) は、f が @do 関数(@do が返した wrapper — __doeff_generator_function__ を持つ)か、その partial か bound method の時だけ handler にする。既に印の付いた installer はそのまま返す。それ以外(素の関数・lambda・callable の instance)は、作る所で TypeError「<名> is not a @do function」で断る。@do 関数でも (effect, k) を取れない物は、前と同じく組む所で断る。")
     (rule R2 "with_handlers と stacked_handlers は、印 _doeff_is_handler_fn の付いた handler(@handler の @do 関数・defhandler・stacked_handlers の束)だけを取り、それ以外を組む所で TypeError「<名> is not a handler」で断る。中で handler() に通さない。")
     (rule R3 "GetHandlers(k) で読んだ handler の入れ直しは doeff.handler_utils.reinstaller が受け持ち、VM の WithHandler で直に入れる。新しい handler を作るのに使わない。")
     (rule R4 ".semgrep.yaml の 3 規則が、試験の外で同じ形を書く所で断る: @do の無い def への @handler・handler 類への lambda・同じ scope の @do の無い関数の handler 類への受け渡し。試験は断りを確かめるために断られる形をわざと作るので外す。")
     (rule R5 "書くその場面の助言は doeff の外(agent-commons の defjevrule)が持つ。doeff の側には R1〜R4 とその確かめだけを置く。")]
  :laws
    [(law handler-is-a-do-function
       :statement "handler(f) ⇒ is_do_function(f) ∨ marked(f) ∨ TypeError ∧ with_handlers(hs, p) ⇒ ∀h∈hs. marked(h) ∨ TypeError"
       :counterexamples
         [(counterexample "with_handlers([lambda effect, k: ...], prog) が中で handler() に通され、素の関数が handler として走る")
          (counterexample "@handler を素の def に付け、return Resume(k, v) の約束で動く handler ができる")
          (counterexample "入れ直しのために handler() を緩め、新しく作る口でも素の関数が通る")]
       :enforced-by ["docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy::test-adr-doe-core-003-handler-takes-only-a-do-function"
                     "docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy::test-adr-doe-core-003-handler-stacks-take-only-handlers"
                     "tests/test_handler_is_a_do_function.py"
                     "tests/test_with_handlers_refuses_unmarked_program_function.py"
                     "defsemgrep doeff-handler-requires-do・doeff-handler-is-not-a-lambda・doeff-handler-target-is-a-do-function"]
       :wiring "配線済み(2026-10-20)— この file の deftest と defsemgrep、tests の 2 file。書く場面の助言は agent-commons の defjevrule(R5)")]
  :enforcement
    [(deftest test-adr-doe-core-003-handler-takes-only-a-do-function
       ;; R1: 素の関数と lambda を断り、@do 関数を handler にする。
       (with [(pytest.raises TypeError :match "is not a @do function")]
         (handler (fn [effect k] (Pass effect k))))
       (with [(pytest.raises TypeError :match "print is not a @do function")]
         (handler print))
       (val passes (doeff-do (fn [effect k] (yield (Pass effect k)))))
       (val installed (handler passes))
       (assert (is (getattr installed "_doeff_is_handler_fn" False) True))
       (assert (is (handler installed) installed))
       (assert (= (run (installed (Pure "ok"))) "ok")))
     (deftest test-adr-doe-core-003-handler-stacks-take-only-handlers
       ;; R2: with_handlers と stacked_handlers は印の無い物(素の関数・@handler の無い @do 関数)を断り、handler を取る。
       (val passes (doeff-do (fn [effect k] (yield (Pass effect k)))))
       (with [(pytest.raises TypeError :match "is not a handler")]
         (with-handlers [passes] (Pure "ok")))
       (with [(pytest.raises TypeError :match "is not a handler")]
         (with-handlers [(fn [effect k] (Pass effect k))] (Pure "ok")))
       (with [(pytest.raises TypeError :match "is not a handler")]
         (stacked-handlers passes))
       (assert (= (run (with-handlers [(handler passes)] (Pure "ok"))) "ok"))
       (assert (= (run ((stacked-handlers (handler passes)) (Pure "ok"))) "ok")))
     (defsemgrep handler-requires-do
       "doeff-handler-requires-do"
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_handlers.py"
         "source" "@handler\ndef answer(effect, k):\n    return Resume(k, 1)\n"}]
       [{"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_handlers.py"
         "source" "@handler\n@do\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}
        {"relative-path" "packages/doeff-core-effects/doeff_core_effects/example_non_tail.py"
         "source" "@handler\n@do(non_tail=True)\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n"}])
     (defsemgrep handler-is-not-a-lambda
       "doeff-handler-is-not-a-lambda"
       [{"relative-path" "examples/example_stack.py"
         "source" "program = with_handlers([lambda effect, k: Resume(k, 1)], body())\n"}
        {"relative-path" "examples/example_installer.py"
         "source" "program = handler(lambda effect, k: Resume(k, 1))(body())\n"}]
       [{"relative-path" "examples/example_stack.py"
         "source" "program = with_handlers([answer], body())\n"}])
     (defsemgrep handler-target-is-a-do-function
       "doeff-handler-target-is-a-do-function"
       [{"relative-path" "examples/example_stack.py"
         "source" "def answer(effect, k):\n    return Resume(k, 1)\n\nprogram = with_handlers([answer], body())\n"}
        {"relative-path" "examples/example_installer.py"
         "source" "def answer(effect, k):\n    return Resume(k, 1)\n\nprogram = handler(answer)(body())\n"}]
       [{"relative-path" "examples/example_stack.py"
         "source" "@do\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n\nprogram = handler(answer)(body())\n"}
        {"relative-path" "examples/example_installer.py"
         "source" "@handler\n@do\ndef answer(effect, k):\n    return (yield Resume(k, 1))\n\nprogram = with_handlers([answer], body())\n"}])]
  :plans ["docs/adr/defadr_doeff_core_003_handler_is_a_do_function.hy"
          "doeff/program.py (_is_do_function・_refuse_non_do・_refuse_unmarked — handler・with_handlers・stacked_handlers)"
          "doeff/handler_utils.py (reinstaller)"
          "packages/doeff-core-effects/doeff_core_effects/handlers.py・packages/doeff-traverse/doeff_traverse/handlers.py (入れ直しを reinstaller に)"
          "tests/test_handler_is_a_do_function.py・tests/test_with_handlers_refuses_unmarked_program_function.py"
          ".semgrep.yaml (3 規則)"])
