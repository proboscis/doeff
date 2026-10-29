;;; Executable ADR: doeff の subpackage(packages/ の下の package)が利用者に見せる公開 API は、effect と handler と handler を返す工場と、
;;; それらを作り・答えを読み・失敗を受け・handler を組むのに要る型だけ。ほかの名は、名か module の path に _ を付けた内側に置くか、
;;; effect か handler か工場に作り直す。handler は印 _doeff_is_handler_fn の付いた callable の値(中核の ProgramHandler)で、program に
;;; 当てる公式の形は h1(h2(p))。
;;;
;;; [removed]
;;; 範囲と境界の扱いは、依頼書 [id](6 問)と、名ごとに問い直した続きの書の答え(どれも 2026-10-27)に従う
;;; [removed]
;;; [id]・card [id])に従い、R3・R6 をそれで改めた。答えの無い物は R13 の「未決」に並べ、答えが
;;; 来たら持ち主がこの ADR に条を足す。
;;;
;;; 強制は card [id] の test(tests/test_public_api_known_violations.py)が持つ。公開 API を数える 1 か所
;;; (tools/public_api_inventory.py)の目録を、package ごとの既知の表(tests/data/public_api_known_violations.json)と突き合わせ、表は
;;; 減るだけ。工場と読めた名は目録の道具が呼んで印を確かめ、印の無い値を返す名を外れと数える(R3)。この ADR は別の検を作らない —
;;; law の :enforced-by がその test を指し、この file の deftest はその test の関数を呼ぶだけ(R12)。
;;;
;;; 戻し方: 決まりを外すなら、この file と tests/test_public_api_known_violations.py と既知の表を消し、台帳(docs/adr/enforcement-ledger.json)を
;;; 作り直す。範囲か境界の 1 条を改めるなら、その条と既知の表の範囲と組を同じ commit で改める。外すのは持ち主の言葉が在る時だけ。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [defk deftest val <-])
(import importlib.util)
(import pathlib [Path])
(import tempfile)
(import types [ModuleType])


(val ROOT (get (. (.resolve (Path __file__)) parents) 2))
(val GUARD-TEST "tests/test_public_api_known_violations.py")


(defk guard-module []
  {:pre [] :post [(: % ModuleType)] :tags {:context "doeff-api-adr" :role "foundation"}}
  "card [id] の test の module を読む(tests/ は package でない — pytest が集める物と混ぜないよう別の名で読む)。"
  (val spec (importlib.util.spec-from-file-location "_adr_doe_api_001_guard" (/ ROOT GUARD-TEST)))
  (val module (importlib.util.module-from-spec spec))
  (.exec-module spec.loader module)
  module)


