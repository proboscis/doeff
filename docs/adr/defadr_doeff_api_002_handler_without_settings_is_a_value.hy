;;; Executable ADR: 設定の無い handler は factory にしない。引数を 1 つも取らない factory は、呼ぶたびに新しい状態を作る時だけ許す。
;;; await_handler は await_handler() でなく素の handler の値で、[await_handler (lazy-ask :strict True) try-handler (state) writer] と書く。
;;;
;;; 出自 = 持ち主(利用者)の決定 2026-10-29(agora card ki-8d7e5eb863c7)。原文と出自の記録は持ち主の内部の追跡に置き、この公開の
;;; repo には決定の日付と card の key だけを書く。
;;;
;;; 強制は 1 か所: tools/handler_factory_census.py が出荷する code(doeff/・packages/・examples/ — tests を除く)から引数の無い
;;; factory を拾い、tests/data/zero_parameter_handler_factories.json の既知の表と突き合わせる(R4)。この file の deftest がその
;;; 突き合わせを呼び、旧い await_handler の形を道具が拾う事と、await_handler が素の handler として動く事を確かめる。
;;;
;;; 戻し方: await_handler を factory に戻すなら、_handlers.py の await_handler を def await_handler() -> ProgramHandler に戻し、
;;; doeff.program の _former_factory_handler と FormerFactoryHandler を消し、呼び手を await_handler() に戻して、既知の表に
;;; await_handler の行を「直す」で足す。外すのは持ち主の言葉が在る時だけ。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [defk deftest val <-])
(import importlib.util)
(import sys)
(import pathlib [Path])
(import types [ModuleType])
(import pytest)
(import asyncio)
(import doeff [Await run])
(import doeff_core_effects [await-handler scheduled try-handler])


(val ROOT (get (. (.resolve (Path __file__)) parents) 2))
(val CENSUS "tools/handler_factory_census.py")
(val OLD-AWAIT-HANDLER
  "from doeff.program import handler as _program_handler\n\ndef await_handler() -> ProgramHandler:\n    @do\n    def handler(effect, k):\n        yield Pass(effect, k)\n\n    return _program_handler(handler)\n")
(val NEW-AWAIT-HANDLER
  "@do\ndef _await_handler(effect, k):\n    yield Pass(effect, k)\n\n\nawait_handler: FormerFactoryHandler = _former_factory_handler(_await_handler, \"await_handler\")\n")


(defk census-module []
  {:pre [] :post [(: % ModuleType)] :tags {:context "doeff-api-adr" :role "foundation"}}
  "引数の無い factory の一覧の道具を読む(tools/ は package でない — 別の名で読む)。"
  (val spec (importlib.util.spec-from-file-location "_adr_doe_api_002_census" (/ ROOT CENSUS)))
  (val module (importlib.util.module-from-spec spec))
  ;; dataclass は定義の module を sys.modules から読むので、読む前に名で置く。
  (setv (get sys.modules "_adr_doe_api_002_census") module)
  (.exec-module spec.loader module)
  module)


(defk awaits-two []
  {:pre [] :post [(: % int)] :tags {:context "doeff-api-adr" :role "program"}}
  "await_handler が答える見本の program: 2 を返す coroutine を Await で待つ。"
  (<- answer int (Await (asyncio.sleep 0 2)))
  answer)


