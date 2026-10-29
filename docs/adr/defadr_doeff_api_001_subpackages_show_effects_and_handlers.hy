;;; Executable ADR: doeff の subpackage(packages/ の下の package)が利用者に見せる公開 API は、effect と handler と handler を返す工場と、
;;; それらを作り・答えを読み・失敗を受け・handler を組むのに要る型と、名ごとに許した program の helper(effect を出すだけの program を
;;; 返す関数 — R18)だけ。ほかの名は、名か module の path に _ を付けた内側に置くか、effect か handler か工場に作り直す。handler は
;;; 印 _doeff_is_handler_fn の付いた callable の値(中核の ProgramHandler)で、program に当てる公式の形は h1(h2(p))。
;;;
;;; 出自 = 持ち主の決定 2026-10-26(取り組み doeff-subpackages-effects-only・発言 ci-VJFV7TDC7NRK59STYR26GGHZV8・card ki-bd3bec7b1360)。
;;; 範囲と境界の扱いは、依頼書 lt-6KJ8EG0YPWXCQ2GRC5XNSQVNQM(6 問)と、名ごとに問い直した続きの書の答え(どれも 2026-10-27)に従う
;;; (:decision の各条に答えの id を書く)。handler の形と当て方・工場は持ち主の決定 2026-10-28(発言 ci-TDTZ23GS70F1VFM0DV183G9VQQ〜
;;; ci-AFX0HEJAT7SAMANA3YCPKE9RJA・card ki-25eecd63bc57)に従い、R3・R6 をそれで改めた。program の helper は持ち主の答え 2026-10-29
;;; (lt-R3TYRMMEM7YN0G9J9D2RZMVX3X・card ki-f20b18c2aad9)に従い R18 に足した。doeff-validation の check と perform を型にする事は持ち主の
;;; 答え 2026-10-29(lt-MHFWEYBQYCC2PQYR84VKXBAET8・card ki-2750cc5e5209)に従い R19 に足した。まだ問うていない道具の package 7 個のうち、
;;; 答えの出た条で決まる doeff-vm-core と doeff-linter を R21 に足した(card ki-4c1d18a98579)。答えの無い物は R13 の「未決」に並べ、答えが来たら
;;; 持ち主がこの ADR に条を足す。
;;;
;;; 強制は card ki-aaaa56621c5a の test(tests/test_public_api_known_violations.py)が持つ。公開 API を数える 1 か所
;;; (tools/public_api_inventory.py)の目録を、package ごとの既知の表(tests/data/public_api_known_violations.json)と突き合わせ、表は
;;; 減るだけ。工場と読めた名は目録の道具が呼んで印を確かめ、印の無い値を返す名を外れと数える(R3)。表の組「許した物 — program の
;;; helper」の名は、目録の道具が中を読んで helper の形(R18)と確かめた物だけを置ける。この ADR は別の検を作らない —
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
  "card ki-aaaa56621c5a の test の module を読む(tests/ は package でない — pytest が集める物と混ぜないよう別の名で読む)。"
  (val spec (importlib.util.spec-from-file-location "_adr_doe_api_001_guard" (/ ROOT GUARD-TEST)))
  (val module (importlib.util.module-from-spec spec))
  (.exec-module spec.loader module)
  module)


