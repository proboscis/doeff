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
       ".semgrep.yaml の 229 ルール(2026-07-14 実測)は `make lint-semgrep` の手動起動のみで、どの自動ゲートも実行しない。semgrep は dev 依存ですらない。姉妹リポジトリ ACP では 2026-07-11 の初回ゲート実行で 16 検査が即 red になった(semgrep バイナリ不在)— 『書かれたが走らない』検査は牙があっても偽の安心を生む。"
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
     (rule R5 "anti-drop ratchet: enforcement 台帳(defadr 数・law 数・defsemgrep 数・deftest enforcement 数)が黙って減ったら fail するメタテストを既定収集に置く(orch SpecInventorySpec の pytest 版)。台帳の意図的な削減は台帳ファイルの明示的更新を伴う。")]
  :laws
    [(law default-pytest-sees-all-enforcement
       :statement "for_all declared_enforcement e: collected_by(default_pytest, e) AND (missing_dependency(e) => hard_fail, not skip)"
       :counterexamples
         [(counterexample "defadr 10本が testpaths 外で沈黙している現状 — `uv run pytest` は enforcement ゼロのまま緑")
          (counterexample "semgrep 不在の環境で defsemgrep が skip され、229 ルール全滅のままスイートが緑")
          (counterexample "invariant-checks 無効のリリースビルドに対して oracle テストが skip され、VM 不変量が未検証のまま緑")])]
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
               "Makefile に invariant-checks の配線が無い — ADR-DOE-ENFORCE-001 R4(B3 裁定 2026-07-14)"))]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
