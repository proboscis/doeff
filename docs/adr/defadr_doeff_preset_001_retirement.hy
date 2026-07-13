;;; Executable ADR: doeff-preset を退役する — 「見えるログ」の既定は slog_handler が正典、
;;; config の既定値はエントリポイント env の責務。バンドル済みプリセットという層は不要になった。

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-PRESET-001
  :title "doeff-preset パッケージを退役する — workspace から削除、publish マトリクスから除外、doeff_preset import は全面禁止。preset が担っていた表示既定は slog_handler(ADR-DOE-CORE-EFFECTS-001 R2)、config Ask 既定はエントリポイントの reader/lazy_ask env に移管済み"
  :status "proposed"
  :scope ["packages/doeff-preset"
          "packages/doeff-agents/examples"
          "packages/doeff-agentic/examples"
          ".github/workflows/publish.yml"
          "docs/release-publish-runbook.md"]
  :problem
    [(fact
       "doeff-preset は現行 API に対して既に壊れている: 自身の docstring とテストが削除済み default_handlers を前提とし(doeff/__init__.py:183 で _Removed)、テスト 17 件が失敗する pre-existing 負債として 2026-07-13 の slog 統合時に棚卸しされた。log_display は ADR-DOE-CORE-EFFECTS-001 R6 で .msg/.kwargs へ修理されたが、それは延命であって存在理由の回復ではない。"
       :evidence "packages/doeff-preset/src/doeff_preset/__init__.py:9; docs/doeff-2026-07-13-slog-semantics-architecture-plan.md の pre-existing 負債節; doeff/__init__.py:183")
     (fact
       "利用者はゼロ: src コードからの doeff_preset import は本体・全 packages に存在せず、参照は doeff-agents/doeff-agentic の examples 14 ファイル(それ自体が削除済み run_program(scoped_handlers=...) / default_handlers を併用する bit-rot 済みデモ)と pyproject の依存宣言のみ。ローカルの他リポジトリにも import は無い。"
       :evidence "rg 'doeff_preset' packages/*/src → 0 件(2026-07-13 実測); packages/doeff-agents/examples/01_basic_session.py:131 の run_program は doeff-agents src に不在")
     (fact
       "preset の本務だった『とりあえず動く表示』は slog 統合設計で本体側の既定になった: slog_handler が stderr terminal sink として『入れたらログは見える』を契約し、CLI default_interpreter も標準装備する。config 既定は reader/lazy_ask の env 注入で足りる。つまり『バンドル済みプリセット』という第 3 の層に残る責務が無い。"
       :evidence "docs/adr/defadr_doeff_core_effects_001_slog_observability.hy R2/R4; packages/doeff-core-effects/doeff_core_effects/handlers.py の slog_handler")]
  :context
    [(interpretation
       "preset は『core を薄く保ちつつ体験を良くする』ための緩衝層だったが、その動機は sink のデフォルト意味論が壊れていた(slog が黙る)ことへの補償だった。根本原因が ADR-DOE-CORE-EFFECTS-001 で直った今、preset は補償器(compensator)の遺物であり、維持するほど handler 合成の正典 idiom(installer を明示的に重ねる)と競合する第 2 の入口を作ってしまう。")
     (interpretation
       "退役は『公開 API の削除』なので、削除した事実そのものを enforcement 付きで記録する必要がある — .log 属性が enforcement 不在のまま非公式に消えて再生した erosion(ADR-DOE-CORE-EFFECTS-001 problem)と同じ轍を踏まない。")]
  :decision
    [(rule R1 "packages/doeff-preset を削除し、root/doeff-agents/doeff-agentic/doeff-conductor の pyproject から依存・workspace source・examples extra を除去する。uv.lock を再生成し、doeff_preset は importable でなくなる。")
     (rule R2 "doeff_preset の import は全面禁止(削除済み API の正式記録)。表示の既定が欲しい場所は slog_handler(program) を合成し、config Ask の既定値は reader(env=...) / lazy_ask(env=...) で与える。preset_handlers 相当の一括合成が必要なら、利用側が installer を明示的に重ねる。")
     (rule R3 "publish 面からも退役する: .github/workflows/publish.yml のビルドマトリクスと docs/release-publish-runbook.md の公開手順から doeff-preset を除外する。PyPI 上の既発行版は残るが、新規リリースは行わない。")
     (rule R4 "examples の移行は preset 参照の除去に限定する: preset_handlers()(prog) → slog_handler(prog)、scoped_handlers=(preset_handlers(),) は削除して program 側を slog_handler で包む。examples が併用している別の削除済み API(run_program / default_handlers)の修理は本 ADR のスコープ外の pre-existing 負債として別 issue で扱う。")]
  :laws
    [(law preset-stays-retired
       :statement "not importable(doeff_preset) and not in_publish_matrix(doeff-preset) and no_tracked_code_reference(doeff_preset)"
       :counterexamples
         [(counterexample "便利だからと doeff_preset を部分復活させて examples から import する(第 2 の合成入口の再生)")
          (counterexample "publish.yml のマトリクスに doeff-preset が残り、退役後も PyPI へ新版が出る")])
     (law display-default-owned-by-slog-handler
       :statement "wants_visible_logs(entrypoint) => composes(slog_handler); no_bundle_package_reintroduced"
       :counterexamples
         [(counterexample "『初心者向けに全部入り handler』パッケージを新設して sink の既定意味論を再び外部化する")])]
  :enforcement
    ;; 実 pytest は tests/test_doeff_preset_retired.py(3 tests: not importable /
    ;; package dir 不在 / tracked 参照ゼロ — 公開 runbook は明示 denylist)。
    ;; installed 版 semgrep rule は .semgrep.yaml の no-doeff-preset-import。
    ;; ここは静的ガードの inline 自己検証。
    [(defsemgrep no-doeff-preset-import
       :languages ["python"]
       :pattern "from doeff_preset import $X"
       :message "doeff-preset は ADR-DOE-PRESET-001 で退役済み。表示既定は slog_handler、config 既定は reader/lazy_ask env。"
       :bad ["from doeff_preset import preset_handlers"]
       :good ["from doeff_core_effects.handlers import slog_handler"])]
  :plans ["docs/doeff-2026-07-13-slog-semantics-architecture-plan.md"])