(defadr ADR-DOE-API-001
  :title "doeff の subpackage が利用者に見せる公開 API は effect と handler と handler を返す工場と、それらの値・結果・例外・設定の型と、名ごとに許した program の helper(effect を出すだけの program を返す関数)だけ。handler は印の付いた callable の値(ProgramHandler)で、program には h1(h2(p)) で当て、class で書かず、公開の名は subpackage の入口からだけ出す。強制は公開 API の目録と減るだけの既知の表を突き合わせる 1 つの test が持ち、工場は呼んで handler が返る事を、helper は中を読んで handler を被せず effect に答えない事を見る"
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
       :evidence "tests/data/public_api_known_violations.json の (submodule) の行(doeff ec6311c0a の目録)・artifact 2026-10-28-doeff-submodule-hide-split.html(doeff 37467d478138 で 733 行・第 1 層の子 265 — その後 doeff-agents と doeff-hy-pytest が submodule を減らした)")
     (fact
       "持ち主は 2026-10-28 に、handler は印の付いた callable の値(中核の ProgramHandler)で、program に当てる公式の形は h1(h2(p))、WithHandler は中核の生の形で利用者向けの docs に出さない、一度きりの handler は Hy の handle の macro・使い回す handler は defhandler か handler を返す工場に名を付ける、doeff-core-effects の maybe・result・on_raise・absent_as・scheduled は中核の state() と同じ handler を返す工場として扱う、と決めた。subpackage が見せてよい物は effect と handler と handler を返す工場になり、工場は呼ぶと handler が返る事を構造(印)で確かめる。"
       :evidence "持ち主の発言 ci-TDTZ23GS70F1VFM0DV183G9VQQ〜ci-AFX0HEJAT7SAMANA3YCPKE9RJA(2026-10-28)・card ki-bd3bec7b1360 の発言・card ki-25eecd63bc57")
     (fact
       "目録(doeff 19e34510931f の木・2026-10-29)は、戻りの型か return の形で handler を返す工場と読める定義を 39 package に 225 個(公開の行で 435)数えた。最小の引数で呼ぶと、202 個(引数なし 69・置き物 133)が印の付いた handler か handler の組を返し、21 個は呼ぶと例外(置き物を呼ぶ時に読む物・tmux の無い機体で tmux を探す doeff-agentic の tmux_handler など)、2 個 — doeff-conductor の make_scheduled_handler・make_offloaded_scheduled_handler — は印の無い (effect, k) の @do 関数(handler の body)を返した。maybe・result・on_raise・absent_as・scheduled は戻りの型が program(DoExpr・Program)なので工場と読まれず、引数なしで呼ぶと program が足りず、program を渡すと印の無い program を返す。"
       :evidence "tools/public_api_inventory.py の目録の行の理由(工場の行の末尾に、どう呼び・何が返ったか)・tests/data/public_api_known_violations.json の組「外れ — handler を返す工場の形に直す」と「足した名」の card ki-25eecd63bc57 の 6 行")
     (fact
       "持ち主は 2026-10-29 17:16 JST に、effect でも handler でもない program の helper — effect を出すだけの program を返す関数 — を見せてよいと決め、doeff-traverse の sequence を今のまま見せるとした。問いは、sequence を隠すか許すか(隠す向きでは doeff-cluster の 5 か所を for の loop に書き替える)だった。"
       :evidence "依頼書 lt-BPA7EJHY29RNHXHVEF7SPKPY81 の問い sequence の答え lt-R3TYRMMEM7YN0G9J9D2RZMVX3X・card ki-bd3bec7b1360 の発言・card ki-f20b18c2aad9")
     (fact
       "目録(doeff c344561a81e5 の木に R18 の判定を足した道具・2026-10-29)は、program を返す関数の公開の行 3,538 のうち 3,441 行(名 2,539)を helper の形(中で handler を被せず、effect に答えない)と読み、93 行を handler を被せる物、4 行を effect に答える物と読んだ。helper の形の行の多くは既知の表の外れの組に在る — 内側の module へ移す 2,163・handler の中へ入れる 1,024・effect にする 91。"
       :evidence "tools/public_api_inventory.py の目録の行の形と理由(helper を越える行の理由の末尾に、どの関数がどの名を読むか)・tests/data/public_api_known_violations.json の組")
     (fact
       "目録(doeff 6675fb4c72c1 の木・make sync の環境・2026-10-29)は、まだ問うていない道具の package 7 個の公開の行を数えた — doeff-vm-core 0(Python の module が無い)・doeff-linter 1(Rust の実行 file)・doeff-indexer 1(console script)・doeff-agentic-cli 1(Rust の実行 file)・doeff-effect-analyzer 64(submodule 6・console script 1・名 57)・doeff-domain 99(submodule 5・名 94)・doeff-test-target 180(submodule 29・名 151)。doeff-indexer と doeff-effect-analyzer はこの環境に入らず、目録は source の字面で読む — doeff-indexer の根が Rust の拡張から出す Indexer・SymbolInfo(doeff の根の doeff/cli/discovery.py が読む)は数に入らない。"
       :evidence "tools/public_api_inventory.py --package <dir> の目録の行(card ki-4c1d18a98579 の発言に名ごとの表)・packages/doeff-indexer/python/doeff_indexer/__init__.py・packages/doeff-indexer/src/python_api.rs")]
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
    [(rule R1 "subpackage の公開 API は effect(EffectBase の型と、effect を作る関数)と handler(@handler・defhandler・stacked_handlers の、印の付いた値)と handler を返す工場(呼ぶと handler か handler の組を返す関数 — R3)と、名ごとに許した program の helper(R18)だけ(2026-10-26 の決まりを、2026-10-28 の決定 ci-TDTZ23GS70F1VFM0DV183G9VQQ〜ci-AFX0HEJAT7SAMANA3YCPKE9RJA が工場まで、2026-10-29 の答え lt-R3TYRMMEM7YN0G9J9D2RZMVX3X が helper まで書き表した)。ほかの名(手続き型の関数・R18 で許していない program 関数・client や API の class・設定を読む関数・定数)は、名か module の path に _ を付けた内側に置くか、effect か handler か工場に作り直す。")
     (rule R2 "effect の値の型・effect の結果の型・effect と handler が投げる例外の型・handler の設定の型は、effect と handler の一部として公開してよい(2026-10-27・依頼書 lt-6KJ8EG0YPWXCQ2GRC5XNSQVNQM の問い types の答え lt-85SHMNQG7HG4AYHBER6V2BAKX6)。")
     (rule R3 "handler は印 _doeff_is_handler_fn の付いた callable の値(中核の ProgramHandler)で、中身は (effect, k) を受けて答えるか外へ渡す body(2026-10-28 の決定 ci-TDTZ23GS70F1VFM0DV183G9VQQ〜ci-AFX0HEJAT7SAMANA3YCPKE9RJA)。handler を返す工場は、引数なしか最小の引数で呼ぶとその値(か、その値の list か tuple)を返す関数。印の無い関数は、program を受けて program を返しても handler と数えず、戻りの型を ProgramHandler と書いても呼んで印の無い値を返す関数は工場と数えない。目録の道具は工場と読めた名を最小の引数(既定値の無い引数ごとに中身の無い置き物)で呼んで印を確かめ、印の無い値を返す名を外れ(handler を返さない関数)と数える。呼ぶと例外の出る工場(置き物を呼ぶ時に読む物・機体に無い命令を探す物)は戻りの型の読みのまま。改めた前の形: 2026-10-27 の「handler は (effect, k) を受けて答える関数に限り、program を受けて program を返すだけの関数を handler と数えない」(combinator の書 lt-6JDFVKXCP8ZG9A7QY4XSCMACRM の問い maybe の答え lt-EMXC1HCMQ9CVW07RFP0CPR3EEZ)— 決定 (1) が handler を ProgramHandler の値と決めたので、形(program → program)でなく印で数える形に改めた。印の無い program → program の関数を handler と数えない事は変わらない。")
     (rule R4 "handler は class で書かない。名が Handler で終わり method で effect を受ける class の形の handler は、内側へ隠すのでなく doeff の handler(@handler・defhandler)に書き直して class を消す(2026-10-27・問い class-handler の答え lt-RMPE1XQ9RVWV4S8N0GTBE40Y61)。直しは card ki-3d948083b454(doeff-git)・ki-6b0772c6257e(doeff-agentic)・ki-6d58cb2f1a33(doeff-conductor)。")
     (rule R5 "公開の名は subpackage の import の根(__init__)からだけ出す。submodule は名に _ を付けて隠し、_ で始まらない submodule は外れと数える(2026-10-27・問い submodule の答え lt-YM5YVPMR3BAR0XR6DNEGG6PA9P)。subpackage ごとの直しの分けは card ki-4bff1f81b0f4。")
     (rule R6 "program を包む関数(combinator)は名ごとに決める(2026-10-27・問い combinator の答え lt-K1C6JM29MWVBHH9H7891JP4BG2)。doeff-core-effects の maybe・result・on_raise・absent_as・scheduled は、中核の state() と同じ handler を返す工場として扱う(2026-10-28 の決定 ci-TDTZ23GS70F1VFM0DV183G9VQQ〜ci-AFX0HEJAT7SAMANA3YCPKE9RJA)— 向きは maybe()(p)・on_raise(受け…)(p) のように、呼ぶと handler を返し、program はその handler に渡す形。maybe・result・on_raise・absent_as は工場の形に直した(2026-10-29・card ki-d27fd0acfe07 — 呼ぶと印の付いた handler を返し、doeff-hy の on-raise・absent-as の展開先もこの形)。4 つの旧い形 maybe(p)・result(p)・on_raise(p, 受け…)・absent_as(既定値, p) は新しい形と同じ答えの移し替えの層として残し、doeff の main を読む repo の呼び手を書き換えたら(card ki-2caf0f53603a)消す — 消すまでは 4 名を既知の表の組「外れ — handler を返す工場の形に直す」に残す。scheduled は今の形(program を受けて program を返す — 引数なしでは呼べず、返す値に印が無い)が工場の構造に合わないので、形を変えるまで既知の表の組「外れ — handler を返す工場の形に直す」に残す(card ki-25eecd63bc57)。改めた前の形: 2026-10-27 の「maybe と result は今は公開の関数として許す決まりの例外」(書 lt-2GXXR6ES9CCA3RZ76KQBZ1HK03 の問い result の答え lt-ZF2RXKY95518G3280NRRP32EFS)— 決定 (3) が 5 つを工場と決めたので、例外を外して工場の側へ移した。writer_log は記録を読む effect ReadWriterLog と handler に作り直した(問い writer-log の答え lt-FVGDH8R59WSBCJ8ME5SVC4G06X・card ki-256e89367dd0)。残りの 6 個のうち closing_links・sequence・try_call・validate は R18 の program の helper として許し、check・perform の 2 個は型 Check・Perform にして関数を消した(R19)。")
     (rule R7 "人が打つ CLI コマンド(doeff-agents・conductor・doeff-flow・doeff-agentic-tui)は残し、中身は subpackage の effect を出す program を handler の下で走らせるだけにする。CLI コマンドのための client と API の class は subpackage の内側に置く(2026-10-27・書 lt-6ZJK3DKWFNBK1YVNGH1DNM72FC の問い cli-user の答え lt-57AKP3A28CQN8VVWH74YH8N8S5)。直しは card ki-307f29018559・ki-1ef3718c89b3・ki-7e6ce5db22d4。")
     (rule R8 "code は CLI コマンドのサブコマンドで process を起こさない。常駐の process は役ごとの Python の module の入口に分け、起動は effect にして、handler が sys.executable -m <module> で起こす(PATH を見ない)。doeff-agents の CLI コマンド doeff-sessionhost は消した(2026-10-27・書 lt-6WY2RE7B77YNNRB3HYWXD3778N の問い sessionhost-launch の答え lt-72NHAT8F7QDV6JER8GTDACPKMB・card ki-bb337cfd0336)。")
     (rule R9 "subpackage は __version__ を持たない。版は importlib.metadata.version で読む(2026-10-27・書 lt-2GENAXPWCYF0FFHSX8Q6K5SR4V の問い version の答え lt-1YE9759TCCZ7JEWP78GV2T4H8Q・card ki-d5071356ca9b)。")
     (rule R10 "effect と handler を出す物でない道具の package 12 個は、まとめて範囲に入れも外しもせず、package ごとに決める(2026-10-27・問い infra の答え lt-8V00BBVKMV2CVHB45S00A3558Z)。決まった物 — doeff-vm: 利用者の入口は doeff の根で、doeff_vm を import するのは doeff の中だけ。根に無い handler を書く型は根に足し、数を測る・設定する関数 9 個は effect と handler にする(書 lt-FVDJW8KVXY4ZDF371898PJAM1C の答え lt-XM33P1YN6QBWX9VFMHT0T439FA・card ki-ff728b00369a・ki-27e8ea225b62)。doeff-hy の macro: effect・handler・program を書く群(defeffect・defhandler・defk・<- ほか 14)と deftest と土台の群(defrecord・val・var・deff ほか 10)は見せる。値に替える群は、absent-as・on-raise を R6 の工場に、check・validate を R13 の答えに合わせる。conductor の workflow の macro 13 個と defsystem は doeff-conductor と doeff-cluster へ移す(書 lt-ZR6B2AN9DE1DN8534M35JQBCJJ の答え lt-GXXY5TVWBHJW4ZCBDTCRN68XZS・card ki-f2107259b141)。doeff-hy-pytest: 見せる名は pytest11 の plugin の登録と、test ごとの宣言 2 個(declare_step_budget・declare_process_tree_cpu)の 3 個に絞る。測りの中の class と関数は内側へ移し、外の repo は plugin が出す要約の行を読む形に直す(書 lt-Y7V79JHX361ASZENVT1HM9V2ND の答え lt-796ZF4G6CQ6TSHS5JDDRP09P1K・2026-10-28 — 直しの card は持ち主が切る)。doeff-cluster は R15。doeff-vm-core と doeff-linter は R21。残りの 6 package は R13 の未決。")
     (rule R11 "範囲は packages/ の下の package の全部。既知の表の範囲「決まりの範囲」の 27 package には今から当てる。「範囲の答え待ち」の 12 package は、R10 の答えが出た物(doeff-vm・doeff-hy・doeff-hy-pytest)から表の範囲と組を答えに合わせて改める(改める card は持ち主が切る)。doeff-hy は R14 で、doeff-cluster は R15 で、doeff-vm-core と doeff-linter は R21 で「決まりの範囲」へ移した。")
     (rule R12 "強制は 1 か所: tests/test_public_api_known_violations.py が tools/public_api_inventory.py の目録を package ごとの既知の表と突き合わせる(card ki-aaaa56621c5a)。表に無い外れが在れば赤・表に在るのに目録に無い名が在れば赤・表の「名」の組は減るだけ。足すしか無い外れは「足した名」に訳と決め(card・依頼・発言の id)を 1 行ずつ書く。R3 の工場の確かめ(呼んで印を見る)も目録の道具の中に置き、呼んで印の無い値を返す名は同じ照合で外れになる(card ki-25eecd63bc57)。R18 の helper の確かめ(中を読む)も目録の道具が持ち、表の組「許した物 — program の helper」の名の形が helper でなければ同じ test が赤にする。許した物の組は、目録で形を確かめる組だけを置ける(card ki-f20b18c2aad9)。この ADR は別の検を作らない。R2 の型・R7 の CLI コマンドのように許した物も、目録の道具が許す物と数えるまでは既知の表の境界の組に残し、隠して直さない。")
     (rule R13 "未決(答えが来たら持ち主がこの ADR に条を足し、既知の表の範囲と組を改める card を切る): (a) なし — combinator の残りだった doeff-validation の check・perform は R19 で決めた(closing_links・sequence・try_call・validate は R18)。(b) 道具の package 6 個 — doeff-adr(書 lt-RD6GWB97WRVMESGDWCHHZDZJW4 は差し戻され、1 通 1 問で問い直している。get_adr は R20 で決めた。adr_ids・register_semgrep_enforcement・assert_semgrep_enforcement は別の書の答え待ち)と、まだ問うていない doeff-indexer・doeff-effect-analyzer・doeff-test-target・doeff-agentic-cli・doeff-domain(doeff-cluster は R15・doeff-hy-pytest は R10・doeff-vm-core と doeff-linter は R21 で決めた)。5 個の名ごとの分け — 答えの出た条で決まる名と問いの要る名、用途・使い方・呼び手の数 — は card ki-4c1d18a98579 の発言に在る。(c) 問いのまだ無い境界 — doeff-conductor の workflow の DSL の言葉(doeff_conductor._dsl の 66 行)・doeff-hy の外の Hy の macro 2 個・doeff-hy の CLI doeff-hy-check と pytest の plugin の module・未分けの 16 行。(d) 外れの組に在る、helper の形の program 関数(2026-10-29 の目録で 3,278 行 — 内側の module へ移す 2,163・handler の中へ入れる 1,024・effect にする 91)を R18 で許すか — R18 は名ごとに許すので、これらは外れのまま。許す名は持ち主が組ごとか名ごとに問う。")
     (rule R14 "doeff-hy は範囲「決まりの範囲」に入れる(2026-10-29・R10 の答え lt-GXXY5TVWBHJW4ZCBDTCRN68XZS の macro の外を、持ち主の段取りで R1・R2・R5 のとおりと決めた — card ki-2526a4b4f561)。macro は R10 の群のとおり、macro を出す 3 つの submodule(doeff_hy.macros・doeff_hy.record・doeff_hy.handle)から見せる — Hy の require が module の path で macro を読むので、この 3 つは R5 の外の境界に置く。macro の外の名は R1・R2・R5 のとおり: R2 の型は入口から出してよく、残りは内側の module へ移し、doeff_hy と doeff_hy_bytecode_guard の下の残りの submodule 50 個は名に _ を付けて隠す。隠した後は、submodule に足した名が既定で公開にならない。直しの分けは card ki-2526a4b4f561 の子。")
     (rule R15 "doeff-cluster は範囲「決まりの範囲」に入れる(2026-10-29・書 lt-3GGRA8T615V785SZ65CZ6JGZ1D の問い cluster-scope の答え lt-G256RJK3A95G4N7R5XC5FEJBVP — hide・card ki-2526a4b4f561・ki-362df4037544)。名は R1・R2・R5 のとおり: 外の repo が使う名(書の数え — 公開の名 3,443 個のうち 423 個)だけを入口から見せ、そのうち effect にする名と handler の中へ入れる名は後の card で直す。外が使わない名は内側の module へ移す。入口は R5 のとおり import の根 doeff_cluster に置く — 今の doeff_cluster は根の __init__ を持たない Hy の namespace package なので、card ki-299be2ea4a13 の道具が根に遅延の import の入口を作り、根のすぐ下の区画(coordinator・foundation・record_store・shared・sim・worker)を _<区画> へ移して、下の submodule(2026-10-29 の目録で 217 個)をまとめて隠す。書の「選んだ後の code」は区画の path(doeff_cluster.sim)から読む形を見せたが、R5 と目録の道具と表の test は import の根から出す名で数えるので、使い手の import は (import doeff_cluster [sim-cluster StartWorker]) になる。macro defsystem(R10)は根のすぐ下の公開の module doeff_cluster.macros から見せ、R5 の外の境界に置く — Hy の require が module の path で macro を読むので(R14 と同じ訳)。今の doeff_cluster.shared.entry.macros から移すのは shared の区画の段 1。隠した後は、submodule に足した名が既定で公開にならない。直しの分けは card ki-2526a4b4f561 の子(区画ごとの段 1・使い手の repo の段 2・移し替えの module と表の行を消す段 3)。")
     (rule R16 "handler を program に当てる公式の形は h1(h2(p)) — handler の値を呼んで包む(2026-10-28 の決定 ci-TDTZ23GS70F1VFM0DV183G9VQQ〜ci-AFX0HEJAT7SAMANA3YCPKE9RJA)。中核の WithHandler(handler, program)は VM へ渡す生の形で、利用者向けの docs と例には出さない。今の docs の WithHandler の書き替えは持ち主が card を切る。")
     (rule R17 "その場で一度だけ当てる handler は Hy の handle の macro(match のように effect ごとの節を並べてその場で当てる — doeff_hy.handle・card ki-fb1c0fb41ff5)で書き、使い回す handler は defhandler か handler を返す工場に名を付けて出す(2026-10-28 の決定 ci-TDTZ23GS70F1VFM0DV183G9VQQ〜ci-AFX0HEJAT7SAMANA3YCPKE9RJA)。subpackage が見せるのは名を付けた方(R1)。")
     (rule R18 "effect を出すだけの program を返す関数(program の helper)は見せてよい(2026-10-29 17:16 JST・依頼書 lt-BPA7EJHY29RNHXHVEF7SPKPY81 の問い sequence の答え lt-R3TYRMMEM7YN0G9J9D2RZMVX3X)。helper と数えるのは次の 3 つを満たす関数: (1) program を返す — @do・defk の関数か、戻りの型が program(Program・DoExpr ほか)の関数。(2) 自分で effect を受けて答えない — 中で handler の制御(Resume・ResumeThrow・Transfer・TransferThrow・Pass)を読まない。(effect, k) を受ける関数は R3 の handler で、helper でない。(3) handler を被せない — 中で WithHandler・with_handlers も、handler の値・作り手(handler・stacked_handlers)・handler を返す工場も読まない。中は、その関数と、中で読む同じ package の関数を辿った全部で見る(呼ぶ program の中で例外を受けて投げ直すのは、effect に答える事でないので helper のまま)。中を読めない関数(Python の関数でない物)は helper と数えない。目録の読みは中の名を global の値か属性の名で見て、同じ package の関数だけを辿るので、引数で受けた handler を当てる形・module の容器や class と instance の属性から読んだ handler を当てる形・別の package の関数が被せる形は見えず、helper と読む。だから helper は名ごとに許し、許す名の中は人が読む。helper は既知の表の組「許した物 — program の helper」に名ごとに置き(表を足した後に見せる helper は「足した名」に欄「組」をその組の名にして書く)、目録の道具がその名の形を確かめて、helper の形でなければ赤にする(R12)。形が helper でも、組に置いていない program 関数は外れのまま(R13 (d))。今許す名: doeff-traverse の sequence・try_call(答えのとおり)と、同じ形の doeff-core-effects の closing_links・doeff-validation の validate(card ki-f20b18c2aad9 が同じ条で扱った)。check と perform は program を返さないので helper でなく、R19 で型にした。")
     (rule R19 "doeff-validation の check と perform は関数でなく型にする: 検査の指定は型 Check(旧 CheckSpec)、効果の実行の印は型 Perform(旧 Performed)で、利用者は Check(述語, 引数…, reason=…)・Perform(program) と型を直に書く(2026-10-29 17:33 JST・依頼書 lt-AXDTX0Z4ZJTG2Z53AHJ9Y5HTWB の問い check-perform の答え lt-MHFWEYBQYCC2PQYR84VKXBAET8 — 型にする・card ki-2750cc5e5209)。述語が callable か・Perform の値が program か effect かの確かめと、式の字面の作りは型の作り方が持ち(どちらも外れれば作る時に TypeError)、関数 check と perform は公開の名から消した。2 つは validate の項目の値の型として R2 の型に数え、R12 のとおり、目録の道具が R2 の型を許す物と数えるまでは既知の表の「足した名」に置く。Hy の利用者は macro の check を書き、その展開も型 Check を作る(各引数は評価を項目の中へ遅らせる thunk つきの CheckArgument で渡し、Check はそれをそのまま使う)。")
     (rule R20 "doeff-adr の get_adr(登録簿の ADR を id で引く関数)は、effect GetAdr と、登録簿から答える handler を返す工場 adr_registry にした(2026-10-29 17:34 JST・依頼書 lt-TNNNQAXTY6XY4RAKWDDFX62NSM の問い get-adr の答え lt-82V6XBGCCR214RCF3SRKSZGTP5 — effect にする・card ki-24a2bca4339f)。登録簿を引く本体は内側の _get_adr に置いた。公開の関数 get_adr は R6 と同じ順で、新しい形と同じ答えの移し替えの層として残し、doeff の main を読む repo の呼び手を GetAdr へ書き換えたら(card ki-f2d54a67497b — agora-controllers は同じ変更で doeff の pin を上げる)消す — 消すまでは既知の表の 2 行(doeff_adr と doeff_adr.registry)に残す(消しは card ki-24a2bca4339f から分けた後の card・構成のレビューの所見 — 消す名の使い手を先に直す)。adr_registry() は process の登録簿を答える時に読み、adr_registry(specs=[AdrSpec …]) は渡した列だけから答える(列に無い id は外の登録簿へ落ちずに KeyError)。内側に据えた方が先に答えるので、test は外の既定の handler の下で別の表に差し替えられる。deftest の下に据えるのは、doeff-adr が handler の組を選ばない決まり(doeff-adr の README)のまま、project の doeff_interpreter fixture — doeff は docs/adr/conftest.py が adr_registry() を据える。外の repo(agora-controllers・agora・custody)の呼び手の書き換えは card ki-f2d54a67497b。")
     (rule R21 "doeff-vm-core と doeff-linter は範囲「決まりの範囲」に入れる(2026-10-29・答えの出た条 R1・R7 で決まる — 問いを出さずに決めた・card ki-4c1d18a98579)。doeff-vm-core は Python から import できる module を持たない Rust の crate(doeff-vm の中身)で、公開の名は 0 — 直す名は無く、範囲に入れれば Python の面を足した時から R1・R5 が当たる。doeff-linter は Python の module を持たず、公開の名は Rust の実行 file doeff-linter の 1 個だけ。人が打ち、commit の hook が呼ぶ命令なので、R7 のとおり残し、既知の表の組「境界 — CLI」に置く(R12)。R7 の「中身は subpackage の effect を出す program を handler の下で走らせるだけ」は、CLI コマンドが subpackage の effect と handler を迂回して状態を読み書きする別の口にならないための条。doeff-linter は effect も handler も持たない静的な検査の道具で、迂回する相手が無いので、Rust の実行 file を doeff の program に書き替える直しは無い(doeff-agentic-cli は doeff-agentic の effect と handler が答える記録を Rust から直に読むので、同じ読みでは決まらず R13 (b) に残す)。R7 の書 lt-6ZJK3DKWFNBK1YVNGH1DNM72FC は前提で道具の package 12 個の CLI コマンドを別の書に回していたので、R7 の答えの文は doeff-linter を決めていない — R21 は card ki-4c1d18a98579 の頼み(答えの出た条に R7 を挙げた)に依り、R7 の目的で決めた延長で、持ち主が覆せる。")]
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
     (law a-handler-is-a-marked-value-and-a-factory-returns-one
       :statement "handler(h) <=> marked(h) (h: ProgramHandler); factory(f) <=> for the arguments f needs at least (none when every parameter has a default): marked(f()) or f() is a list or tuple of marked values; counted_as_factory(f) and callable_without_arguments(f) and not factory(f) => outside(f); takes(f, Program) and returns(f, Program) and not marked(f) => not handler(f); class c named *Handler => not handler(c)"
       :counterexamples
         [(counterexample "2026-10-29 より前の maybe(p) のように program を受けて program を返す印の無い関数を、形が program -> program だから handler と数えて許す")
          (counterexample "戻りの型を ProgramHandler と書いた関数が、呼ぶと program を素通しする印の無い関数を返すのに、戻りの型だけで handler を返す工場と数えられて表の外で通る")
          (counterexample "名が Handler で終わる class を、method で effect を受けるから handler と数えて公開する")]
       :enforced-by ["tests/test_public_api_inventory.py::test_names_are_typed_by_marker_return_type_and_return_shape"
                     "tests/test_public_api_inventory.py::test_a_factory_says_whether_its_call_was_checked"
                     "tests/test_public_api_inventory.py::test_a_factory_is_called_with_home_and_cwd_in_a_scratch_dir"
                     "tests/test_public_api_known_violations.py::test_a_factory_whose_call_is_not_a_handler_turns_red"
                     "tests/test_public_api_known_violations.py::test_public_api_outside_effects_and_handlers_is_in_the_known_table"
                     "docs/adr/defadr_doeff_api_001_subpackages_show_effects_and_handlers.hy::test-adr-doe-api-001-a-factory-whose-call-is-not-a-handler-turns-the-guard-red"]
       :wiring "一部配線(2026-10-29・card ki-25eecd63bc57)— 目録は印で handler を数え、印の無い Handler の名の class を未分類、(effect, k) の関数を handler と読む。戻りの型か return の形で工場と読めた関数は、最小の引数(無いか、既定値の無い引数ごとに置き物)で呼んで(HOME・XDG の dir・cwd を一時の dir へ向けて)印を確かめ、印の無い値を返せば外れ「handler を返さない関数」と数えるので、既知の表に無ければ赤。呼ぶと例外の出る工場は戻りの型の読みのまま(行の理由に例外を書く — 2026-10-29 の目録で 225 個のうち 21 個)。import の時に module が決めた書き先は一時の dir へ向かない。この file の deftest はその関数を呼ぶだけ")
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
       :wiring "配線済み(2026-10-28・card ki-25d43146681e)— 目録は _ で始まらない submodule を 1 行(名を出す module の欄が (submodule))の外れと数え、既知の表は今の公開の submodule 731 行を載せて、submodule の行を足した版から減るだけ。名を submodule から入口へ移した行は表の module を書き換えずに同じ行と読む(submodule がまだ同じ名を出す間は定義の場所が同じ時だけ・古い口を閉じた後は名と根だけ)。1 行目に印 # moved-submodule-shim: <古い名> -> <新しい名> の在る移し替えの module(card ki-299be2ea4a13 の道具が置く)は、移した先の package の下の module も古い名で数えるので、stage 1 は表を書き換えずに緑。隠した submodule の行は表から消す。この file の deftest はその関数を呼ぶだけ")
     (law an-allowed-program-helper-only-emits-effects
       :statement "for_all package p in scope: for_all (m, n) in group(p, 許した物 — program の helper): returns_program(n) and for_all body b in bodies(n): not reads(b, handler_control) and not reads(b, handler_installer) — bodies(n) = n and the functions of p that n reads, followed through; handler_control = {Resume, ResumeThrow, Transfer, TransferThrow, Pass}; handler_installer = WithHandler or a marked value or a handler builder or a factory; and an allowed group is a group whose names the inventory checks"
       :counterexamples
         [(counterexample "program を受けて handler の値を当てて返す関数(maybe の今の形)を、program を返すから helper の組に置いて通す")
          (counterexample "helper の組に置いた関数の中で、同じ package の別の関数が WithHandler で handler を被せるのに、組の名の中だけを読んで通す")
          (counterexample "中で Resume を出して effect に答える関数を helper と数える")
          (counterexample "program を返さない関数(check のように指定の値を返す物)を helper の組に置いて通す")
          (counterexample "形を確かめない許した物の組(例 許した物 — 便利な関数)を表に作り、外れの名をそこへ移して通す")
          (counterexample "表を足した後に見せる関数を「足した名」に helper として書き、組の確かめを受けずに handler を被せる関数を通す")]
       :enforced-by ["tests/test_public_api_known_violations.py::test_only_a_program_helper_can_be_put_into_the_helper_group"
                     "tests/test_public_api_known_violations.py::test_an_allowed_group_without_a_check_is_not_well_formed"
                     "tests/test_public_api_known_violations.py::test_a_helper_added_after_the_table_is_checked_as_one"
                     "tests/test_public_api_inventory.py::test_what_the_helper_reading_does_not_see"
                     "tests/test_public_api_known_violations.py::test_public_api_outside_effects_and_handlers_is_in_the_known_table"
                     "tests/test_public_api_inventory.py::test_names_are_typed_by_marker_return_type_and_return_shape"
                     "tests/test_public_api_inventory.py::test_a_program_function_says_whether_it_is_a_helper"
                     "docs/adr/defadr_doeff_api_001_subpackages_show_effects_and_handlers.hy::test-adr-doe-api-001-only-a-program-helper-can-be-allowed-as-one"]
       :wiring "配線済み(2026-10-29・card ki-f20b18c2aad9)— 目録は program を返す関数の中(と、中で読む同じ package の関数の中)の名を code object から読み、handler の制御か handler を被せる物を読めば形「handler を被せる program を返す関数」か「effect に答える program を返す関数」、読まなければ形「program の helper」と数える(種類はどれも それ以外)。表の test は組「許した物 — program の helper」の名と、組をそれと書いた「足した名」の名の形が helper でなければ赤にし、許した物の組は形を確かめる組だけを置ける。読みが見ない形(引数で受けた handler・別の package の関数が被せる)は、今の限りとして目録の test に固定した(構成のレビュー 2026-10-29 の所見)。この file の deftest はその関数を呼ぶだけ")]
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
         (guard.test-a-different-definition-with-a-known-name-at-the-root-turns-red (/ (Path scratch) "different"))))
     (deftest test-adr-doe-api-001-a-factory-whose-call-is-not-a-handler-turns-the-guard-red
       ;; R3・R12: 同じ test の関数を呼ぶだけ — 使い捨ての package の、戻りの型で工場と読める名が呼ぶと印の無い値を返すと赤・
       ;; handler を返すように直すと緑。
       (<- guard ModuleType (guard-module))
       (with [scratch (tempfile.TemporaryDirectory)]
         (guard.test-a-factory-whose-call-is-not-a-handler-turns-red (/ (Path scratch) "factory"))))
     (deftest test-adr-doe-api-001-only-a-program-helper-can-be-allowed-as-one
       ;; R18・R12: 同じ test の関数を呼ぶだけ — 使い捨ての package の、effect を出すだけの program 関数は組「許した物 — program の
       ;; helper」に置けば緑。handler を被せる・effect に答える・program を返さない関数を組に置くと赤。形を確かめない許した物の組は表の
       ;; 形の検が赤。
       (<- guard ModuleType (guard-module))
       (with [scratch (tempfile.TemporaryDirectory)]
         (guard.test-only-a-program-helper-can-be-put-into-the-helper-group (/ (Path scratch) "helper"))
         (guard.test-a-helper-added-after-the-table-is-checked-as-one (/ (Path scratch) "added-helper")))
       (guard.test-an-allowed-group-without-a-check-is-not-well-formed))]
  :plans ["tests/test_public_api_known_violations.py・tests/data/public_api_known_violations.json (card ki-aaaa56621c5a)"
          "tools/public_api_inventory.py (card ki-633e6b8564b4)"
          "取り組み doeff-subpackages-effects-only の区切り 2〜4 の card (直し — doeff-agents・CLI コマンド・doeff-vm・doeff-hy の macro・writer_log・__version__・class の handler・submodule)"
          "R11・R13 の答えが来た後: 既知の表の範囲と組を答えに合わせる card、この ADR に条を足す card (持ち主が切る)"
          "R6: maybe・result・on_raise・absent_as を handler を返す工場の形に直す card (card ki-d27fd0acfe07・工場の形は済み)、外の repo の呼び手を書き換える card (card ki-2caf0f53603a)、4 つと scheduled の旧い形を消して既知の表の組「外れ — handler を返す工場の形に直す」を空にする card (持ち主が切る)"
          "R16: 利用者向けの docs と例の WithHandler を h1(h2(p)) に書き替える card (持ち主が切る)"
          "R18・R13 (d): 外れの組の helper の形の program 関数を許すかを組ごとか名ごとに問う依頼書と、答えに合わせて組を移す card (持ち主が切る)"])
