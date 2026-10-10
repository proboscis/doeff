;;; Executable ADR: enforcement の正典ゲートは既定のローカル pytest である。
;;; GitHub CI には依存しない(予算により停止中)。doeff は自分の invariant を
;;; 自分の機構(defadr / deftest / defsemgrep)で、既定実行の中で守る。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest val])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-ENFORCE-001
  :title "pytest canonical gate: doeff の全 enforcement(defadr 収集・defsemgrep 静的検査・VM conformance oracle・台帳 ratchet)は既定の `uv run pytest` で収集・実行される。手動起動のみの検査、skip で緑になる検査、testpaths 外で沈黙する検査を禁止する"
  :status "proposed"
  :scope ["pyproject.toml"
          "uv.lock"
          "Makefile"
          ".semgrep.yaml"
          ".pre-commit-config.yaml"
          "packages/doeff-adr/src/doeff_adr"
          "packages/doeff-vm-core/Cargo.toml"
          "scripts/check_enforcement_ledger.py"
          "scripts/git-hooks/pre-commit"
          ".agents/land-queue.toml"
          "scripts/gate_tools.sh"
          "tests/test_daily_test_population.py"
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
       :evidence "2026-07-14 doeff 投資計画議論")]
  :context
    [(interpretation
       "支配的な故障モードは『検査の不在』ではなく『配線の不在』(侵食監査の横断所見 #1)。検査は良質に書かれている — 走らないだけ。よって本 ADR の仕事は新しい検査を書くことではなく、既存の検査を『pytest が緑 = enforcement が走った』が構造的に成立する場所へ移すこと。")
     (interpretation
       "書き手がエージェントである以上、ゲートの正典は『エージェントが自分のループで必ず走らせるもの』でなければならない。それは pytest である。GitHub CI は(予算以前に)エージェントのループの外にある。")
     (interpretation
       "skip は偽緑の温床である。semgrep バイナリ不在・fixture 不在・feature 未ビルドは、skip ではなく hard fail として現れなければならない — fail-fast はこのリポジトリ群の基本方針である。")]
  :decision
    [(rule R1 "enforcement の正典ゲートは既定の `uv run pytest`(testpaths 収集)である。GitHub CI には依存しない。pre-commit / make はこのゲートの別名であってよいが、代替ではない。")
     (rule R2 "testpaths は docs/adr を含む。さらに defadr 収集自己検査(defadr_*.hy のファイル数と収集された ADR モジュール数の一致検査)を doeff-adr パッケージが所有・提供し、全消費リポジトリが継承する(issue doeff-adr-wiring-selfcheck の根本対処)。")
     (rule R3 ".semgrep.yaml のルールは defsemgrep(installed-rule 形式)経由で既定 pytest 収集に載せる。semgrep バイナリ不在は skip ではなく hard fail。semgrep は dev 依存として `make sync` で必ず入る。")
     (rule R4 "dev ビルドは doeff-vm-core を feature `invariant-checks` + `python_bridge` 有効でビルドする(2026-07-14 B3 裁定)。VM conformance oracle(I1–I8)は pytest から起動される。invariant-checks 無効ビルドでの oracle テストは hard fail(skip 禁止)。【2026-09-29 追補 — 実行時に有効にする形へ(agora-redesign #980)】cargo feature で build を分けた形は、make sync(検査つき)と素の uv sync(検査なし・venv-run が lock の変化で自動で撃つ)の 2 経路を生み、同じ venv が最後に組んだ経路で 15 倍速くも遅くもした(実測: agora の 1 検で 3.3〜5.7 秒 / 49.8〜62.2 秒)。検査はどの build にも compile し、実行時に有効にする — 環境変数 DOEFF_VM_INVARIANT_CHECKS=1(拡張が最初の step で 1 回読む・0 / 1 以外は panic)か doeff_vm.doeff_vm.set_invariant_checks(True)。doeff の pytest は root の conftest.py が有効にし(子 process は変数を継ぐ)、それ以外(agora と本番)は既定で無効。cargo feature `invariant-checks` は Rust の conformance test(make test-vm-invariants)のために『常に有効』の意味で残す。oracle が無効な pytest の走行は tests/test_vm_invariant_checks_enabled.py が hard fail にする規則は変わらない。無効時の費用は step ごとの atomic の読み 1 回。")
     (rule R5 "anti-drop ratchet: enforcement 台帳(defadr 数・law 数・defsemgrep 数・deftest enforcement 数)が黙って減ったら fail するメタテストを既定収集に置く(orch SpecInventorySpec の pytest 版)。台帳の意図的な削減は台帳ファイルの明示的更新を伴う。")
     (rule R6 "ゲートの壁時計締切は定数でなく機械の過負荷率の関数にする(2026-08-17 追加 — operator 裁定 decision-doeff-land-gate-deadline-2026-08-17.html『A. 締切を直す』)。締切が捕まえるべきものは hang であって busy ではない。外部プロセス(semgrep・CLI・build)を待つ試験の所要は過負荷率に比例して伸びるので、定数の締切は過負荷帯で『正しい仕事に赤を出す装置』へ退化する。係数 = 1 分平均 load / コア数(下限 1.0・上限 env PYTEST_DEADLINE_SCALE_CAP 既定 8 — 上限があるので真の hang は依然として有界時間で落ちる)。無効化は env PYTEST_DEADLINE_SCALE=off(負荷を自分で制御する CI 用)。【2 つの締切は必ず一緒に動かす】pytest-timeout の per-test 締切と、その上に立つ SIGKILL watchdog の両方が同じ係数で伸び、watchdog は常に per-test 締切より厳密に上に居ること — 片方だけ上げると『遅い試験 1 本が赤くなる』が『走行ごと SIGKILL で全損する』に化ける(実測 2026-08-17: PYTEST_TIMEOUT=600 を素の 90 秒 watchdog に当てて全数電池が 45% で即死)。marker の締切(@pytest.mark.timeout)も同じ係数で伸ばす — pytest-timeout は marker を ini より優先するので、ini だけ伸ばすと『自分は遅いと申告した試験』= 外部プロセスを起こす当の試験群が素の締切に取り残される。【伸ばしたことは黙らない】係数が 1 を超えた走行は伸ばした旨と実効値を stderr に出す(伸びた締切は同時に『この機械は過負荷である』の信号でもあり、8 倍かかった走行が黙って緑を返すのは観測の欠落)。【締切は 3 つある】内側 2 つ(per-test・watchdog)に加え、門の走行そのものの持ち時間(.agents/land-queue.toml の gate.timeout_s)が第 3 の締切である。内側を伸ばせば走行の総時間は必然的に伸びる(60 秒で落ちていた試験が数百秒まで走れるようになったのだから当然で、欠陥ではなく設計)ので、第 3 の締切を据え置くと内側の修理は『1 テストの赤』を『走行全体の時間切れ』へ移し替えるだけになる — 実測 2026-08-17: 内側だけ直した便が 2710.7 秒で門の 2700 秒に当たった(内訳 = Rust 再 build 約 12 分 + 電池 33 分超・load 約 100 帯)。第 3 の締切は連邦の機構(dotfiles land.py)が読む静的な宣言で負荷に連動する口を持たないため、係数が上限に張り付いた走行でも終われる値を宣言で置き、その根拠を宣言の隣に書く(2026-08-17 時点 7200)。【2026-09-27 追補 — watchdog の終わり方(agora-redesign #645 課題 6)】上の『SIGKILL watchdog』は 2026-08-17 時点の形の記録。watchdog は #639 の依頼 T で SIGKILL をやめ、止まった検を pytest の短報の形(`FAILED <nodeid> - WATCHDOG: …`)で名指して終了コード 1 で終える形に変わった(signal で終わる走行は着地の道具が『外からの kill』と読み、赤として数えないため)。#645 課題 1 からは、日次の session の書き手(plugin `ai-session-answer`)が据わっていればその口 `answer_stopped` でも止まった検を名乗る。2 つの締切を同じ係数で動かし watchdog を per-test 締切より上に置く規則は変わらない。")
     (rule R7 "R5 の台帳突合は著述時にも走る(2026-08-21 追加 — 日次 verify 赤 doeff-verify-20260821-065005 の根治)。実弾: ADR-DOE-HY-004 新設(f47f0a4b)が defadr +1・deftest +2・law +1 を台帳未更新のまま運んだが、着地の窓は力学のみ(2026-08-17 operator 裁定・mode = \"focus\")で、しかもこの commit は land queue を通らず直接 push で main へ届いた — 窓をどう固くしても捕まらない経路が正規に在る以上、記帳漏れを構造的に止められる検出点は著述時 = git commit 時だけ(どの経路でも commit は必ず著述機の git を通る)。実装: 勘定の定義点は scripts/check_enforcement_ledger.py の 1 点(stdlib 単独 — venv・依存の状態に依らず走る)で、既定 pytest の R5 検査 tests/test_enforcement_ledger.py も同じ家を消費する(第 2 の定義点を作らない — regex が乖離した日から hook 緑 = verify 緑が成立しなくなる)。hook(tracked 原本 = scripts/git-hooks/pre-commit・導入 = make hooks-install・pre-commit framework 機体は .pre-commit-config.yaml の enforcement-ledger)は staged 断面(index)を突合する — working tree 突合は『台帳も直したが stage し忘れた』を素通しする。作り直し(rebase / cherry-pick / sequencer)中は判定しない — 着地の窓の追随・replay を塞がない。正典ゲートは R1 のとおり既定 pytest のまま(hook は R1 の『pre-commit はゲートの別名であってよい』の実装であり代替ではない — hook 未導入の機体と --no-verify は日次 verify が引き続き捕まえる)。")
     (rule R8 "日次の全体検証の母集団は root の pytest(testpaths)・package ごとの pytest(make test-packages)・Rust crate ごとの cargo test(make test-rust)の 3 つで、.agents/land-queue.toml の gate.full の別々の処理ステージに置く(2026-09-25 追加 — card acp:kanban-issue:ki-08ec2d7c901f)。後ろを止めてよいのは先頭の build の段(make sync)だけで、テストの段はどれも自分で make sync からやり直す(遠隔の同期は段ごとに target/ を消し、段は 1 つずつ手元へ倒れ得る)。1 つの母集団の赤は他の母集団を未実行にしない。package の母集団は packages/<p>/tests に fixtures 以外の検の file を持つ全 package と、package の tests/ の外の根(Makefile の PACKAGE_EXTRA_TEST_ROOTS の 1 点で宣言)を訪ね、1 package の赤で止まらず、最後に失敗の package を名指して 0 以外で終わる。失敗名は repo の根からの相対で出す(日次の道具は失敗名を要約の行から逐語で取り、repo の根から pytest へそのまま渡す)。repo の検の file(pytest の python_files の test_*.py / *_test.py・root の ini の doeff_hy_test_files で doeff-adr の plugin が集める Hy の test_*.hy(deftest の file も上から順に実行する script も・集め手を conftest.py に写さない・どの集め手にも当たらない test_*.py / test_*.hy は赤 — agora-redesign #2591)・doeff-adr の plugin が集める defadr_*.hy の pattern — fixtures の下を除く・定義は tests/test_daily_test_population.py の 1 点)は母集団の根の下か、tests/test_daily_test_population.py の理由つきの除外の表に在る。package は自分の pytest の設定を持たない(持つと root の ini と conftest が package の母集団に効かなくなる)。宣言の形はこの ADR の deftest、挙動の本体は tests/test_daily_test_population.py が固定し、どちらも root の段 = 日次で毎回走る。")
     (rule R9 "台帳は木から生成する一覧で、手で書かない(2026-09-28 追加 — agora-redesign #802)。実弾: e15f9516(2026-09-26・agentd の退役)が ADR-DOE-AGENTS-012 の冊・semgrep の規則 7 本・ADR-DOE-AGENTS-004 の法 1 本を消したが台帳を下げずに本線へ入り、以後 docs/adr に触る commit は全部この hook で落ち、作業係は SKIP=enforcement-ledger / --no-verify で外した(10 便以上・2026-09-27〜28)。数の台帳には 3 つの欠陥があった — (a) 数は並行する 2 便の足し算を git が合わせられない(2026-09-17 ed98775a: 2 便が同じ 104 → 105 を刻み、黙って 105 に合わさった)、(b) 字面の数え(`\"(law \"` の出現数)は註と文字列の中の綴りも数えた(2026-09-28: law 134 に対し形は 132・deftest 82 に対し 80)、(c) 本線に既に在るずれを、ずれに無関係な後の commit の責任にした。形: 台帳 docs/adr/enforcement-ledger.json は enforcement 資産の**名の一覧**(defadr の file 名・.semgrep.yaml の規則 id・ADR の law / deftest / defsemgrep を『ADR id 名』で)で、scripts/check_enforcement_ledger.py --write(= make enforcement-ledger)だけが作る。消えた項目は台帳の差分に名前で残り、--write は外した項目を名前で申告する — 意図した削除かを見る場所は台帳の差分。ADR は Hy の読み(文字列・`;` の註・`#_` で捨てた形・`#[[…]]` の中は数えない)で形として数え、その読みが Hy の reader と同じ答えを返すことを tests/test_enforcement_ledger.py が実物の ADR 全部で突き合わせる。hook(--staged)は台帳と一致すれば通し、一致しなくても**その commit 自身の増減**(HEAD からの差)が台帳の増減にそのまま写っていれば通して、HEAD に既に在ったずれは申告だけする(ずれを持ち込んだ commit の責任。後の commit を塞いで迂回を常態にしない)。絶対一致は既定 pytest(日次)が引き続き課す。着地の窓に突合を足す案は採らない — 窓は力学のみ(2026-08-17 operator 裁定)。【2026-09-28 追補 — 赤の出所を分けて出す】実弾: 上の直し(8c4754cb)の後も、直し以前の『本線の台帳がずれているので docs/adr を触る commit はどれも赤』という理解のまま作業係が --no-verify で外し続け、そのうち b32941c3(ADR-DOE-HY-007 に law defk-answers-are-not-used-bare を足した)が台帳に載せずに本線へ入った — この時 hook が出した赤はその commit 自身の記帳漏れだった。hook の赤の申告は『この commit の増減のうち台帳に無い分』と『(参考)HEAD に既に在ったずれ』を分けて名指し、赤が前者だけから出ることを文面で言う。増えた時は通して減った時だけ赤にする片側の形(agora-controllers scripts/ratchet_tables.hy の考え方)は採らない — 台帳は名の一覧で並行の追加は行ごとに合わさるので揺れの元が無く、足した項目を台帳に載せない形は、後でその項目が消えても台帳の差分に名前が残らず R5 の見張りが効かない。")
     (rule R10 "日次の全体検証(.agents/land-queue.toml の gate.full)の処理ステージは、日次の task が走っている所で直に走らせ、木を別の機体へ送って走らせない — run に remote_check の前置き(`remote_check.py --node <機体> … -- sh -c \"…\"`)を書かない(2026-10-10 追加 — card acp:kanban-issue:ki-9338eec1d15e・調整役 cisco-c8 の割り・依頼 lt-2BJSBE9W1MVDGPRC8JTW443KSJ)。なぜ: 利用者の決定 2026-10-05 23:0x『zeus は会話の速さを一番にする』。日次の task は 2026-10-10 16:00 から atlas の静的な doeff-worker の Pod で走る(agora-controllers の daily-verify・card ki-73fbd134a244)が、doeff の 6 段はどれも `remote_check.py --node zeus` で木を zeus へ送っていたので、Rust の build・root の pytest・18 package の検・cargo test が zeus の CPU に載り、会話のターンと取り合っていた。代案の `--node atlas`(ssh で atlas の機体へ)は取らない — 機体への ssh の鍵と機体の環境の整えが要り、機体の操作になる。uv の cache の置き場は呼び手の UV_CACHE_DIR(日次の task が worker の共有の cache を渡す)を先に使い、無い時だけ専用の lane `${XDG_CACHE_HOME:-$HOME/.cache}/uv-lanes/land-doeff` へ倒す — task の HOME は走行ごとに空の dir なので、HOME の下の lane は毎回冷えた cache になる。走る所の道具の依存(packages の段の redis-server・lint の段の基点の鍵の doeff-linter)は scripts/gate_tools.sh の 1 点で用意する(zeus の land-arm の開発版と断面の置き場は atlas の task の HOME に無い)。R8 の『テストの段はどれも自分で make sync から』は残す — 段は同じ木で順に走るので 2 度目からの make sync は uv の同期の確かめだけで、段を 1 つだけ走らせ直しても自己完結にしておく。戻し方: 各段の run を remote_check の前置き(`python3 $HOME/dotfiles/agentcli/src/agentcli/remote_check.py --node zeus --tree . --label doeff-gate-full --sync-timeout 900 --run-timeout 3600 --fallback-local -- sh -c \"…\"`・lint の段は label doeff-gate-lint)で包み直し(中の `$` は遠隔の sh が解くよう `\\$` に)、この R10 と law daily-stages-run-where-the-task-runs と deftest test-adr-doe-enforce-001-daily-stages-run-where-the-task-runs を外す。")]
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
     (law daily-populations-are-independent-and-complete
       :statement "for_all population p in {root_pytest, package_pytest, rust_cargo_test}: count(stages s in gate.full where invokes(s, p)) == 1 AND blocks_rest(s) => s == build AND (red(p) => executed(q) for_all q != p); visited(make_test_packages) == { pkg : exists test_*.py or test_*.hy in packages/pkg/tests outside fixtures } regardless of red(pkg); failure_names(package_pytest) are repo_root_relative; for_all test-named file f: under(f, population_roots) OR f in excluded_with_reason"
       :counterexamples
         [(counterexample
             "2026-09-24 の日次(断面 f271ae39): gate.full が root の pytest・make test-packages・make test-rust を && でつないだ 1 本の命令で、root の赤 3 本(test_effectful 2・test_semgrep_gate 1)で止まり、package と Rust の母集団は 1 本も走らなかった。台帳の処理ステージは full の 1 つだけで coverage = {complete: true, unexecuted: []} と記録した — 走っていない母集団が在るのに『全部測った』と名乗った")
          (counterexample
             "card acp:kanban-issue:ki-9fc7d4bca4dc の記憶の行: 読み手を見落とした変更が、日次の呼び手の無い木(packages/doeff-agents/tests)の緑のまま着地した — その木の赤は誰の日次にも載らない")
          (counterexample
             "make test-packages の loop の `|| exit 1`: 名前順の先頭の package が赤の日は、後ろの 26 package が 1 本も走らない(偽の runner で最初の package だけを赤にした実測 — 呼ばれた回数 1)")
          (counterexample
             "package の dir から pytest を走らせた失敗名: 要約の行は cwd からの相対なので `FAILED tests/test_cli.py::…` になり、同名の test file を持つ 4 組(test_cli・test_effect_handlers・test_effects・test_types)が package をまたいで衝突し、日次の道具が repo の根から撃ち直せない")
          (counterexample
             "期待する package の集合を Makefile の実走から取る固定のテスト: Makefile が root の testpaths に在る package を飛ばすと、訪ねた集合と期待が一緒に縮んで緑のまま残る(盲検 B)")])
     (law ledger-is-generated-from-the-tree
       :statement "ledger(tree) == inventory(tree) where inventory = sorted names of { defadr files, .semgrep.yaml rule ids, (law …) / (deftest …) / (defsemgrep …) forms read as Hy (not strings, comments, #_ forms) }; written only by check_enforcement_ledger --write; for_all commit c: hook passes c iff ledger(c) == inventory(c) OR (inventory(c) - inventory(HEAD)) == (ledger(c) - ledger(HEAD))"
       :counterexamples
         [(counterexample
             "2026-09-26 e15f9516: ADR-DOE-AGENTS-012 の冊(deftest 69・law 56・defsemgrep 5)と semgrep の規則 7 本を消した commit が台帳の数を据え置いたまま本線へ入り、以後 docs/adr に触る 10 便以上が無関係なずれで hook に落ちて SKIP / --no-verify で外した")
          (counterexample
             "2026-09-17 ed98775a: 並行する 2 便が台帳の deftest の数をどちらも 104 → 105 と刻み、rebase は同じ行の同じ変更として黙って 105 に合わせた(実数 106)— 数は git の merge で足し算にならない")
          (counterexample
             "字面の数え text.count(\"(law \"): ADR-DOE-AGENTS-006 と ADR-DOE-CLUSTER-001 の註の中の綴りが law に数えられ、2026-09-28 の本線で law 134(形は 132)・deftest 82(形は 80)。註を 1 行足すだけで台帳が赤になる")])
     (law daily-stages-run-where-the-task-runs
       :statement "for_all stage s in gate.full: not ships_tree_to_another_machine(s) (no remote_check, no --node) AND for_all assignment UV_CACHE_DIR=v in run(s): v defers to the caller's UV_CACHE_DIR"
       :counterexamples
         [(counterexample
             "2026-10-10 16:18 の atlas の最初の doeff の日次(log doeff-20261010-071839-31779・断面 f858bd96): task は atlas の doeff-worker の Pod で走ったが、6 段とも `remote-check: ran=remote node=zeus` — root の pytest 1091 秒・18 package の検 2352 秒・cargo test 200 秒が zeus の CPU に載った(利用者の決定 2026-10-05『zeus は会話の速さを一番にする』に反する)")
          (counterexample
             "UV_CACHE_DIR を `${XDG_CACHE_HOME:-$HOME/.cache}/uv-lanes/land-doeff` で上書きしたまま task の中で走らせる — task の HOME は走行ごとに空なので、日次の 4 段がどれも冷えた cache から依存を取り直す(worker の共有の cache は呼び手の UV_CACHE_DIR に在るのに使わない)")])]
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
       ;; 2026-09-29(#980): 配線は root の conftest.py が oracle を実行時に有効にする形。この走行で
       ;; 有効であること(= 配線が効いていること)と、make sync が build を feature で分けないことを見る。
       (import pathlib [Path])
       (import doeff_vm.doeff_vm [invariant_checks_enabled])
       (setv root (get (. (Path __file__) parents) 2))
       (setv conftest (.read-text (/ root "conftest.py")))
       (setv makefile (.read-text (/ root "Makefile")))
       (assert (in "\"vm_invariant_checks\"" conftest)
               "root の conftest.py が VM の oracle を有効にしていない — ADR-DOE-ENFORCE-001 R4")
       (assert (invariant_checks_enabled)
               "この pytest の走行で VM の oracle が無効 — ADR-DOE-ENFORCE-001 R4")
       (assert (not-in "maturin develop --release --features" makefile)
               "make sync が VM の build を feature で分けている — 2 経路の build の食い違いに戻る(#980)"))
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
       (assert (in "\"deadline_scale_cap\"" conftest)
               "係数に上限が無い — 真の hang が有界時間で落ちなくなる")
       (assert (in "\"deadline_scale\"" conftest)
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
       ;; 第 3 の締切(全部のテストを撃つ走行そのものの持ち時間)が、内側を上限まで
       ;; 伸ばした走行を収容できること — 据え置くと赤の場所が移るだけになる。
       ;; 全部のテストは日次の全体検証([gate].full)だけが撃つので、持ち時間はその予算の鍵
       ;; full_timeout_s(agora-redesign #2589 — 窓の門の上限 timeout_s とは別の鍵になった)。
       (import tomllib)
       (setv land-cfg (tomllib.loads (.read-text (/ root ".agents/land-queue.toml")
                                                 :encoding "utf-8")))
       (setv gate-budget (get land-cfg "gate" "full_timeout_s"))
       (assert (>= gate-budget 7200)
               f"日次の全体検証の持ち時間 {gate-budget}s は内側を上限まで伸ばした走行を収容できない — ADR-DOE-ENFORCE-001 R6(締切は 3 つあり族として直す)"))
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
       ;; R9: 台帳は生成物 — 生成の口(--write)と make の入口の実在。
       (assert (in "--write" (.read-text checker :encoding "utf-8"))
               "勘定の家に台帳の生成の口 --write が無い — R9(台帳を手で書く形への退行)")
       (assert (in "enforcement-ledger:" makefile)
               "Makefile に台帳の生成の入口 enforcement-ledger が無い — R9")
       ;; 挙動本体の現存 pin(削除退行を loud にする)。
       (assert (.exists (/ root "tests/test_enforcement_ledger_hook.py"))
               "R7 の挙動本体 tests/test_enforcement_ledger_hook.py が消えている")
       ;; 既定 pytest の R5 検査が同じ家を消費していること。
       (setv r5-test (.read-text (/ root "tests/test_enforcement_ledger.py") :encoding "utf-8"))
       (assert (in "check_enforcement_ledger" r5-test)
               "R5 検査が勘定の家を消費していない — 第 2 定義点への退行"))
     (deftest test-adr-doe-enforce-001-daily-populations-are-separate-stages
       ;; R8 + law daily-populations-are-independent-and-complete: 宣言の形の pin。
       ;; 挙動の本体は tests/test_daily_test_population.py(package の loop の網羅・
       ;; 失敗名が repo の根からの相対・test file の置き場・package の pytest の設定)。
       ;; 段の run の中の命令は、遠隔の前置き(remote_check … -- sh -c "…")の内側を
       ;; && / || / ; で区切って 1 命令ずつ判じる — run の文字列全体の部分一致で判じると、
       ;; 2 つの母集団を 1 段に畳んだ宣言を正しい文言で赤にできない(設計段の試作の欠陥)。
       (import re shlex tomllib)
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       (setv land-cfg (tomllib.loads (.read-text (/ root ".agents/land-queue.toml")
                                                 :encoding "utf-8")))
       (setv full (get land-cfg "gate" "full"))
       (assert (isinstance full list)
               f"gate.full が処理ステージの列でない({(. (type full) __name__)})— 1 本の命令では 1 つの母集団の赤が後ろの母集団を未実行にする(2026-09-24 断面 f271ae39)— ADR-DOE-ENFORCE-001 R8")
       (defn stage-commands [run]
         (setv words (shlex.split run)
               script run)
         (for [i (range (- (len words) 2))]
           (when (and (= (get words i) "sh") (= (get words (+ i 1)) "-c"))
             (setv script (get words (+ i 2)))
             (break)))
         (lfor part (re.split r"&&|\|\||;" script)
               :if (.strip part)
               (.strip part)))
       (defn command-kind [command]
         (setv words (list (shlex.split command)))
         (while (and words (re.fullmatch r"[A-Za-z_][A-Za-z0-9_]*=.*" (get words 0)))
           (.pop words 0))
         (cond (not words) None
               (= (cut words 0 2) ["make" "sync"]) "sync"
               (and (= (get words 0) "make") (in "test-packages" words)) "packages"
               (and (= (get words 0) "make") (in "test-rust" words)) "rust"
               (and (!= (get words 0) "make") (in "pytest" words)) "root"
               True None))
       (setv populations ["root" "packages" "rust"])
       (setv names (lfor stage full (get stage "name")))
       (assert (= (len (set names)) (len names))
               f"gate.full の処理ステージの名前が重複している: {names} — R8")
       (setv kinds-by-stage
             (dfor stage full
                   (get stage "name")
                   (lfor command (stage-commands (get stage "run")) (command-kind command))))
       ;; 先頭は後ろを止める build の段で、母集団を呼ばない。
       (setv build (get full 0)
             build-name (get build "name"))
       (assert (and (= build-name "build") (is (.get build "blocks_rest") True))
               f"gate.full の先頭が blocks_rest = true の build の段でない: {build-name} — R8")
       (assert (in "sync" (get kinds-by-stage "build"))
               "build の段が make sync を呼ばない — R8")
       (setv build-pops (lfor k (get kinds-by-stage "build") :if (in k populations) k))
       (assert (not build-pops)
               f"build の段(後ろを止める段)が母集団 {build-pops} を呼ぶ — その赤が他の母集団を未実行にする — R8")
       ;; build の後ろの段は後ろを止めない。
       (setv blocking (lfor stage (cut full 1 None) :if (.get stage "blocks_rest") (get stage "name")))
       (assert (not blocking)
               f"build 以外の段 {blocking} が blocks_rest を立てている — その赤が後ろの母集団を未実行にする — R8")
       ;; 各母集団はちょうど 1 つの段が呼ぶ・1 つの段は 2 つの母集団を呼ばない。
       (for [pop populations]
         (setv owners (lfor #(name kinds) (.items kinds-by-stage) :if (in pop kinds) name))
         (assert (= (len owners) 1)
                 f"母集団 {pop} を呼ぶ処理ステージが {owners}(ちょうど 1 つでない)— R8"))
       (for [#(name kinds) (.items kinds-by-stage)]
         (setv pops (sorted (set (lfor k kinds :if (in k populations) k))))
         (assert (<= (len pops) 1)
                 f"処理ステージ {name} が 2 つ以上の母集団 {pops} を呼ぶ — 前の母集団の赤が後ろを未実行にする — R8")
         ;; テストの段は自己完結: 母集団の命令より前に make sync を持つ。
         (when pops
           (setv first-pop (min (lfor #(i k) (enumerate kinds) :if (in k populations) i)))
           (assert (in "sync" (cut kinds 0 first-pop))
                   f"処理ステージ {name} が母集団の命令の前に make sync を持たない — 遠隔の同期は段ごとに target/ を消し、段は 1 つずつ手元へ倒れ得る — R8")))
       ;; 挙動本体の現存 pin(削除退行を loud にする)。
       (assert (.exists (/ root "tests/test_daily_test_population.py"))
               "R8 の挙動本体 tests/test_daily_test_population.py が消えている"))
     (deftest test-adr-doe-enforce-001-daily-stages-run-where-the-task-runs
       ;; R10 + law daily-stages-run-where-the-task-runs: 宣言の形の pin。処理ステージの run は木を別の機体へ送る前置き
       ;; (remote_check・--node)を持たず、UV_CACHE_DIR の代入はどれも呼び手の UV_CACHE_DIR を先に使う。
       ;; 走る所の道具の用意(redis-server・基点の鍵の doeff-linter)の挙動は tests/test_gate_tools.py が持つ。
       (import re shlex tomllib)
       (import pathlib [Path])
       (val root (get (. (Path __file__) parents) 2))
       (val land-cfg (tomllib.loads (.read-text (/ root ".agents/land-queue.toml")
                                                 :encoding "utf-8")))
       (val full (get land-cfg "gate" "full"))
       (val shipped (lfor stage full
                           :if (or (in "remote_check" (get stage "run"))
                                   (in "--node" (shlex.split (get stage "run"))))
                           (get stage "name")))
       (assert (not shipped)
               (.format "処理ステージ {} が木を別の機体へ送って走らせる(remote_check の前置き)— 日次の task が走っている所で直に走らせる(利用者の決定 2026-10-05『zeus は会話の速さを一番にする』・card ki-9338eec1d15e)— ADR-DOE-ENFORCE-001 R10"
                        shipped))
       (val deferring "${UV_CACHE_DIR:-")
       (val cold (lfor stage full
                        value (re.findall r"UV_CACHE_DIR=(\S+)" (get stage "run"))
                        :if (not (.startswith value deferring))
                        #((get stage "name") value)))
       (assert (not cold)
               (.format "処理ステージの UV_CACHE_DIR が呼び手の UV_CACHE_DIR を使わない: {} — task の HOME は走行ごとに空なので HOME の下の lane は毎回冷える。`UV_CACHE_DIR={}…}}` の形にする — ADR-DOE-ENFORCE-001 R10"
                        cold deferring)))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
