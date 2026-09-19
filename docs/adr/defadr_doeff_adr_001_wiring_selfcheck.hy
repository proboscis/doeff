;;; Executable ADR: defadr の存在と pytest collection の配線を自己検査する。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
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
       :evidence "packages/doeff-adr/src/doeff_adr/pytest_plugin.py")
     (fact
       "doeff 自身の既定ゲートへの組み込み(tests/test_adr_wiring_gate.py)は、テストの中から strict の `pytest --collect-only` を子 process で撃ち、全 suite をもう 1 回収集していた。日次の全体検証(pod)で 2026-09-15 から 3 日連続で、この 1 本が pytest-timeout の 60 秒に当たり、thread 方式なので process ごと落ちて summary 行の無い赤になった(進捗 約 28 % の地点・失敗名の抽出 0 件)。pod の実測(2026-09-17): 全 1,549 本の収集は bytecode cache が冷たいと 135 秒・温かいと 3.7 秒(Mac は冷 19 秒・温 4 秒)。着地の機構は子を PYTHONDONTWRITEBYTECODE=1 で走らせるので、日次の走行では cache が書かれず、入れ子の収集は毎回冷たい。冷たい収集の大半は Hy の ADR 1 ファイルの compile で(docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy — Mac で 19 秒中 13 秒)、このファイルが 09-14 の 165 KB(この検は通過)から 09-15 の 285 KB・09-17 の 400 KB へ育って締切を越えた。"
       :evidence "agora-redesign #467 根 3/9; agora-1-0 の land-runs/doeff-verify-20260917-033009.log; dotfiles agentcli land.py の _hygiene_env")]
  :context
    [(interpretation
       "設定ファイルを静的に推測するより、リポジトリ全体の候補と session.items の実測値を比較すれば testpaths・明示引数・ignore hook を同じ規則で扱える。")
     (interpretation
       "既存利用者への導入時の誤爆を避けるため通常 pytest は警告とし、CI 用コマンドだけを strict にする。")
     (interpretation
       "既定の起動(path 引数なし)の session は、既定の範囲をすでに収集し終えている。その収集結果そのものが測定値で、テストの中から 2 回目の収集を起こす理由が無い。収集の所要は suite の大きさと機体の遅さに比例するので、1 テストの締切の下に置けば、いつか必ず越える。")
     (interpretation
       "配線は収集の範囲の性質で、選択(-k / -m / --deselect)の性質ではない。doeff の正典ゲート自身が -m 'not e2e' で走る — 選択で落ちた ADR も範囲には届いている。")]
  :decision
    [(rule R1 "pytest collection 完了時に、リポジトリ内の実行可能 ADR 候補と実際に収集された item のファイル集合を照合する。収集されたファイルの集合は選択(-k / -m / --deselect)が効く前に測る(2026-09-17 追補)。")
     (rule R2 "既定モードは warn とし、strict では未収集 ADR を列挙して非ゼロ終了する。明示的な off は局所実行用に残す。")
     (rule R3 "doeff-adr verify-wiring は strict の collect-only pytest を起動する。外部リポジトリは CI 一行ゲートとして使える。doeff 自身は pytest 正典(ADR-DOE-ENFORCE-001 R1)に従い tests/test_adr_wiring_gate.py で既定ゲートに常時組み込む。")
     (rule R4 "defsemgrep は semgrep executable 不在を skip にせず fail-closed のまま維持する。wiring strict とは別の実行時依存検査として扱う。")
     (rule R5 "session の中から配線を検めるテスト(doeff の tests/test_adr_wiring_gate.py)は、走っている session 自身の収集結果を plugin の口 doeff_adr.pytest_plugin.default_scope_wiring で読む。テストの中から 2 回目の収集(入れ子の pytest)を起こさない(2026-09-17 追加 — agora-redesign #467 根 3/9 の根治)。照合は session につき 1 回で、収集完了時の報告(R1 / R2)とこの口は同じ測定値を読む。path を名指しした session は既定の範囲について何も言えない(狭ければ偽の赤・広ければ偽の緑)ので、口は NotDefaultScope を返し、テストは合格ではなく skip として理由を名乗る。手元で確かめる口は `uv run pytest -k <テスト名>`(既定の範囲・収集 1 回)か R3 の `doeff-adr verify-wiring`。")]
  :laws
    [(law every-executable-adr-is-collected
       :statement "exists(defadr_file) => collected_by_configured_pytest_scope(defadr_file)"
       :counterexamples
         [(counterexample "docs/adr/defadr_*.hy が存在するが testpaths は tests のみ")
          (counterexample "CI が tests だけを明示して docs/adr を走査しない")])
     (law strict-wiring-fails-closed
       :statement "uncollected_defadr and wiring_mode(strict) => nonzero_exit_with_paths"
       :counterexamples
         [(counterexample "未収集 ADR があっても collected test の成功だけで CI が緑になる")])
     (law gate-test-reads-the-sessions-own-collection
       :statement "in_session_gate_test(t) => reads(t, measured_collection(current_session)) AND NOT spawns(t, nested_collection); collected(session) measured_before selection(-k, -m, --deselect); explicit_path_args(session) => verdict(t) == NotDefaultScope, never green"
       :counterexamples
         [(counterexample
             "2026-09-15〜17 実弾: テストの中から全 suite の `pytest --collect-only` を子 process で撃つ形 — 日次の全体検証(pod・bytecode cache なし)で収集が 135 秒かかり、1 テスト 60 秒の締切に 3 日連続で当たった。pytest-timeout の thread 方式は process ごと落とすので、通っていた約 430 本の結果も summary 行も失われ、日次の判定が 3 日成立しなかった")
          (counterexample
             "締切だけを伸ばす — 収集の所要は suite の大きさに比例するので、伸ばした締切もいずれ越える。全 suite の収集を 1 走行で 2 回払う形が残る")
          (counterexample
             "testpaths に docs/adr が無いまま `pytest tests docs/adr` と名指しした session の収集結果を、既定の範囲の答えとして読む — 名指しが全 ADR を収集するので緑になるが、既定の `uv run pytest` では ADR が沈黙したまま")
          (counterexample
             "選択の後の session.items で照合する — 正典ゲートは -m 'not e2e' で走り、`-k` の焦点走も既定の範囲を収集する。選択で落ちた ADR を未収集と数えると、配線が正しいのに赤になる")
          (counterexample
             "名指しの session でテストを合格にする — 何も検めていない走行が緑を名乗る")])]
  :enforcement
    [(defsemgrep no-silent-off-wiring-default
       :languages ["generic"]
       :pattern "default=\"off\""
       :message "doeff-adr wiring must not silently default to off."
       :bad ["parser.addini(\"doeff_adr_wiring\", default=\"off\")"]
       :good ["parser.addini(\"doeff_adr_wiring\", default=\"warn\")"])
     (deftest test-adr-doe-adr-001-gate-test-spawns-no-nested-collection
       ;; R5 + law gate-test-reads-the-sessions-own-collection の実在 pin。
       ;; 挙動の実体は packages/doeff-adr/tests/test_wiring.py(既定の範囲の赤と緑・
       ;; 名指しの session は NotDefaultScope・選択の前に測る、の 4 本)。
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       (setv gate (.read-text (/ root "tests/test_adr_wiring_gate.py") :encoding "utf-8"))
       (assert (in "default_scope_wiring" gate)
               "tests/test_adr_wiring_gate.py が session 自身の収集結果(plugin の口)を読んでいない — ADR-DOE-ADR-001 R5")
       (assert (not (in "import subprocess" gate))
               "tests/test_adr_wiring_gate.py が子 process を起こしている — 入れ子の全収集への退行(ADR-DOE-ADR-001 R5)")
       (setv plugin (.read-text (/ root "packages/doeff-adr/src/doeff_adr/pytest_plugin.py")
                                :encoding "utf-8"))
       (assert (in "def default_scope_wiring(" plugin)
               "plugin の口 default_scope_wiring が消えている — ADR-DOE-ADR-001 R5"))
     (deftest test-adr-doe-adr-001-law-and-code-name-the-same-two-kinds
       ;; R1 + law every-gate-owned-file-is-collected の外延 pin。
       ;; 2026-09-19 実弾: plugin は pytest の python_files に一致する .py も
       ;; 測り始めたのに、法の主語は「実行可能 ADR」のままだった。法より広い
       ;; enforcement は、次に読む人が law を読んで code の挙動を取り違える形で
       ;; 効き続ける(現に「R1 の改訂だから触れない」と読まれた)。
       (import doeff-adr.registry [get-adr])
       (import pathlib [Path])
       (setv root (get (. (Path __file__) parents) 2))
       (setv plugin (.read-text (/ root "packages/doeff-adr/src/doeff_adr/pytest_plugin.py")
                                :encoding "utf-8"))
       (assert (in "python_files" plugin)
               "plugin が pytest の python_files を測らなくなった — code を狭めるなら法(R1・law)を同じ commit で狭めること")
       (setv spec (get-adr "ADR-DOE-ADR-001"))
       (setv r1 (next (gfor rule (. spec decision)
                            :if (= (.get rule "id") "R1")
                            (.get rule "text"))))
       (assert (in "python_files" r1)
               "R1 の主語が plugin より狭い — code は defadr_*.hy と pytest の python_files の 2 種を測っている")
       (setv statements (.join "\n" (gfor law (. spec laws) (.get law "statement" ""))))
       (assert (in "gate_owned_file" statements)
               "law の主語が実行可能 ADR だけのまま — 門が受け持つ 2 種を名乗ること"))]
  :plans ["packages/doeff-adr/tests/test_wiring.py"
          "packages/doeff-adr/README.md"
          "tests/test_adr_wiring_gate.py"
          "docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"])