(defadr ADR-DOE-API-002
  :title "設定の無い handler は factory にしない。引数を 1 つも取らない factory は呼ぶたびに新しい状態を作る時だけで、ほかは handler の値にする。await_handler は素の handler で、旧い await_handler() は 1 つの版の間 DeprecationWarning つきで同じ handler を返す。強制は引数の無い factory の一覧と既知の表の突き合わせ 1 か所"
  :status "accepted"
  :scope ["packages/doeff-core-effects/doeff_core_effects/_handlers.py (await_handler)"
          "doeff/program.py (FormerFactoryHandler・_former_factory_handler・_install_raw の former_factory)"
          "tools/handler_factory_census.py・tests/data/zero_parameter_handler_factories.json"
          "doeff/・packages/・examples/ の handler を返す関数(tests を除く)"]
  :problem
    [(fact
       "持ち主は 2026-10-29 に、await_handler が引数を取らない関数の呼び出し await_handler() を経由する形を直し、設定の無い handler は factory にしないと決めた(agora card ki-8d7e5eb863c7)。"
       :evidence "持ち主の内部の追跡に在る発言(2026-10-29)")
     (fact
       "2026-10-29 の main の await_handler は def await_handler() -> ProgramHandler で、引数を 1 つも取らずに中で @do def handler(effect, k) を作って返した。factory にした訳は asyncio と scheduler の effect の遅延の import と、_get_await_bridge_loop() を中に閉じ込める事だけで、呼びごとの状態は持たなかった(bridge の loop は process 全体で 1 つ)。README の例は [await_handler(), lazy_ask(strict=True), try_handler, state(), writer] と、同じ列に呼ぶ物と呼ばない物が並んだ。"
       :evidence "packages/doeff-core-effects/doeff_core_effects/_handlers.py(doeff c83155f55de0)")
     (fact
       "同じ木で、引数を 1 つも取らずに handler を返す関数は出荷する code に 15 個在った。6 個は呼ぶたびに新しい状態(記憶の置き場・待ちの表・進みの棒の表)を作り、9 個(await_handler・sequential・maybe・result・production_handlers の 3 つ・conductor の agent_handler と git_handler)は設定も呼びごとの状態も持たない。"
       :evidence "tools/handler_factory_census.py の一覧と tests/data/zero_parameter_handler_factories.json の行の訳")]
  :context
    [(interpretation
       "factory は設定(state の初めの値・lazy_ask の env)か、呼びごとに別に持つ状態を受け取る所。どちらも無い factory の () は何も足さず、書き手に「この handler は呼ぶか」を覚え分けさせる。try_handler・writer と同じ値にすれば、列は [await_handler try_handler writer] と同じ形で並ぶ。")
     (interpretation
       "呼ぶたびに新しい状態を作る factory(in_memory_cache_handler() — 呼びごとに別の cache)は、値にすると process の中の全部の program が 1 つの状態を共有してしまうので、factory のままにする。引数が無くても、それは設定でなく呼びごとの状態の作り手。")]
  :decision
    [(rule R1 "設定(引数)も呼びごとの状態も無い handler は factory にせず、handler の値(印の付いた ProgramHandler)として出す。program には h(p) で当て、列には h のまま置く。")
     (rule R2 "引数を 1 つも取らない factory は、呼ぶたびに新しい状態(記憶の置き場・待ちの表など)を作って handler に閉じ込める時だけ許す。既定値だけの引数を持つ factory(state(initial=None))は設定を受けるので R1 の外。")
     (rule R3 "factory を値に直す時は、旧い呼び(引数の無い呼び)を 1 つの版の間、DeprecationWarning を出して同じ handler を返す形で残す(doeff.program の _former_factory_handler — 型は FormerFactoryHandler で、型の検査も旧い呼びを deprecated と言う)。doeff の中と、doeff の main を読む repo の呼び手を新しい形へ書き換えたら、旧い呼びを消す。PyPI の版で毎日走らせる examples/testing_without_mocks と publications/zenn-use-cases-v0 は、値の形を持つ版が PyPI に出た時に書き換える(ADR-DOE-CORE-003 R4 と同じ訳)。")
     (rule R4 "強制は 1 か所: tools/handler_factory_census.py が出荷する code(doeff/・packages/・examples/ の git の追う Python と Hy — tests の dir と examples/testing_without_mocks を除く)から引数の無い factory を拾い、tests/data/zero_parameter_handler_factories.json の既知の表と突き合わせる。表の行は分け(呼びごとの状態・直す・factory でない)と訳を持つ。表に無い factory は赤、表に在るのに拾えない行も赤(表は code と一緒に減るだけ)。「直す」の行は直す card を訳に書く。")
     (rule R5 "await_handler を R1 の形に直した(2026-10-29・agora card ki-8d7e5eb863c7)。中の asyncio と scheduler の effect の import は、Await に初めて答える時に読む(doeff の import で asyncio を読まない・scheduler の module はこの module の後に読まれる)。Await の意味と bridge の loop の振る舞いは変えない。")
     (rule R6 "R4 の一覧で「直す」に残った物は、card で 1 つずつ R1 の形に直す: sequential(agora card ki-b10e81c19717)・production_handlers の 3 つと conductor の agent_handler・git_handler(agora card ki-e9166ac1c3b1)。maybe と result は 2026-10-28 の決定(ADR-DOE-API-001 R6 — state() と同じ factory)と食い違うので、形を持ち主に問い、答えに揃える(agora card ki-2d05bfc6ec94)。答えが来るまで ADR-DOE-API-001 R6 の形のまま表に「直す」で置く。")]
  :laws
    [(law a-handler-without-settings-is-a-value
       :statement "for_all f in shipped_code with params(f) = {} and returns_handler(f): row(f) in known_table and (verdict(row(f)) = 呼びごとの状態 => new_state_per_call(f)); for_all row in known_table: row.key in census(shipped_code)"
       :counterexamples
         [(counterexample "def await_handler() -> ProgramHandler が中で handler を作って返すだけで、列に await_handler() と try_handler が並ぶ")
          (counterexample "新しい package に引数の無い def xxx_handler() を足し、既知の表に書かずに通る")
          (counterexample "factory を値に直したのに既知の表に行を残し、表が縮まない")
          (counterexample "値に直した handler の旧い呼び xxx() が、警告を出さずに黙って同じ物を返し、呼び手が書き換えられないまま残る")]
       :enforced-by ["docs/adr/defadr_doeff_api_002_handler_without_settings_is_a_value.hy::test-adr-doe-api-002-every-factory-without-parameters-is-in-the-known-table"
                     "docs/adr/defadr_doeff_api_002_handler_without_settings_is_a_value.hy::test-adr-doe-api-002-the-census-finds-the-former-await-handler"
                     "docs/adr/defadr_doeff_api_002_handler_without_settings_is_a_value.hy::test-adr-doe-api-002-await-handler-is-a-handler-value"
                     "tests/test_await_handler_is_a_handler_value.py"]
       :wiring "配線済み(2026-10-29・agora card ki-8d7e5eb863c7)— 一覧の道具と既知の表・この file の deftest 3 つ・tests の 1 file")]
  :enforcement
    [(deftest test-adr-doe-api-002-every-factory-without-parameters-is-in-the-known-table
       ;; R4: 出荷する code の引数の無い factory と既知の表が一致する(表に無い factory も、拾えない行も無い)。
       (<- census ModuleType (census-module))
       (assert (= (census.differences) []) (census.differences)))
     (deftest test-adr-doe-api-002-the-census-finds-the-former-await-handler
       ;; R4・R5: 2026-10-29 より前の await_handler の形は factory と拾われ、値の形は拾われない。Hy の (defn #^ 型 名 []) も拾う。
       (<- census ModuleType (census-module))
       (assert (= (lfor f (census._python-factories "x.py" OLD-AWAIT-HANDLER) f.name) ["await_handler"]))
       (assert (= (census._python-factories "x.py" NEW-AWAIT-HANDLER) []))
       (assert (= (lfor f (census._hy-factories "x.hy" "(defn #^ ProgramHandler clock-handler []\n  (handler answer))\n") f.name)
                  ["clock-handler"])))
     (deftest test-adr-doe-api-002-await-handler-is-a-handler-value
       ;; R1・R3・R5: await_handler は印の付いた値で、program に当てて Await に答える。旧い await_handler() は警告を出して同じ値を返す。
       (assert (is (getattr await-handler "_doeff_is_handler_fn" False) True))
       (assert (= (run ((scheduled) (await-handler (awaits-two)))) 2))
       (with [(pytest.warns DeprecationWarning :match "await_handler has no settings")]
         (val called (await-handler)))
       (assert (is called await-handler)))]
  :plans ["packages/doeff-core-effects/doeff_core_effects/_handlers.py (await_handler を値に)"
          "doeff/program.py (FormerFactoryHandler・_former_factory_handler)"
          "tools/handler_factory_census.py・tests/data/zero_parameter_handler_factories.json"
          "tests/test_await_handler_is_a_handler_value.py"
          "doeff の中の await_handler() と (await-handler) の呼び手(歴史の記録と PyPI の版で走る dir を除く)"
          "agora card ki-b10e81c19717・ki-e9166ac1c3b1・ki-2d05bfc6ec94(R6 の残り)"])
