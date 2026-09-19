;;; Executable ADR: enforcement の正典ゲートは既定のローカル pytest である。
;;; GitHub CI には依存しない(予算により停止中)。doeff は自分の invariant を
;;; 自分の機構(defadr / deftest / defsemgrep)で、既定実行の中で守る。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-ENFORCE-001
  :title "pytest canonical gate: doeff の全 enforcement(defadr 収集・defsemgrep 静的検査・VM conformance oracle・台帳 ratchet)は既定の `uv run pytest` で収集・実行される。手動起動のみの検査、skip で緑になる検査、testpaths 外で沈黙する検査を禁止する"
  :status "proposed"
  :scope ["pyproject.toml"
          "uv.lock"
          "conftest.py"
          "Makefile"
          ".semgrep.yaml"
          ".pre-commit-config.yaml"
          "packages/doeff-adr/src/doeff_adr"
          "packages/doeff-vm-core/Cargo.toml"
          "scripts/check_enforcement_ledger.py"
          "scripts/git-hooks/pre-commit"
          "docs/adr/defadr_doeff_enforce_001_pytest_canonical_gate.hy"]
  :problem
    [(fact
       "侵食監査 2026-07-02 の判定: doeff の宣言済み不変量は ENFORCED 7 / PARTIAL 4 / EXISTS-NOT-WIRED 10 / DOC-ONLY 6。監査の結語は『現状、デフォルト実行で自分の invariant を defadr 機構で守れている箇所はゼロ』。"
       :evidence "docs/crystallization/erosion-audit-2026-07-02.md")
     (fact
       "VM conformance oracle(runtime invariants I1–I8 — 3度のリビルドから抽出された教訓の機械化)は doeff-vm-core の cargo feature `invariant-checks`(+ `python_bridge`)配下に実装済みだが、Makefile にも既定テストにも起動経路が存在しない(`grep invariant Makefile` → 0件、2026-07-14 実測)。"
       :evidence "packages/doeff-vm-core/Cargo.toml:14-20; Makefile(2026-07-14); docs/crystallization/invariants.md")
     (fact
       "ADR 起草時、.semgrep.yaml の 229 ルール(2026-07-14 実測)は `make lint-semgrep` の手動起動のみで、semgrep も dev 依存ではなかった。R3 実装により `semgrep>=1.161.0,<2` を dev 依存へ追加し、uv.lock で 1.169.0 に固定して `make sync` だけで導入される。姉妹リポジトリ ACP では 2026-07-11 の初回ゲート実行で 16 検査が即 red になった(semgrep バイナリ不在)— 『書かれたが走らない』検査は牙があっても偽の安心を生む。"
       :evidence ".semgrep.yaml; docs/crystallization/erosion-audit-2026-07-02.md; agent-control-plane .github/workflows/ci.yml 初回実行 2026-07-11")
     (fact
       "pyproject の testpaths は tests + 4 パッケージ tests のみで docs/adr を含まない。既存 defadr 10本は既定 pytest で不可視。doeff / proboscis-ema / agent-control-plane の3リポジトリが同一の罠(defadr が testpaths 外で黙って収集されない)に同時に嵌った — 各リポジトリの不注意ではなく所有層(doeff-adr)で潰すべき系統的欠陥。"
       :evidence "pyproject.toml:80-86(2026-07-14); erosion-audit 横断所見 #3; doeff issue doeff-adr-wiring-selfcheck")
     (fact
       "GitHub Actions は予算制約により停止中であり、再有効化は選択肢にない(2026-07-14 maintainer)。ゲートは開発者・エージェントが常に走らせるローカル実行に置くしかない。"
       :evidence "2026-07-14 doeff 投資計画議論")
     (fact
       "R2 で docs/adr を testpaths へ載せた後も、同じ穴が .py 側に開いたままだった(2026-09-19 実測・origin/main 14b0f2b3)。packages/*/tests は 27 木あるが testpaths に在るのは 5 木だけで、19 木 + packages/doeff-agents/conformance(32 file)が既定の走行の外。既定の収集 1,551 本に対し、集められない側が 3,200 本 — **集めない数が集める数の 2 倍**。重さは理由にならない(全数の収集が 11 秒・代表 461 本の実走が 3.7 秒)。誰も testpaths に書かなかっただけである。直接の実害として、誰も集めないので誰も気づかない腐りが 6 file: doeff-flow の 4 file(本体の run_workflow が削除済みの default_handlers() と doeff_vm.RunResult を呼ぶ)・doeff-agentic の 1 file(対象 module が、doeff_agents の __init__ から再輸出の落ちた Sleep を import。class 自体は io_effects.hy:156 に在るので消えてはいない — 再輸出の一覧という『誰も検めない表』が本体と乖離しても、木が暗がりに在る限り誰も気づかない)・doeff-openrouter の 1 file(旧 run(program, handlers=…) 時代の検体)。"
       :evidence "agora-redesign #467; card acp:kanban-issue:ki-ba058de03685; docs/doeff-2026-07-13-slog-semantics-architecture-plan.md:123")]
  :context
    [(interpretation
       "支配的な故障モードは『検査の不在』ではなく『配線の不在』(侵食監査の横断所見 #1)。検査は良質に書かれている — 走らないだけ。よって本 ADR の仕事は新しい検査を書くことではなく、既存の検査を『pytest が緑 = enforcement が走った』が構造的に成立する場所へ移すこと。")
     (interpretation
       "書き手がエージェントである以上、ゲートの正典は『エージェントが自分のループで必ず走らせるもの』でなければならない。それは pytest である。GitHub CI は(予算以前に)エージェントのループの外にある。")
     (interpretation
       "skip は偽緑の温床である。semgrep バイナリ不在・fixture 不在・feature 未ビルドは、skip ではなく hard fail として現れなければならない — fail-fast はこのリポジトリ群の基本方針である。")
     (interpretation
       "『在るのに走らない file』は 1 つの欠陥で、file の拡張子はその属性にすぎない。defadr_*.hy と test_*.py に別々の仕組みを立てると、定義点が 2 つになり、片方だけ動かした日から答えが割れる。第 2 の kind として同じ 1 つの測定に載せるのが正しい形で、これは新しい概念を足さない。")
     (interpretation
       "『集めてはいけない file』は実在する(他の道具が食わせる見本・import で実行文が走る例)。逃げ口が doeff_adr_wiring=off しか無いと、1 file のために repo ごと門を落とすことになる。path ごとの宣言を置き、理由を隣に書かせる — 黙らせた跡が diff に残る形にする。")]
  :decision
    [(rule R1 "enforcement の正典ゲートは既定の `uv run pytest`(testpaths 収集)である。GitHub CI には依存しない。pre-commit / make はこのゲートの別名であってよいが、代替ではない。")
     (rule R2 "testpaths は docs/adr を含む。さらに defadr 収集自己検査(defadr_*.hy のファイル数と収集された ADR モジュール数の一致検査)を doeff-adr パッケージが所有・提供し、全消費リポジトリが継承する(issue doeff-adr-wiring-selfcheck の根本対処)。")
     (rule R3 ".semgrep.yaml のルールは defsemgrep(installed-rule 形式)経由で既定 pytest 収集に載せる。semgrep バイナリ不在は skip ではなく hard fail。semgrep は dev 依存として `make sync` で必ず入る。")
     (rule R4 "dev ビルドは doeff-vm-core を feature `invariant-checks` + `python_bridge` 有効でビルドする(2026-07-14 B3 裁定)。VM conformance oracle(I1–I8)は pytest から起動される。invariant-checks 無効ビルドでの oracle テストは hard fail(skip 禁止)。")
     (rule R5 "anti-drop ratchet: enforcement 台帳(defadr 数・law 数・defsemgrep 数・deftest enforcement 数)が黙って減ったら fail するメタテストを既定収集に置く(orch SpecInventorySpec の pytest 版)。台帳の意図的な削減は台帳ファイルの明示的更新を伴う。")
     (rule R6 "ゲートの壁時計締切は定数でなく機械の過負荷率の関数にする(2026-08-17 追加 — operator 裁定 decision-doeff-land-gate-deadline-2026-08-17.html『A. 締切を直す』)。締切が捕まえるべきものは hang であって busy ではない。外部プロセス(semgrep・CLI・build)を待つ試験の所要は過負荷率に比例して伸びるので、定数の締切は過負荷帯で『正しい仕事に赤を出す装置』へ退化する。係数 = 1 分平均 load / コア数(下限 1.0・上限 env PYTEST_DEADLINE_SCALE_CAP 既定 8 — 上限があるので真の hang は依然として有界時間で落ちる)。無効化は env PYTEST_DEADLINE_SCALE=off(負荷を自分で制御する CI 用)。【2 つの締切は必ず一緒に動かす】pytest-timeout の per-test 締切と、その上に立つ SIGKILL watchdog の両方が同じ係数で伸び、watchdog は常に per-test 締切より厳密に上に居ること — 片方だけ上げると『遅い試験 1 本が赤くなる』が『走行ごと SIGKILL で全損する』に化ける(実測 2026-08-17: PYTEST_TIMEOUT=600 を素の 90 秒 watchdog に当てて全数電池が 45% で即死)。marker の締切(@pytest.mark.timeout)も同じ係数で伸ばす — pytest-timeout は marker を ini より優先するので、ini だけ伸ばすと『自分は遅いと申告した試験』= 外部プロセスを起こす当の試験群が素の締切に取り残される。【伸ばしたことは黙らない】係数が 1 を超えた走行は伸ばした旨と実効値を stderr に出す(伸びた締切は同時に『この機械は過負荷である』の信号でもあり、8 倍かかった走行が黙って緑を返すのは観測の欠落)。【締切は 3 つある】内側 2 つ(per-test・watchdog)に加え、門の走行そのものの持ち時間(.agents/land-queue.toml の gate.timeout_s)が第 3 の締切である。内側を伸ばせば走行の総時間は必然的に伸びる(60 秒で落ちていた試験が数百秒まで走れるようになったのだから当然で、欠陥ではなく設計)ので、第 3 の締切を据え置くと内側の修理は『1 テストの赤』を『走行全体の時間切れ』へ移し替えるだけになる — 実測 2026-08-17: 内側だけ直した便が 2710.7 秒で門の 2700 秒に当たった(内訳 = Rust 再 build 約 12 分 + 電池 33 分超・load 約 100 帯)。第 3 の締切は連邦の機構(dotfiles land.py)が読む静的な宣言で負荷に連動する口を持たないため、係数が上限に張り付いた走行でも終われる値を宣言で置き、その根拠を宣言の隣に書く(2026-08-17 時点 7200)。")
     (rule R7 "R5 の台帳突合は著述時にも走る(2026-08-21 追加 — 日次 verify 赤 doeff-verify-20260821-065005 の根治)。実弾: ADR-DOE-HY-004 新設(f47f0a4b)が defadr +1・deftest +2・law +1 を台帳未更新のまま運んだが、着地の窓は力学のみ(2026-08-17 operator 裁定・mode = \"focus\")で、しかもこの commit は land queue を通らず直接 push で main へ届いた — 窓をどう固くしても捕まらない経路が正規に在る以上、記帳漏れを構造的に止められる検出点は著述時 = git commit 時だけ(どの経路でも commit は必ず著述機の git を通る)。実装: 勘定の定義点は scripts/check_enforcement_ledger.py の 1 点(stdlib 単独 — venv・依存の状態に依らず走る)で、既定 pytest の R5 検査 tests/test_enforcement_ledger.py も同じ家を消費する(第 2 の定義点を作らない — regex が乖離した日から hook 緑 = verify 緑が成立しなくなる)。hook(tracked 原本 = scripts/git-hooks/pre-commit・導入 = make hooks-install・pre-commit framework 機体は .pre-commit-config.yaml の enforcement-ledger)は staged 断面(index)を突合する — working tree 突合は『台帳も直したが stage し忘れた』を素通しする。作り直し(rebase / cherry-pick / sequencer)中は判定しない — 着地の窓の追随・replay を塞がない。正典ゲートは R1 のとおり既定 pytest のまま(hook は R1 の『pre-commit はゲートの別名であってよい』の実装であり代替ではない — hook 未導入の機体と --no-verify は日次 verify が引き続き捕まえる)。")
     (rule R8 "既定の走行の収集の範囲は **repo 内の python の検体 file 全部**である(2026-09-19 追加 — agora-redesign #467)。R2 が defadr_*.hy について言っていることを、第 2 の file 種 test_*.py へ広げる。【定義点は 1 つ】複製の仕組みを作らず、doeff-adr の配線検査の同じ 1 回の歩きに kind を足す — どの .py が検体かは pytest 自身の python_files を読む(第 2 の意見を持つと、pattern を変えた project で偽の赤と偽の緑が同時に出る)。【配線は『届いたか』で測る】file から item が何本出たかでは測らない — module 直下の pytest.importorskip は依存の無い機体で 0 本になるので、正しく配線された木が機体によって赤くなったり緑になったりする(選択 -k / -m を配線と数えないのと同じ理由)。pytest.File の collector が立ったかで測る。【集めてはいけない側は宣言する】他の道具が食わせる見本・import で実行文が走る例・腐っていて別便で直す木は、doeff_adr_wiring_exclude に **path ごと・理由つきで**書く。この表は root conftest.py が collect_ignore_glob へ渡すので、「集めない」と「門が期待しない」が同じ 1 つの宣言から出る(第 2 の表を作らない)。doeff_adr_wiring=off は repo ごとの opt-out で、1 file のために使わない。【載せ方】testpaths は木を 1 つずつ名指す — `packages` を丸ごと足すと examples/ の import で走る script や src の中のたまたま test_ で始まる module まで集めて、収集が副作用を起こす。同名 file(test_effect_handlers.py が 9 本)は --import-mode=importlib で解く(prepend では import file mismatch が 14 件 — file を 14 本改名しないと直らない)。importlib は隣の helper module を見えなくするので、それが要る木は pythonpath ini で名指す。")]
  :laws
    [(law default-pytest-sees-all-enforcement
       :statement "for_all declared_enforcement e: collected_by(default_pytest, e) AND (missing_dependency(e) => hard_fail, not skip)"
       :counterexamples
         [(counterexample "defadr 10本が testpaths 外で沈黙している現状 — `uv run pytest` は enforcement ゼロのまま緑")
          (counterexample "semgrep 不在の環境で defsemgrep が skip され、229 ルール全滅のままスイートが緑")
          (counterexample "invariant-checks 無効のリリースビルドに対して oracle テストが skip され、VM 不変量が未検証のまま緑")])
     (law deadline-measures-hang-not-load
       :statement "effective_deadline == base_deadline * clamp(loadavg1 / ncpu, 1.0, CAP); scaled_together(per_test_deadline, watchdog) AND watchdog > per_test_deadline for_all scales; scale > 1 => announced"
       :counterexamples
         [(counterexample
             "2026-08-17 実測(修理前): 18 コアの機械が load 60〜156 で定常稼働する中、1 テスト 60 秒の定数締切に対し semgrep 単体が load 80 で 29.7 秒を使い、電池全体の負荷が乗ると超える。着地の門が 5 連敗し(毎回ちがう外部プロセス起動テスト)、同じ試験は手つかずの main を単独で回しても同じく落ちた = 枝の欠陥ではなく締切の性質。2 席で 9 回の着地が死に、正しい修理が出荷できなかった")
          (counterexample
             "per-test 締切だけを伸ばす — その上に立つ SIGKILL watchdog は定数のままなので、遅い試験 1 本が赤くなる代わりに走行ごと即死し、通った試験の結果まで全部失われる(実測 2026-08-17: PYTEST_TIMEOUT=600 で全数電池が 45% で SIGKILL)。締切は 2 つあり、片方だけ動かすのは動かさないより悪い")
          (counterexample
             "ini の締切だけを伸ばし marker(@pytest.mark.timeout)を素のまま残す — pytest-timeout は marker を ini より優先するので、『自分は遅い』と申告した試験 = 外部プロセスを起こす当の試験群だけが取り残される")
          (counterexample
             "係数に上限を置かない — 過負荷が青天井の機械では締切が事実上消え、真の hang が永久に落ちない(『busy を hang と誤認する』の裏返しで、今度は hang を busy と誤認する)")
          (counterexample
             "伸ばしたことを黙って行う — 8 倍の締切で通った走行は『緑』としか見えず、機械が過負荷であるという同じくらい重要な観測が失われる")
          (counterexample
             "内側の 2 つだけ直して門の持ち時間(gate.timeout_s)を据え置く — 内側を伸ばせば走行の総時間は必ず伸びるので、赤の場所が『1 テストの timeout』から『走行全体の時間切れ』へ移るだけで、着地はやはり通らない(実測 2026-08-17: 内側だけ直した便が 2710.7 秒で 2700 秒の門に当たった)。締切は 3 つあり、族として直す")])
     (law ledger-checked-at-authoring-time
       :statement "for_all commit c touching(enforcement_assets): checked_against_ledger(staged_snapshot(c)) before commit_created on hook-installed machines; counting_home(pre_commit_hook) == counting_home(default_pytest_R5) == scripts/check_enforcement_ledger.py"
       :counterexamples
         [(counterexample
             "2026-08-21 実弾: ADR-DOE-HY-004 新設(f47f0a4b)が defadr +1・deftest +2・law +1 を台帳未更新のまま直接 push で main へ運び、着地の窓(力学のみ)も素通り — 初検出が翌朝の日次 verify(doeff-verify-20260821-065005)まで遅れ、無実の 2 便(L43 / L44)が容疑に挙がった")
          (counterexample
             "敷設時実弾(2026-08-09): land-queue 敷設の bootstrap で semgrep_rules 246≠247 の記帳漏れが main の実赤として発覚 — 同じ形の 2 度目が今回。個々の著者の不注意ではなく、著述時に何も突合しない構造の欠陥")
          (counterexample
             "working tree だけを突合する hook — 台帳を直したが stage し忘れた commit が緑で通り、tree は一致・commit は不一致のまま歴史に入る")
          (counterexample
             "勘定の第 2 定義点(hook が独自の regex を持つ)— test と hook の regex が乖離した日から hook 緑 = verify 緑が成立しなくなり、hook は偽の安心を配る装置になる")
          (counterexample
             "作り直し(rebase / replay)中も判定する hook — 着地の窓の基底追随が、当便と無関係な中間断面の不一致で塞がれ、land queue が jam する")])
     (law every-python-test-file-is-collected
       :statement "for_all python_test_file f under rootdir: reached_by(default_pytest_collection, f) XOR declared_uncollectable(f, doeff_adr_wiring_exclude); reached measured_by File_collector not item_count; measuring_home(python_kind) == measuring_home(executable_adr_kind); declaration_home(uncollectable) == declaration_home(collect_ignore_glob)"
       :counterexamples
         [(counterexample
             "2026-09-19 実弾(この規則の出自): packages/*/tests 27 木のうち 19 木 + conformance 32 file が testpaths の外に在り、3,200 本が沈黙していた — 既定の走行が集める 1,551 本の 2 倍。書かれた test が在るので誰も書き直さず、6 file は数か月前に消えた API(default_handlers / doeff_vm.RunResult / doeff_agents.Sleep)のまま腐って、1 度も赤を出さなかった")
          (counterexample
             ".py 専用の第 2 の walk を新設する — 定義点が 2 つになり、norecursedirs の扱い・歩きの上限・報告の文面が別々に育つ。片方だけ直した日から、同じ repo について 2 つの答えが出る")
          (counterexample
             "どの .py が検体かを plugin 側に焼く — python_files を変えた project で、pytest が集めない test_*.py を門が名指し(偽の赤)、その project の本当の検体は門に見えない(偽の緑)。2 つの誤りが同時に出る")
          (counterexample
             "item の本数で配線を測る — module 直下の pytest.importorskip は optional 依存の無い機体で 0 本になるので、正しく配線された木が機体によって赤くなる。doeff には現にそういう file が 2 本在る(tests/test_llm_multi_provider_handlers.py・packages/doeff-gemini/tests/unit/test_structured_llm.py)")
          (counterexample
             "集めてはいけない 1 file のために doeff_adr_wiring=off を置く — repo ごと門が落ち、その後に開いた穴が永久に見えなくなる。逃げ口は path ごとで、理由が隣に残ること")
          (counterexample
             "『集めない』(collect_ignore_glob)と『門が期待しない』(doeff_adr_wiring_exclude)を別の表に書く — 乖離した日に、pytest が集めないのに門が未収集と呼ぶか、門は緑なのに収集が落ちるかのどちらかになる")
          (counterexample
             "testpaths に `packages` を丸ごと足す — packages/doeff-agentic/examples/test_*.py は module 直下に実行文が在るので、収集した瞬間に workflow が走る。src の中の doeff-test-target/…/test_effects.py も production の module のまま集まる")
          (counterexample
             "腐っている木を pytest.mark.skip で緑にする — 宣言が diff に残らず、門も『集まっている』と答えるので、直す動機が二度と生まれない")])]
  :enforcement
    [(deftest test-adr-doe-enforce-001-docs-adr-in-default-testpaths
       ;; RED(2026-07-14): pyproject testpaths は docs/adr を含まない。R2 実装で green。
       (import tomllib)
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       (setv cfg (tomllib.loads (.read-text (/ root "pyproject.toml"))))
       (setv testpaths (get cfg "tool" "pytest" "ini_options" "testpaths"))
       (assert (in "docs/adr" testpaths)
               f"docs/adr が testpaths に無い: {testpaths} — ADR-DOE-ENFORCE-001 R2"))
     (deftest test-adr-doe-enforce-001-vm-oracle-wired
       ;; RED(2026-07-14): Makefile に invariant-checks の起動経路が無い。R4 実装で green。
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       (setv makefile (.read-text (/ root "Makefile")))
       (assert (in "invariant-checks" makefile)
               "Makefile に invariant-checks の配線が無い — ADR-DOE-ENFORCE-001 R4(B3 裁定 2026-07-14)"))
     (deftest test-adr-doe-enforce-001-deadlines-are-load-scaled
       ;; R6 + law deadline-measures-hang-not-load: 2 つの締切がどちらも
       ;; 係数に掛かっていること、係数の家が 1 つであること、伸ばした事実を
       ;; 黙らないことの実在 pin。挙動の実体は tests/test_deadline_load_scaling.py
       ;; (係数の下限・上限・無効化・watchdog が per-test を追い越さない・
       ;;  呼び手の明示値を潰さない、の 5 本)。
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       (setv conftest (.read-text (/ root "conftest.py") :encoding "utf-8"))
       ;; 係数の単一の家。
       (assert (in "def deadline_scale(" conftest)
               "締切の係数の単一の家が root conftest に無い — ADR-DOE-ENFORCE-001 R6")
       (assert (in "os.getloadavg()" conftest)
               "係数が機械の過負荷率を読んでいない(定数へ退行している)")
       (assert (in "PYTEST_DEADLINE_SCALE_CAP" conftest)
               "係数に上限が無い — 真の hang が有界時間で落ちなくなる")
       (assert (in "PYTEST_DEADLINE_SCALE" conftest)
               "無効化の口が無い(負荷を自分で制御する CI が締切を固定できない)")
       ;; 2 つの締切が一緒に動くこと — watchdog は per-test 締切から導出する。
       (assert (in "def scaled_watchdog_timeout(" conftest)
               "watchdog が per-test 締切から導出されていない(片方だけ伸びて SIGKILL 全損になる)")
       ;; 伸ばした事実を黙らない。
       (assert (in "deadline scale x" conftest)
               "係数が 1 を超えた走行が黙っている(過負荷の観測が失われる)")
       ;; marker の締切も同じ係数に掛かること。
       (assert (in "def pytest_collection_modifyitems(" conftest)
               "marker の締切が素のまま残る(pytest-timeout は marker を ini より優先する)")
       ;; 挙動本体の現存 pin(削除退行を loud にする)。
       (setv behavioral (/ root "tests/test_deadline_load_scaling.py"))
       (assert (.exists behavioral)
               "R6 の挙動本体 tests/test_deadline_load_scaling.py が消えている")
       ;; 第 3 の締切(門の走行そのものの持ち時間)が、内側を上限まで
       ;; 伸ばした走行を収容できること — 据え置くと赤の場所が移るだけになる。
       (import tomllib)
       (setv land-cfg (tomllib.loads (.read-text (/ root ".agents/land-queue.toml")
                                                 :encoding "utf-8")))
       (setv gate-budget (get land-cfg "gate" "timeout_s"))
       (assert (>= gate-budget 7200)
               f"門の持ち時間 {gate-budget}s は内側を上限まで伸ばした走行を収容できない — ADR-DOE-ENFORCE-001 R6(締切は 3 つあり族として直す)"))
     (deftest test-adr-doe-enforce-001-ledger-authoring-guard-wired
       ;; R7 + law ledger-checked-at-authoring-time: 台帳突合の著述時配線の実在 pin。
       ;; 挙動の実体は tests/test_enforcement_ledger_hook.py(勘定の一致・staged 断面・
       ;; stage し忘れの検出・rebase 中の沈黙・無関係 commit の素通し、の 10 本)。
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       ;; 勘定の単一の家。
       (setv checker (/ root "scripts/check_enforcement_ledger.py"))
       (assert (.exists checker)
               "勘定の家 scripts/check_enforcement_ledger.py が消えている — ADR-DOE-ENFORCE-001 R7")
       ;; hook の tracked 原本が家を staged 断面で消費すること。
       (setv hook (/ root "scripts/git-hooks/pre-commit"))
       (assert (.exists hook)
               "hook の tracked 原本 scripts/git-hooks/pre-commit が消えている — R7")
       (setv hook-text (.read-text hook :encoding "utf-8"))
       (assert (in "check_enforcement_ledger.py" hook-text)
               "hook が勘定の家を呼んでいない(第 2 定義点への退行)")
       (assert (in "--staged" hook-text)
               "hook が staged 断面を突合していない(stage し忘れを素通しする退行)")
       ;; pre-commit framework 機体にも同じ検査が入ること。
       (setv pc (.read-text (/ root ".pre-commit-config.yaml") :encoding "utf-8"))
       (assert (in "check_enforcement_ledger.py --staged" pc)
               ".pre-commit-config.yaml に enforcement-ledger 検査が無い — framework 導入機で hook が外れる")
       ;; 導入経路の実在。
       (setv makefile (.read-text (/ root "Makefile") :encoding "utf-8"))
       (assert (in "hooks-install" makefile)
               "Makefile に hooks-install が無い — hook の導入経路の消失")
       ;; 挙動本体の現存 pin(削除退行を loud にする)。
       (assert (.exists (/ root "tests/test_enforcement_ledger_hook.py"))
               "R7 の挙動本体 tests/test_enforcement_ledger_hook.py が消えている")
       ;; 既定 pytest の R5 検査が同じ家を消費していること。
       (setv r5-test (.read-text (/ root "tests/test_enforcement_ledger.py") :encoding "utf-8"))
       (assert (in "check_enforcement_ledger" r5-test)
               "R5 検査が勘定の家を消費していない — 第 2 定義点への退行"))
     (deftest test-adr-doe-enforce-001-packages-test-trees-are-wired
       ;; R8 + law every-python-test-file-is-collected の実在 pin。
       ;; 挙動の実体は 2 つ: tests/test_adr_wiring_gate.py(走行ごとに repo 全体を
       ;; 実測する門そのもの)と packages/doeff-adr/tests/test_wiring.py
       ;; (第 2 の file 種の 6 本 — testpaths の外の赤・入れれば緑・python_files を
       ;;  読む・宣言で外す・session の中の口・届いたかで測る)。
       ;; この deftest が見るのは「宣言が在るか」と「宣言の家が 1 つか」だけで、
       ;; 収集をもう 1 回起こさない — 法 gate-test-reads-the-sessions-own-collection。
       (import tomllib)
       (import fnmatch)
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       (setv cfg (tomllib.loads (.read-text (/ root "pyproject.toml") :encoding "utf-8")))
       (setv ini (get cfg "tool" "pytest" "ini_options"))
       ;; 取り込み方式 — 同名 file を 14 本改名せずに衝突を消す唯一の宣言。
       (assert (in "--import-mode=importlib" (.get ini "addopts" []))
               "addopts に --import-mode=importlib が無い — 同名 file(test_effect_handlers.py が 9 本)が同じ module 名に畳まれて走行が落ちる(ADR-DOE-ENFORCE-001 R8)")
       ;; 集める木の宣言 — python の検体を持つ packages/*/tests は、testpaths に
       ;; 居るか、1 file 残らず宣言で暗がりに置かれているかのどちらか。
       (setv testpaths (set (get ini "testpaths")))
       (setv excluded (.get ini "doeff_adr_wiring_exclude" []))
       (defn dark? [path]
         (setv rel (.as-posix (.relative-to path root)))
         (any (gfor pat excluded (fnmatch.fnmatch rel pat))))
       (for [tree (sorted (.glob root "packages/*/tests"))]
         (setv files (list (.rglob tree "test_*.py")))
         (when files
           (setv rel (.as-posix (.relative-to tree root)))
           (assert (or (in rel testpaths) (all (gfor f files (dark? f))))
                   f"{rel} に python の検体が在るのに testpaths にも doeff_adr_wiring_exclude にも居ない — ADR-DOE-ENFORCE-001 R8")))
       ;; packages/*/tests の形をしていない木も名指す(見落としの実例)。
       (assert (in "packages/doeff-agents/conformance" testpaths)
               "conformance(32 file)が testpaths から落ちている — ADR-DOE-ENFORCE-001 R8")
       ;; 宣言の家は 1 つ — 同じ表を pytest の収集(collect_ignore_glob)へ渡す。
       (setv conftest (.read-text (/ root "conftest.py") :encoding "utf-8"))
       (assert (in "doeff_adr_wiring_exclude" conftest)
               "root conftest が宣言の表を読んでいない — 『集めない』と『門が期待しない』が 2 つの表に割れる(R8)")
       (assert (in "collect_ignore_glob" conftest)
               "宣言が pytest の収集へ渡っていない — 宣言した file を pytest が集めて落ちる(R8)")
       ;; 測り方の家は 1 つ — .py の kind も同じ plugin の同じ歩きに載る。
       (setv plugin (.read-text (/ root "packages/doeff-adr/src/doeff_adr/pytest_plugin.py")
                                :encoding "utf-8"))
       (assert (in "python_files" plugin)
               "どの .py が検体かを pytest 自身の ini から読んでいない(第 2 の意見への退行)— R8")
       (assert (in "doeff_adr_wiring_exclude" plugin)
               "門が宣言された暗がりを読んでいない — 逃げ口が doeff_adr_wiring=off だけになる(R8)")
       (assert (in "def pytest_collectstart(" plugin)
               "配線を item の本数で測る形への退行 — optional 依存の無い機体で正しい木が赤くなる(R8)")
       ;; 挙動本体の現存 pin(削除退行を loud にする)。
       (assert (.exists (/ root "packages/doeff-adr/tests/test_wiring.py"))
               "R8 の挙動本体 packages/doeff-adr/tests/test_wiring.py が消えている")
       (assert (.exists (/ root "tests/test_adr_wiring_gate.py"))
               "R8 の実測の本体 tests/test_adr_wiring_gate.py が消えている"))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
