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
          "Makefile"
          ".semgrep.yaml"
          "packages/doeff-adr/src/doeff_adr"
          "packages/doeff-vm-core/Cargo.toml"
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
     (rule R4 "dev ビルドは doeff-vm-core を feature `invariant-checks` + `python_bridge` 有効でビルドする(2026-07-14 B3 裁定)。VM conformance oracle(I1–I8)は pytest から起動される。invariant-checks 無効ビルドでの oracle テストは hard fail(skip 禁止)。")
     (rule R5 "anti-drop ratchet: enforcement 台帳(defadr 数・law 数・defsemgrep 数・deftest enforcement 数)が黙って減ったら fail するメタテストを既定収集に置く(orch SpecInventorySpec の pytest 版)。台帳の意図的な削減は台帳ファイルの明示的更新を伴う。")
     (rule R6 "ゲートの壁時計締切は定数でなく機械の過負荷率の関数にする(2026-08-17 追加 — operator 裁定 decision-doeff-land-gate-deadline-2026-08-17.html『A. 締切を直す』)。締切が捕まえるべきものは hang であって busy ではない。外部プロセス(semgrep・CLI・build)を待つ試験の所要は過負荷率に比例して伸びるので、定数の締切は過負荷帯で『正しい仕事に赤を出す装置』へ退化する。係数 = 1 分平均 load / コア数(下限 1.0・上限 env PYTEST_DEADLINE_SCALE_CAP 既定 8 — 上限があるので真の hang は依然として有界時間で落ちる)。無効化は env PYTEST_DEADLINE_SCALE=off(負荷を自分で制御する CI 用)。【2 つの締切は必ず一緒に動かす】pytest-timeout の per-test 締切と、その上に立つ SIGKILL watchdog の両方が同じ係数で伸び、watchdog は常に per-test 締切より厳密に上に居ること — 片方だけ上げると『遅い試験 1 本が赤くなる』が『走行ごと SIGKILL で全損する』に化ける(実測 2026-08-17: PYTEST_TIMEOUT=600 を素の 90 秒 watchdog に当てて全数電池が 45% で即死)。marker の締切(@pytest.mark.timeout)も同じ係数で伸ばす — pytest-timeout は marker を ini より優先するので、ini だけ伸ばすと『自分は遅いと申告した試験』= 外部プロセスを起こす当の試験群が素の締切に取り残される。【伸ばしたことは黙らない】係数が 1 を超えた走行は伸ばした旨と実効値を stderr に出す(伸びた締切は同時に『この機械は過負荷である』の信号でもあり、8 倍かかった走行が黙って緑を返すのは観測の欠落)。【締切は 3 つある】内側 2 つ(per-test・watchdog)に加え、門の走行そのものの持ち時間(.agents/land-queue.toml の gate.timeout_s)が第 3 の締切である。内側を伸ばせば走行の総時間は必然的に伸びる(60 秒で落ちていた試験が数百秒まで走れるようになったのだから当然で、欠陥ではなく設計)ので、第 3 の締切を据え置くと内側の修理は『1 テストの赤』を『走行全体の時間切れ』へ移し替えるだけになる — 実測 2026-08-17: 内側だけ直した便が 2710.7 秒で門の 2700 秒に当たった(内訳 = Rust 再 build 約 12 分 + 電池 33 分超・load 約 100 帯)。第 3 の締切は連邦の機構(dotfiles land.py)が読む静的な宣言で負荷に連動する口を持たないため、係数が上限に張り付いた走行でも終われる値を宣言で置き、その根拠を宣言の隣に書く(2026-08-17 時点 7200)。")]
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
             "内側の 2 つだけ直して門の持ち時間(gate.timeout_s)を据え置く — 内側を伸ばせば走行の総時間は必ず伸びるので、赤の場所が『1 テストの timeout』から『走行全体の時間切れ』へ移るだけで、着地はやはり通らない(実測 2026-08-17: 内側だけ直した便が 2710.7 秒で 2700 秒の門に当たった)。締切は 3 つあり、族として直す")])]
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
               f"門の持ち時間 {gate-budget}s は内側を上限まで伸ばした走行を収容できない — ADR-DOE-ENFORCE-001 R6(締切は 3 つあり族として直す)"))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