(defadr ADR-DOE-API-001
  :title "doeff の subpackage が利用者に見せる公開 API は effect と handler と handler を返す工場と、それらの値・結果・例外・設定の型だけ。handler は印の付いた callable の値(ProgramHandler)で、program には h1(h2(p)) で当て、class で書かず、公開の名は subpackage の入口からだけ出す。強制は公開 API の目録と減るだけの既知の表を突き合わせる 1 つの test が持ち、工場は呼んで handler が返る事を見る"
  :status "accepted"
  :scope ["packages/*/ (subpackage の import の根・submodule・[project.scripts]・entry point)"
          "tools/public_api_inventory.py (公開 API を数える 1 か所)"
          "tests/test_public_api_known_violations.py (強制 — card [id])"
          "tests/data/public_api_known_violations.json (既知の表 — 減るだけ)"
          "docs/adr/defadr_doeff_api_001_subpackages_show_effects_and_handlers.hy"]
  :problem
    [(fact
       [removed]
       :evidence "取り組み doeff-subpackages-effects-only・持ち主の発言 [id]")
     (fact
       "発端: 2026-10-26 の docs/19-agent-tutorial.md とその動画は、doeff-agents の class ScenarioAgentHandler を作って .wrap で program を包む形を見せ、doeff_agents の根は launch_session・monitor_session のような、session を手で起こして見張る手続き型の関数を出していた。そこから利用者は、effect を yield して handler を据える doeff の書き方でない使い方を覚える。"
       :evidence "commit 548f04f62(card [id])が doeff_agents の根から外した 69 名と、その前の版の docs/19-agent-tutorial.md")
     (fact
       "公開 API の目録(card [id])は packages/ の下の 39 package の公開の名に種類(effect・handler・それ以外・未分類)を付け、card [id] がそれ以外と未分類を、外れ(直しの向きつき)と境界(種類つき)に分けた。決まりの文だけでは入るか外れるかが決まらない境界と、effect と handler を出す物でない道具の package 12 個が残った。"
       :evidence "tools/public_api_inventory.py・tests/data/public_api_known_violations.json の組の名")
     (fact
       "境界と道具の package の扱いを、依頼書 [id](道具の package・型・class の形の handler・combinator・submodule・CLI コマンドと __version__ の 6 問)で問い、2026-10-27 に 6 問とも答えが出た。型と submodule はそのまま決まり、class の形の handler は案の外の向き(class で書かない)に決まった。道具の package・combinator・CLI コマンドと __version__ は、名ごとに具体的に見せて問い直す答えで、続きの書が名ごとに決めている。"
       :evidence "依頼書 [id] の答え [id]・[id]・[id]・[id]・[id]・[id]")
     (fact
       "既知の違反の表(main edc3802d5b5f): 39 package の 13,753 行と「足した名」31 行。外れの組と減らす向き — 外れ — handler の中へ入れる 2,498(handler だけが使う部品は handler の module の _ の付いた所へ・R1)・外れ — 内側の module へ移す 2,276(R1・R5)・外れ — effect にする 121(R1)。"
       :evidence "tests/data/public_api_known_violations.json(main edc3802d5b5f)— 名の正本はこの表で、この fact の数は書いた時の写し")
     (fact
       "既知の違反の表(同じ版)の境界の組と扱い — 基盤・道具の package 6,653(R10・R13)・effect の結果の型 857・effect の値の型 834・例外の型 214・handler の設定の型 115(R2 で許す物・R12)・workflow の DSL の言葉 66(doeff-conductor の dsl・R13)・class の形の handler 55(R4)・combinator 23(R6・R13)・handler と読める名(道具が読めない)19(handler の戻りの型を付けて目録に handler と読ませる)・未分け 16(R13)・CLI 4(R7 で残す物・R12)・Hy の macro 2(doeff-docker の with_handlers・doeff-events の event_loop — R13)。"
       :evidence "tests/data/public_api_known_violations.json(main edc3802d5b5f)")
     (fact
       "既知の違反の表(同じ版)の行の多い package — doeff-cluster 5,192(範囲の答え待ち)・doeff-agents 1,653・doeff-records 1,507・doeff-core-effects 1,273・doeff-claude-code 774・doeff-hy 735(範囲の答え待ち)・doeff-conductor 558。直しは行の多い決まりの範囲の package から、取り組み doeff-subpackages-effects-only の区切りごとに進める。"
       :evidence "tests/data/public_api_known_violations.json(main edc3802d5b5f)")
     (fact
       "公開の submodule(R5 の外れ)は、card [id] が既知の表に載せた 39 package の 731 行(決まりの範囲の 28 package に 410・範囲の答え待ちの 11 package に 321)。決まりの範囲の import の根のすぐ下の子は 267 個で、子を _<名> に替えれば下の module も隠れる。直しの分けは card [id](stage 1〜3 の 31 枚)。"
       :evidence "tests/data/public_api_known_violations.json の (submodule) の行(doeff ec6311c0a の目録)・artifact 2026-10-28-doeff-submodule-hide-split.html(doeff 37467d478138 で 733 行・第 1 層の子 265 — その後 doeff-agents と doeff-hy-pytest が submodule を減らした)")
     (fact
       [removed]
       [removed]
     (fact
       "目録(doeff 19e34510931f の木・2026-10-29)は、戻りの型か return の形で handler を返す工場と読める定義を 39 package に 225 個(公開の行で 435)数えた。最小の引数で呼ぶと、202 個(引数なし 69・置き物 133)が印の付いた handler か handler の組を返し、21 個は呼ぶと例外(置き物を呼ぶ時に読む物・tmux の無い機体で tmux を探す doeff-agentic の tmux_handler など)、2 個 — doeff-conductor の make_scheduled_handler・make_offloaded_scheduled_handler — は印の無い (effect, k) の @do 関数(handler の body)を返した。maybe・result・on_raise・absent_as・scheduled は戻りの型が program(DoExpr・Program)なので工場と読まれず、引数なしで呼ぶと program が足りず、program を渡すと印の無い program を返す。"
       :evidence "tools/public_api_inventory.py の目録の行の理由(工場の行の末尾に、どう呼び・何が返ったか)・tests/data/public_api_known_violations.json の組「外れ — handler を返す工場の形に直す」と「足した名」の card [id] の 6 行")]
  :context
    [(interpretation
       "doeff の値は、handler を替えれば同じ program が test でも本番でも走る事にある。subpackage が手続き型の関数や client の class を見せると、利用者はその口を直に呼び、handler の差し替えの外で書く。effect と handler だけを見せれば、どの subpackage も effect を yield して handler を据えるだけで使える。")
     (interpretation
       "handler は印 _doeff_is_handler_fn の付いた callable の値(中核の ProgramHandler — program を受けて、包んだ program を返す)。中身は (effect, k) を受けて答える(Resume)か外へ渡す(Pass)body で、@handler・defhandler・stacked_handlers が印を付ける(ADR-DOE-CORE-003)。中核の state() のように、呼ぶと handler か handler の組を返す関数が handler を返す工場。program を受けて program を返す関数でも、印の無い物は handler でなく、呼んで印の無い値を返す物は工場でない — 数えるのは形の読みでなく印。")
     (interpretation
       "handler を値にすれば、program に当てるのは呼ぶだけ(h1(h2(p)) — 外から h1・h2 の順に包む)で、名を付けて使い回すのも渡すのも普通の値と同じ。中核の WithHandler(handler, program)は VM へ渡す生の形で、利用者が書く形でない。")
     (interpretation
       "型は effect と handler の一部: 値の型が無いと effect を作れず、結果の型が無いと答えを読めず、例外の型が無いと失敗を受けられず、設定の型が無いと handler を作れない。")
     (interpretation
       "公開の名の数え方は目録の道具の 1 か所が持つ: import の根の __init__ が出す名・path のどの部分も _ で始まらない submodule が出す名と、その submodule そのもの(R5)・[project.scripts] と entry point と maturin の bin。この ADR は数え方を別に持たない。repo の根の doeff(program・do・run・handler の核)は subpackage でなく、目録も数えない。")]
  :decision
    [(rule R1 "subpackage の公開 API は effect(EffectBase の型と、effect を作る関数)と handler(@handler・defhandler・stacked_handlers の、印の付いた値)と handler を返す工場(呼ぶと handler か handler の組を返す関数 — R3)だけ(2026-10-26 の決まりを、2026-10-28 の決定 [id]〜[id] が工場まで書き表した)。ほかの名(手続き型の関数・program 関数・client や API の class・設定を読む関数・定数)は、名か module の path に _ を付けた内側に置くか、effect か handler か工場に作り直す。")
     (rule R2 "effect の値の型・effect の結果の型・effect と handler が投げる例外の型・handler の設定の型は、effect と handler の一部として公開してよい(2026-10-27・依頼書 [id] の問い types の答え [id])。")
     (rule R3 "handler は印 _doeff_is_handler_fn の付いた callable の値(中核の ProgramHandler)で、中身は (effect, k) を受けて答えるか外へ渡す body(2026-10-28 の決定 [id]〜[id])。handler を返す工場は、引数なしか最小の引数で呼ぶとその値(か、その値の list か tuple)を返す関数。印の無い関数は、program を受けて program を返しても handler と数えず、戻りの型を ProgramHandler と書いても呼んで印の無い値を返す関数は工場と数えない。目録の道具は工場と読めた名を最小の引数(既定値の無い引数ごとに中身の無い置き物)で呼んで印を確かめ、印の無い値を返す名を外れ(handler を返さない関数)と数える。呼ぶと例外の出る工場(置き物を呼ぶ時に読む物・機体に無い命令を探す物)は戻りの型の読みのまま。改めた前の形: 2026-10-27 の「handler は (effect, k) を受けて答える関数に限り、program を受けて program を返すだけの関数を handler と数えない」(combinator の書 [id] の問い maybe の答え [id])— 決定 (1) が handler を ProgramHandler の値と決めたので、形(program → program)でなく印で数える形に改めた。印の無い program → program の関数を handler と数えない事は変わらない。")
     (rule R4 "handler は class で書かない。名が Handler で終わり method で effect を受ける class の形の handler は、内側へ隠すのでなく doeff の handler(@handler・defhandler)に書き直して class を消す(2026-10-27・問い class-handler の答え [id])。直しは card [id](doeff-git)・[id](doeff-agentic)・[id](doeff-conductor)。")
     (rule R5 "公開の名は subpackage の import の根(__init__)からだけ出す。submodule は名に _ を付けて隠し、_ で始まらない submodule は外れと数える(2026-10-27・問い submodule の答え [id])。subpackage ごとの直しの分けは card [id]。")
     [removed]
     (rule R7 "人が打つ CLI コマンド(doeff-agents・conductor・doeff-flow・doeff-agentic-tui)は残し、中身は subpackage の effect を出す program を handler の下で走らせるだけにする。CLI コマンドのための client と API の class は subpackage の内側に置く(2026-10-27・書 [id] の問い cli-user の答え [id])。直しは card [id]・[id]・[id]。")
     (rule R8 "code は CLI コマンドのサブコマンドで process を起こさない。常駐の process は役ごとの Python の module の入口に分け、起動は effect にして、handler が sys.executable -m <module> で起こす(PATH を見ない)。doeff-agents の CLI コマンド doeff-sessionhost は消した(2026-10-27・書 [id] の問い sessionhost-launch の答え [id]・card [id])。")
     (rule R9 "subpackage は __version__ を持たない。版は importlib.metadata.version で読む(2026-10-27・書 [id] の問い version の答え [id]・card [id])。")
     [removed]
     (rule R11 "範囲は packages/ の下の package の全部。既知の表の範囲「決まりの範囲」の 27 package には今から当てる。「範囲の答え待ち」の 12 package は、R10 の答えが出た物(doeff-vm・doeff-hy・doeff-hy-pytest)から表の範囲と組を答えに合わせて改める(改める card は持ち主が切る)。doeff-hy は R14 で、doeff-cluster は R15 で「決まりの範囲」へ移した。")
     (rule R12 "強制は 1 か所: tests/test_public_api_known_violations.py が tools/public_api_inventory.py の目録を package ごとの既知の表と突き合わせる(card [id])。表に無い外れが在れば赤・表に在るのに目録に無い名が在れば赤・表の「名」の組は減るだけ。足すしか無い外れは「足した名」に訳と決め(card・依頼・発言の id)を 1 行ずつ書く。R3 の工場の確かめ(呼んで印を見る)も目録の道具の中に置き、呼んで印の無い値を返す名は同じ照合で外れになる(card [id])。この ADR は別の検を作らない。R2 の型・R7 の CLI コマンドのように許した物も、目録の道具が許す物と数えるまでは既知の表の境界の組に残し、隠して直さない。")
     (rule R13 "未決(答えが来たら持ち主がこの ADR に条を足し、既知の表の範囲と組を改める card を切る): (a) combinator の残り 6 個 — doeff-core-effects の closing_links、doeff-traverse の sequence・try_call、doeff-validation の check・perform・validate(書 [id] の差し戻しを受けて 1 通 1 問に分けて問い直している — sequence は書 [id] の答え待ち、残りはその後に出す)。(b) 道具の package 8 個 — doeff-adr(書 [id] は差し戻され、1 通 1 問で問い直す)と、まだ問うていない doeff-vm-core・doeff-linter・doeff-indexer・doeff-effect-analyzer・doeff-test-target・doeff-agentic-cli・doeff-domain(doeff-cluster は R15・doeff-hy-pytest は R10 で決めた)。(c) 問いのまだ無い境界 — doeff-conductor の workflow の DSL の言葉(doeff_conductor._dsl の 66 行)・doeff-hy の外の Hy の macro 2 個・doeff-hy の CLI doeff-hy-check と pytest の plugin の module・未分けの 16 行。")
     [removed]
     (rule R15 "doeff-cluster は範囲「決まりの範囲」に入れる(2026-10-29・書 [id] の問い cluster-scope の答え [id] — hide・card [id]・[id])。名は R1・R2・R5 のとおり: 外の repo が使う名(書の数え — 公開の名 3,443 個のうち 423 個)だけを入口から見せ、そのうち effect にする名と handler の中へ入れる名は後の card で直す。外が使わない名は内側の module へ移す。入口は R5 のとおり import の根 doeff_cluster に置く — 今の doeff_cluster は根の __init__ を持たない Hy の namespace package なので、card [id] の道具が根に遅延の import の入口を作り、根のすぐ下の区画(coordinator・foundation・record_store・shared・sim・worker)を _<区画> へ移して、下の submodule(2026-10-29 の目録で 217 個)をまとめて隠す。書の「選んだ後の code」は区画の path(doeff_cluster.sim)から読む形を見せたが、R5 と目録の道具と表の test は import の根から出す名で数えるので、使い手の import は (import doeff_cluster [sim-cluster StartWorker]) になる。macro defsystem(R10)は根のすぐ下の公開の module doeff_cluster.macros から見せ、R5 の外の境界に置く — Hy の require が module の path で macro を読むので(R14 と同じ訳)。今の doeff_cluster.shared.entry.macros から移すのは shared の区画の段 1。隠した後は、submodule に足した名が既定で公開にならない。直しの分けは card [id] の子(区画ごとの段 1・使い手の repo の段 2・移し替えの module と表の行を消す段 3)。")
     (rule R16 "handler を program に当てる公式の形は h1(h2(p)) — handler の値を呼んで包む(2026-10-28 の決定 [id]〜[id])。中核の WithHandler(handler, program)は VM へ渡す生の形で、利用者向けの docs と例には出さない。今の docs の WithHandler の書き替えは持ち主が card を切る。")
     (rule R17 "その場で一度だけ当てる handler は Hy の handle の macro(match のように effect ごとの節を並べてその場で当てる — doeff_hy.handle・card [id])で書き、使い回す handler は defhandler か handler を返す工場に名を付けて出す(2026-10-28 の決定 [id]〜[id])。subpackage が見せるのは名を付けた方(R1)。")]
  :laws
    [(law subpackage-shows-only-effects-and-handlers
       :statement "for_all package p under packages/ with scope(p) = 決まりの範囲: outside(p) = {n in public_names(p) | kind(n) not in {effect, handler}} = known(p), and listed_names(table) only shrinks from its first version"
       :counterexamples
         [(counterexample "doeff_agents の根が launch_session を出し、tutorial がそれで session を手で起こす(この ADR の発端の形)")
          (counterexample "subpackage の根に新しい helper 関数を足し、既知の表にも「足した名」にも書かずに通る")
          (counterexample "外れを直したのに既知の表に名を残し、表が縮まない")
          (counterexample "既知の表の「名」の組に新しい名を書き足して、足した外れを通す")]
       :enforced-by ["tests/test_public_api_known_violations.py::test_public_api_outside_effects_and_handlers_is_in_the_known_table"
                     "tests/test_public_api_known_violations.py::test_an_added_outlier_turns_red_and_removing_it_turns_green"
                     "tests/test_public_api_known_violations.py::test_a_fixed_name_left_in_the_table_turns_red"
                     "tests/test_public_api_known_violations.py::test_the_known_table_only_shrinks_from_its_first_version"
                     "tests/test_public_api_known_violations.py::test_a_name_put_into_the_listed_groups_turns_red"
                     "docs/adr/defadr_doeff_api_001_subpackages_show_effects_and_handlers.hy::test-adr-doe-api-001-an-added-outlier-turns-the-guard-red"
                     "docs/adr/defadr_doeff_api_001_subpackages_show_effects_and_handlers.hy::test-adr-doe-api-001-the-known-table-is-well-formed-and-only-shrinks"]
       :wiring "配線済み(2026-10-26・card [id])— package ごとの 39 件・使い捨ての package の赤と緑・表の形・減るだけ。この file の deftest はその関数を呼ぶだけ")
     (law a-handler-is-a-marked-value-and-a-factory-returns-one
       :statement "handler(h) <=> marked(h) (h: ProgramHandler); factory(f) <=> for the arguments f needs at least (none when every parameter has a default): marked(f()) or f() is a list or tuple of marked values; counted_as_factory(f) and callable_without_arguments(f) and not factory(f) => outside(f); takes(f, Program) and returns(f, Program) and not marked(f) => not handler(f); class c named *Handler => not handler(c)"
       :counterexamples
         [(counterexample "maybe(p) のように program を受けて program を返す印の無い関数を、形が program -> program だから handler と数えて許す")
          (counterexample "戻りの型を ProgramHandler と書いた関数が、呼ぶと program を素通しする印の無い関数を返すのに、戻りの型だけで handler を返す工場と数えられて表の外で通る")
          (counterexample "名が Handler で終わる class を、method で effect を受けるから handler と数えて公開する")]
       :enforced-by ["tests/test_public_api_inventory.py::test_names_are_typed_by_marker_return_type_and_return_shape"
                     "tests/test_public_api_inventory.py::test_a_factory_says_whether_its_call_was_checked"
                     "tests/test_public_api_inventory.py::test_a_factory_is_called_with_home_and_cwd_in_a_scratch_dir"
                     "tests/test_public_api_known_violations.py::test_a_factory_whose_call_is_not_a_handler_turns_red"
                     "tests/test_public_api_known_violations.py::test_public_api_outside_effects_and_handlers_is_in_the_known_table"
                     "docs/adr/defadr_doeff_api_001_subpackages_show_effects_and_handlers.hy::test-adr-doe-api-001-a-factory-whose-call-is-not-a-handler-turns-the-guard-red"]
       :wiring "一部配線(2026-10-29・card [id])— 目録は印で handler を数え、印の無い Handler の名の class を未分類、(effect, k) の関数を handler と読む。戻りの型か return の形で工場と読めた関数は、最小の引数(無いか、既定値の無い引数ごとに置き物)で呼んで(HOME・XDG の dir・cwd を一時の dir へ向けて)印を確かめ、印の無い値を返せば外れ「handler を返さない関数」と数えるので、既知の表に無ければ赤。呼ぶと例外の出る工場は戻りの型の読みのまま(行の理由に例外を書く — 2026-10-29 の目録で 225 個のうち 21 個)。import の時に module が決めた書き先は一時の dir へ向かない。この file の deftest はその関数を呼ぶだけ")
     (law the-public-names-come-from-the-import-root
       :statement "for_all package p in scope: for_all public submodule m of p: m's path has a part starting with _ — enforced as: public_submodules(p) = known_submodules(p), known_submodules only shrinks from the version that added them, and a name n moved from a submodule s to the import root r is the same row as (s, n) when s no longer shows n or shows the same definition"
       :counterexamples
         [(counterexample "effect を doeff_agentic.effects から import させ、根と submodule の 2 つの口で同じ名を見せる")
          (counterexample "subpackage に _ の無い submodule を新しく足し、出す名が effect だけなので既知の表にも「足した名」にも書かずに通る")
          (counterexample "R2 の型を submodule から入口へ移すと、表の鍵(名を出す module)が替わったと読まれて赤になり、直しが表の名の組を書き足すか「足した名」に書くしか無い")
          (counterexample "submodule がまだ出している名と同じ名の、違う定義を入口に足し、入口へ移した名と読まれて通る")]
       :enforced-by ["tests/test_public_api_known_violations.py::test_public_api_outside_effects_and_handlers_is_in_the_known_table"
                     "tests/test_public_api_known_violations.py::test_an_added_public_submodule_turns_red_and_hiding_it_turns_green"
                     "tests/test_public_api_known_violations.py::test_moving_names_from_a_submodule_to_the_root_keeps_the_table_green"
                     "tests/test_public_api_known_violations.py::test_a_different_definition_with_a_known_name_at_the_root_turns_red"
                     "tests/test_public_api_known_violations.py::test_a_marked_shim_keeps_the_modules_below_a_moved_package_at_their_old_names"
                     "tests/test_public_api_known_violations.py::test_the_known_table_only_shrinks_from_its_first_version"
                     "tests/test_public_api_known_violations.py::test_a_submodule_row_put_into_the_table_after_it_had_submodule_rows_turns_red"
                     "docs/adr/defadr_doeff_api_001_subpackages_show_effects_and_handlers.hy::test-adr-doe-api-001-a-public-submodule-turns-the-guard-red-and-a-moved-name-does-not"]
       :wiring "配線済み(2026-10-28・card [id])— 目録は _ で始まらない submodule を 1 行(名を出す module の欄が (submodule))の外れと数え、既知の表は今の公開の submodule 731 行を載せて、submodule の行を足した版から減るだけ。名を submodule から入口へ移した行は表の module を書き換えずに同じ行と読む(submodule がまだ同じ名を出す間は定義の場所が同じ時だけ・古い口を閉じた後は名と根だけ)。1 行目に印 # moved-submodule-shim: <古い名> -> <新しい名> の在る移し替えの module(card [id] の道具が置く)は、移した先の package の下の module も古い名で数えるので、stage 1 は表を書き換えずに緑。隠した submodule の行は表から消す。この file の deftest はその関数を呼ぶだけ")]
  :enforcement
    [(deftest test-adr-doe-api-001-an-added-outlier-turns-the-guard-red
       ;; R12: 強制は card [id] の test が持ち、ここではその関数を呼ぶだけ — 使い捨ての package に effect と handler の外の
       ;; 関数を 1 つ足すと赤・外すと緑・直したのに表に残すと赤。
       (<- guard ModuleType (guard-module))
       (with [scratch (tempfile.TemporaryDirectory)]
         (guard.test-an-added-outlier-turns-red-and-removing-it-turns-green (/ (Path scratch) "added"))
         (guard.test-a-fixed-name-left-in-the-table-turns-red (/ (Path scratch) "fixed"))))
     (deftest test-adr-doe-api-001-the-known-table-is-well-formed-and-only-shrinks
       ;; R12: 既知の表は packages/ の下の dir を 1 つずつ持ち、「名」の組は表を足した commit の版から減るだけ(同じ test の関数を呼ぶ)。
       (<- guard ModuleType (guard-module))
       (guard.test-the-known-table-has-one-well-formed-entry-per-package-dir)
       (guard.test-the-known-table-only-shrinks-from-its-first-version)
       (guard.test-a-name-put-into-the-listed-groups-turns-red)
       (guard.test-a-submodule-row-put-into-the-table-after-it-had-submodule-rows-turns-red))
     (deftest test-adr-doe-api-001-a-public-submodule-turns-the-guard-red-and-a-moved-name-does-not
       ;; R5・R12: 同じ test の関数を呼ぶだけ — 使い捨ての package に _ の無い submodule を足すと赤・隠すと緑、名を submodule から
       ;; 入口へ移しても(移し替えの module を残す間も消した後も)表を書き換えずに緑、同じ名の違う定義を入口に足すと赤。
       (<- guard ModuleType (guard-module))
       (with [scratch (tempfile.TemporaryDirectory)]
         (guard.test-an-added-public-submodule-turns-red-and-hiding-it-turns-green (/ (Path scratch) "submodule"))
         (guard.test-moving-names-from-a-submodule-to-the-root-keeps-the-table-green (/ (Path scratch) "moved") "lazy" "alias")
         (guard.test-a-marked-shim-keeps-the-modules-below-a-moved-package-at-their-old-names (/ (Path scratch) "marked"))
         (guard.test-a-different-definition-with-a-known-name-at-the-root-turns-red (/ (Path scratch) "different"))))
     (deftest test-adr-doe-api-001-a-factory-whose-call-is-not-a-handler-turns-the-guard-red
       ;; R3・R12: 同じ test の関数を呼ぶだけ — 使い捨ての package の、戻りの型で工場と読める名が呼ぶと印の無い値を返すと赤・
       ;; handler を返すように直すと緑。
       (<- guard ModuleType (guard-module))
       (with [scratch (tempfile.TemporaryDirectory)]
         (guard.test-a-factory-whose-call-is-not-a-handler-turns-red (/ (Path scratch) "factory"))))]
  :plans ["tests/test_public_api_known_violations.py・tests/data/public_api_known_violations.json (card [id])"
          "tools/public_api_inventory.py (card [id])"
          "取り組み doeff-subpackages-effects-only の区切り 2〜4 の card (直し — doeff-agents・CLI コマンド・doeff-vm・doeff-hy の macro・writer_log・__version__・class の handler・submodule)"
          "R11・R13 の答えが来た後: 既知の表の範囲と組を答えに合わせる card、この ADR に条を足す card (持ち主が切る)"
          "R6: maybe・result・on_raise・absent_as・scheduled を handler を返す工場の形に直し、既知の表の組「外れ — handler を返す工場の形に直す」を空にする card (持ち主が切る)"
          "R16: 利用者向けの docs と例の WithHandler を h1(h2(p)) に書き替える card (持ち主が切る)"])
