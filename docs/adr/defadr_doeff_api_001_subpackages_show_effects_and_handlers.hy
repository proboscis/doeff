;;; Executable ADR: doeff の subpackage(packages/ の下の package)が利用者に見せる公開 API は、effect と handler と、それらを作り・
;;; 答えを読み・失敗を受け・handler を組むのに要る型だけ。ほかの名は、名か module の path に _ を付けた内側に置くか、effect か handler に
;;; 作り直す。
;;;
;;; 出自 = 持ち主の決定 2026-10-26(取り組み doeff-subpackages-effects-only・発言 ci-VJFV7TDC7NRK59STYR26GGHZV8・card ki-bd3bec7b1360)。
;;; 範囲と境界の扱いは、依頼書 lt-6KJ8EG0YPWXCQ2GRC5XNSQVNQM(6 問)と、名ごとに問い直した続きの書の答え(どれも 2026-10-27)に従う
;;; (:decision の各条に答えの id を書く)。答えの無い物は R13 の「未決」に並べ、答えが来たら持ち主がこの ADR に条を足す。
;;;
;;; 強制は card ki-aaaa56621c5a の test(tests/test_public_api_known_violations.py)が持つ。公開 API を数える 1 か所
;;; (tools/public_api_inventory.py)の目録を、package ごとの既知の表(tests/data/public_api_known_violations.json)と突き合わせ、表は
;;; 減るだけ。この ADR は別の検を作らない — law の :enforced-by がその test を指し、この file の deftest はその test の関数を呼ぶだけ(R12)。
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
  "card ki-aaaa56621c5a の test の module を読む(tests/ は package でない — pytest が集める物と混ぜないよう別の名で読む)。"
  (val spec (importlib.util.spec-from-file-location "_adr_doe_api_001_guard" (/ ROOT GUARD-TEST)))
  (val module (importlib.util.module-from-spec spec))
  (.exec-module spec.loader module)
  module)


