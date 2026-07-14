;;; Executable ADR: defadr の存在と pytest collection の配線を自己検査する。

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-ADR-001
  :title "実行可能 ADR の存在と実際の pytest collection を全量照合する"
  :status "accepted"
  :scope ["doeff-adr" "pytest collection" "CI wiring"]
  :problem
    [(fact
       "doeff・proboscis-ema・agent-control-plane の3リポジトリで defadr_*.hy が存在しても testpaths または CI 引数の外に置かれ、検査が無音で不活性化した。"
       :evidence "docs/crystallization/erosion-audit-2026-07-02.md")
     (fact
       "pytest_collect_file は pytest が走査したファイルだけを受け取るため、collection root 外の defadr をプラグイン単独では従来発見できなかった。"
       :evidence "packages/doeff-adr/src/doeff_adr/pytest_plugin.py")]
  :context
    [(interpretation
       "設定ファイルを静的に推測するより、リポジトリ全体の候補と session.items の実測値を比較すれば testpaths・明示引数・ignore hook を同じ規則で扱える。")
     (interpretation
       "既存利用者への導入時の誤爆を避けるため通常 pytest は警告とし、CI 用コマンドだけを strict にする。")]
  :decision
    [(rule R1 "pytest collection 完了時に、リポジトリ内の実行可能 ADR 候補と実際に収集された item のファイル集合を照合する。")
     (rule R2 "既定モードは warn とし、strict では未収集 ADR を列挙して非ゼロ終了する。明示的な off は局所実行用に残す。")
     (rule R3 "doeff-adr verify-wiring は strict の collect-only pytest を起動し、CI で一行の配線ゲートとして使えるようにする。")
     (rule R4 "defsemgrep は semgrep executable 不在を skip にせず fail-closed のまま維持する。wiring strict とは別の実行時依存検査として扱う。")]
  :laws
    [(law every-executable-adr-is-collected
       :statement "exists(defadr_file) => collected_by_configured_pytest_scope(defadr_file)"
       :counterexamples
         [(counterexample "docs/adr/defadr_*.hy が存在するが testpaths は tests のみ")
          (counterexample "CI が tests だけを明示して docs/adr を走査しない")])
     (law strict-wiring-fails-closed
       :statement "uncollected_defadr and wiring_mode(strict) => nonzero_exit_with_paths"
       :counterexamples
         [(counterexample "未収集 ADR があっても collected test の成功だけで CI が緑になる")])]
  :enforcement
    [(defsemgrep no-silent-off-wiring-default
       :languages ["generic"]
       :pattern "default=\"off\""
       :message "doeff-adr wiring must not silently default to off."
       :bad ["parser.addini(\"doeff_adr_wiring\", default=\"off\")"]
       :good ["parser.addini(\"doeff_adr_wiring\", default=\"warn\")"])]
  :plans ["packages/doeff-adr/tests/test_wiring.py"
          "packages/doeff-adr/README.md"
          ".github/workflows/python-compatibility.yml"])