(defadr ADR-DOE-API-001
  :title "doeff の subpackage が利用者に見せる公開 API は effect と handler と、それらの値・結果・例外・設定の型だけ。handler は (effect, k) を受ける関数で class で書かず、公開の名は subpackage の入口からだけ出す。強制は公開 API の目録と減るだけの既知の表を突き合わせる 1 つの test が持つ"
  :status "accepted"
  :scope ["packages/*/ (subpackage の import の根・submodule・[project.scripts]・entry point)"
          "tools/public_api_inventory.py (公開 API を数える 1 か所)"
          "tests/test_public_api_known_violations.py (強制 — card ki-aaaa56621c5a)"
          "tests/data/public_api_known_violations.json (既知の表 — 減るだけ)"
          "docs/adr/defadr_doeff_api_001_subpackages_show_effects_and_handlers.hy"]
  :problem
    [(fact
       "持ち主は 2026-10-26 に、doeff の subpackage が利用者に出す物は effect と handler で、ほかは出さないと決めた。"
       :evidence "取り組み doeff-subpackages-effects-only・持ち主の発言 ci-VJFV7TDC7NRK59STYR26GGHZV8")
     (fact
       "発端: 2026-10-26 の docs/19-agent-tutorial.md とその動画は、doeff-agents の class ScenarioAgentHandler を作って .wrap で program を包む形を見せ、doeff_agents の根は launch_session・monitor_session のような、session を手で起こして見張る手続き型の関数を出していた。そこから利用者は、effect を yield して handler を据える doeff の書き方でない使い方を覚える。"
       :evidence "commit 548f04f62(card ki-4b53dcee28f3)が doeff_agents の根から外した 69 名と、その前の版の docs/19-agent-tutorial.md")
     (fact
       "公開 API の目録(card ki-633e6b8564b4)は packages/ の下の 39 package の公開の名に種類(effect・handler・それ以外・未分類)を付け、card ki-1c2bd5bea63f がそれ以外と未分類を、外れ(直しの向きつき)と境界(種類つき)に分けた。決まりの文だけでは入るか外れるかが決まらない境界と、effect と handler を出す物でない道具の package 12 個が残った。"
       :evidence "tools/public_api_inventory.py・tests/data/public_api_known_violations.json の組の名")
     (fact
       "境界と道具の package の扱いを、依頼書 lt-6KJ8EG0YPWXCQ2GRC5XNSQVNQM(道具の package・型・class の形の handler・combinator・submodule・CLI コマンドと __version__ の 6 問)で問い、2026-10-27 に 6 問とも答えが出た。型と submodule はそのまま決まり、class の形の handler は案の外の向き(class で書かない)に決まった。道具の package・combinator・CLI コマンドと __version__ は、名ごとに具体的に見せて問い直す答えで、続きの書が名ごとに決めている。"
       :evidence "依頼書 lt-6KJ8EG0YPWXCQ2GRC5XNSQVNQM の答え lt-8V00BBVKMV2CVHB45S00A3558Z・lt-85SHMNQG7HG4AYHBER6V2BAKX6・lt-RMPE1XQ9RVWV4S8N0GTBE40Y61・lt-K1C6JM29MWVBHH9H7891JP4BG2・lt-YM5YVPMR3BAR0XR6DNEGG6PA9P・lt-8T39MYCBSHJDX310DXMB6205VS")
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
       "公開の submodule(R5 の外れ)は、card ki-25d43146681e が既知の表に載せた 39 package の 731 行(決まりの範囲の 28 package に 410・範囲の答え待ちの 11 package に 321)。決まりの範囲の import の根のすぐ下の子は 267 個で、子を _<名> に替えれば下の module も隠れる。直しの分けは card ki-4bff1f81b0f4(stage 1〜3 の 31 枚)。"
       :evidence "tests/data/public_api_known_violations.json の (submodule) の行(doeff ec6311c0a の目録)・artifact 2026-10-28-doeff-submodule-hide-split.html(doeff 37467d478138 で 733 行・第 1 層の子 265 — その後 doeff-agents と doeff-hy-pytest が submodule を減らした)")]
  :context
    [(interpretation
       "doeff の値は、handler を替えれば同じ program が test でも本番でも走る事にある。subpackage が手続き型の関数や client の class を見せると、利用者はその口を直に呼び、handler の差し替えの外で書く。effect と handler だけを見せれば、どの subpackage も effect を yield して handler を据えるだけで使える。")
     (interpretation
       "handler は (effect, k) を受けて答える(Resume)か外へ渡す(Pass)関数(@handler・defhandler — ADR-DOE-CORE-003)。handler か handler の組を作って返す関数(factory)は handler を出す物に数える。program を受けて program を返すだけの関数は handler の形でない。")
     (interpretation
       "型は effect と handler の一部: 値の型が無いと effect を作れず、結果の型が無いと答えを読めず、例外の型が無いと失敗を受けられず、設定の型が無いと handler を作れない。")
     (interpretation
       "公開の名の数え方は目録の道具の 1 か所が持つ: import の根の __init__ が出す名・path のどの部分も _ で始まらない submodule が出す名と、その submodule そのもの(R5)・[project.scripts] と entry point と maturin の bin。この ADR は数え方を別に持たない。repo の根の doeff(program・do・run・handler の核)は subpackage でなく、目録も数えない。")]
  :decision
    [(rule R1 "subpackage の公開 API は effect(EffectBase の型と、effect を作る関数)と handler(@handler・defhandler の handler と、handler か handler の組を作って返す関数)だけ。ほかの名(手続き型の関数・program 関数・client や API の class・設定を読む関数・定数)は、名か module の path に _ を付けた内側に置くか、effect か handler に作り直す。")
     (rule R2 "effect の値の型・effect の結果の型・effect と handler が投げる例外の型・handler の設定の型は、effect と handler の一部として公開してよい(2026-10-27・依頼書 lt-6KJ8EG0YPWXCQ2GRC5XNSQVNQM の問い types の答え lt-85SHMNQG7HG4AYHBER6V2BAKX6)。")
     (rule R3 "handler は (effect, k) を受けて答えるか外へ渡す関数に限る。program を受けて program を返すだけの関数を handler と数えない(2026-10-27・combinator の書 lt-6JDFVKXCP8ZG9A7QY4XSCMACRM の問い maybe の答え lt-EMXC1HCMQ9CVW07RFP0CPR3EEZ)。")
     (rule R4 "handler は class で書かない。名が Handler で終わり method で effect を受ける class の形の handler は、内側へ隠すのでなく doeff の handler(@handler・defhandler)に書き直して class を消す(2026-10-27・問い class-handler の答え lt-RMPE1XQ9RVWV4S8N0GTBE40Y61)。直しは card ki-3d948083b454(doeff-git)・ki-6b0772c6257e(doeff-agentic)・ki-6d58cb2f1a33(doeff-conductor)。")
     (rule R5 "公開の名は subpackage の import の根(__init__)からだけ出す。submodule は名に _ を付けて隠し、_ で始まらない submodule は外れと数える(2026-10-27・問い submodule の答え lt-YM5YVPMR3BAR0XR6DNEGG6PA9P)。subpackage ごとの直しの分けは card ki-4bff1f81b0f4。")
     (rule R6 "program を包む関数(combinator)は名ごとに決める(2026-10-27・問い combinator の答え lt-K1C6JM29MWVBHH9H7891JP4BG2)。doeff-core-effects の maybe と result は、今は公開の関数として許す — 決まりの例外で、後で見直す(書 lt-2GXXR6ES9CCA3RZ76KQBZ1HK03 の問い result の答え lt-ZF2RXKY95518G3280NRRP32EFS が maybe も含めて決めた)。writer_log は記録を読む effect ReadWriterLog と handler に作り直した(問い writer-log の答え lt-FVGDH8R59WSBCJ8ME5SVC4G06X・card ki-256e89367dd0)。残りの 9 個は R13 の未決。")
     (rule R7 "人が打つ CLI コマンド(doeff-agents・conductor・doeff-flow・doeff-agentic-tui)は残し、中身は subpackage の effect を出す program を handler の下で走らせるだけにする。CLI コマンドのための client と API の class は subpackage の内側に置く(2026-10-27・書 lt-6ZJK3DKWFNBK1YVNGH1DNM72FC の問い cli-user の答え lt-57AKP3A28CQN8VVWH74YH8N8S5)。直しは card ki-307f29018559・ki-1ef3718c89b3・ki-7e6ce5db22d4。")
     (rule R8 "code は CLI コマンドのサブコマンドで process を起こさない。常駐の process は役ごとの Python の module の入口に分け、起動は effect にして、handler が sys.executable -m <module> で起こす(PATH を見ない)。doeff-agents の CLI コマンド doeff-sessionhost は消した(2026-10-27・書 lt-6WY2RE7B77YNNRB3HYWXD3778N の問い sessionhost-launch の答え lt-72NHAT8F7QDV6JER8GTDACPKMB・card ki-bb337cfd0336)。")
     (rule R9 "subpackage は __version__ を持たない。版は importlib.metadata.version で読む(2026-10-27・書 lt-2GENAXPWCYF0FFHSX8Q6K5SR4V の問い version の答え lt-1YE9759TCCZ7JEWP78GV2T4H8Q・card ki-d5071356ca9b)。")
     (rule R10 "effect と handler を出す物でない道具の package 12 個は、まとめて範囲に入れも外しもせず、package ごとに決める(2026-10-27・問い infra の答え lt-8V00BBVKMV2CVHB45S00A3558Z)。決まった物 — doeff-vm: 利用者の入口は doeff の根で、doeff_vm を import するのは doeff の中だけ。根に無い handler を書く型は根に足し、数を測る・設定する関数 9 個は effect と handler にする(書 lt-FVDJW8KVXY4ZDF371898PJAM1C の答え lt-XM33P1YN6QBWX9VFMHT0T439FA・card ki-ff728b00369a・ki-27e8ea225b62)。doeff-hy の macro: effect・handler・program を書く群(defeffect・defhandler・defk・<- ほか 14)と deftest と土台の群(defrecord・val・var・deff ほか 10)は見せる。値に替える群(absent-as・on-raise・check・validate)は R6 と R13 の答えに合わせる。conductor の workflow の macro 13 個と defsystem は doeff-conductor と doeff-cluster へ移す(書 lt-ZR6B2AN9DE1DN8534M35JQBCJJ の答え lt-GXXY5TVWBHJW4ZCBDTCRN68XZS・card ki-f2107259b141)。残りの 10 package は R13 の未決。")
     (rule R11 "範囲は packages/ の下の package の全部。既知の表の範囲「決まりの範囲」の 27 package には今から当てる。「範囲の答え待ち」の 12 package は、R10 の答えが出た物(doeff-vm・doeff-hy)から表の範囲と組を答えに合わせて改める(改める card は持ち主が切る)。doeff-hy は R14 で「決まりの範囲」へ移した。")
     (rule R12 "強制は 1 か所: tests/test_public_api_known_violations.py が tools/public_api_inventory.py の目録を package ごとの既知の表と突き合わせる(card ki-aaaa56621c5a)。表に無い外れが在れば赤・表に在るのに目録に無い名が在れば赤・表の「名」の組は減るだけ。足すしか無い外れは「足した名」に訳と決め(card・依頼・発言の id)を 1 行ずつ書く。この ADR は別の検を作らない。R2 の型・R6 の maybe と result・R7 の CLI コマンドのように許した物も、目録の道具が許す物と数えるまでは既知の表の境界の組に残し、隠して直さない。")
     (rule R13 "未決(答えが来たら持ち主がこの ADR に条を足し、既知の表の範囲と組を改める card を切る): (a) combinator の残り 9 個 — doeff-core-effects の on_raise・absent_as・scheduled・closing_links、doeff-traverse の sequence・try_call、doeff-validation の check・perform・validate(書 lt-2GXXR6ES9CCA3RZ76KQBZ1HK03 の答え待ち)。(b) 道具の package 10 個 — doeff-hy-pytest(書 lt-Y7V79JHX361ASZENVT1HM9V2ND の答え待ち)と、まだ問うていない doeff-vm-core・doeff-adr・doeff-linter・doeff-indexer・doeff-effect-analyzer・doeff-test-target・doeff-agentic-cli・doeff-domain・doeff-cluster(doeff-cluster の書は持ち主が出す — card ki-2526a4b4f561)。(c) 問いのまだ無い境界 — doeff-conductor の workflow の DSL の言葉(doeff_conductor.dsl の 66 行)・doeff-hy の外の Hy の macro 2 個・doeff-hy の CLI doeff-hy-check と pytest の plugin の module・未分けの 16 行。")
     (rule R14 "doeff-hy は範囲「決まりの範囲」に入れる(2026-10-29・R10 の答え lt-GXXY5TVWBHJW4ZCBDTCRN68XZS の macro の外を、持ち主の段取りで R1・R2・R5 のとおりと決めた — card ki-2526a4b4f561)。macro は R10 の群のとおり doeff_hy.macros から見せる — Hy の require が module の path で macro を読むので、この submodule は R5 の外の境界に置く。macro の外の名は R1・R2・R5 のとおり: R2 の型は入口から出してよく、残りは内側の module へ移し、doeff_hy と doeff_hy_bytecode_guard の下の submodule 52 個は名に _ を付けて隠す。隠した後は、submodule に足した名が既定で公開にならない。直しの分けは card ki-2526a4b4f561 の子。")]
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
       :wiring "配線済み(2026-10-26・card ki-aaaa56621c5a)— package ごとの 39 件・使い捨ての package の赤と緑・表の形・減るだけ。この file の deftest はその関数を呼ぶだけ")
     (law a-handler-takes-an-effect-and-its-continuation
       :statement "handler(h) <=> h answers (effect, k) or builds such h; takes(f, Program) and returns(f, Program) and not marked(f) => not handler(f); class c named *Handler => not handler(c)"
       :counterexamples
         [(counterexample "maybe を、program を受けて program を返すから handler と数えて許す")
          (counterexample "名が Handler で終わる class を、method で effect を受けるから handler と数えて公開する")]
       :enforced-by ["tests/test_public_api_inventory.py::test_names_are_typed_by_marker_return_type_and_return_shape"
                     "tests/test_public_api_known_violations.py::test_public_api_outside_effects_and_handlers_is_in_the_known_table"]
       :wiring "一部配線(2026-10-28)— 目録は印の無い Handler の名の class を未分類、(effect, k) の関数を handler と読み、既知の表に無ければ赤。目録が「戻りの型が Program -> Program の callable の関数」を handler と読む所は R3 と照らし直していない(持ち主が card を切る)")
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
       :wiring "配線済み(2026-10-28・card ki-25d43146681e)— 目録は _ で始まらない submodule を 1 行(名を出す module の欄が (submodule))の外れと数え、既知の表は今の公開の submodule 731 行を載せて、submodule の行を足した版から減るだけ。名を submodule から入口へ移した行は表の module を書き換えずに同じ行と読む(submodule がまだ同じ名を出す間は定義の場所が同じ時だけ・古い口を閉じた後は名と根だけ)。1 行目に印 # moved-submodule-shim: <古い名> -> <新しい名> の在る移し替えの module(card ki-299be2ea4a13 の道具が置く)は、移した先の package の下の module も古い名で数えるので、stage 1 は表を書き換えずに緑。隠した submodule の行は表から消す。この file の deftest はその関数を呼ぶだけ")]
  :enforcement
    [(deftest test-adr-doe-api-001-an-added-outlier-turns-the-guard-red
       ;; R12: 強制は card ki-aaaa56621c5a の test が持ち、ここではその関数を呼ぶだけ — 使い捨ての package に effect と handler の外の
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
         (guard.test-a-different-definition-with-a-known-name-at-the-root-turns-red (/ (Path scratch) "different"))))]
  :plans ["tests/test_public_api_known_violations.py・tests/data/public_api_known_violations.json (card ki-aaaa56621c5a)"
          "tools/public_api_inventory.py (card ki-633e6b8564b4)"
          "取り組み doeff-subpackages-effects-only の区切り 2〜4 の card (直し — doeff-agents・CLI コマンド・doeff-vm・doeff-hy の macro・writer_log・__version__・class の handler・submodule)"
          "R11・R13 の答えが来た後: 既知の表の範囲と組を答えに合わせる card、この ADR に条を足す card (持ち主が切る)"])
