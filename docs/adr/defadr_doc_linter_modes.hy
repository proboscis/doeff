(require doeff-adr.macros [defadr deftest rule law])
(import doeff-adr.macros [fact interpretation counterexample])

(defadr ADR-DOC-LINTER-SELECTABLE-SCOPE
  :title "文章・用語パネルの右上から検査範囲を3択で選択する"
  :status "accepted"
  :scope ["ide-plugins/vscode/doeff-runner/src/lint" "ide-plugins/vscode/doeff-runner/package.json"]
  :problem [(fact "operator 2026-10-05 Asia/Tokyo: I said add a selectable option so i can either 1. only run on opened file 2. run on the whole project files. 3. turn off")
            (fact "同日の補足: by 'tab' imean a button like this.[Image #1] see the 3 icons on top right?")]
  :context [(fact "会話 Codex 01a109cb-0dd6-7f83-aab7-76ac64a40830。追跡 proboscis/agora-redesign#3571、親 #3264。")
            (interpretation "検出精度を少数の実例で評価する間も、必要に応じて全体検査や完全停止を自分で選べる必要がある。")]
  :decision [(rule single-setting "docLint.mode の openFiles / workspace / off を設定の正本とし、右上の filter ボタンから選ぶ。選択は workspace ごとに保存する。")
             (rule default-open-files "既定は開いているファイルのみ。タブにない文書を内部 API が読み込んでも検査対象にしない。用語の定義と参照も選択した範囲に限る。")
             (rule changes-and-stop "全体モードの初回以後は変更ファイルを差分検査する。範囲変更では古い検査を中断し、オフでは予約を消す。永続キャッシュは削除しない。")
             (rule rollback "前の版へ戻す場合は先に docLint.enabled=false を設定する。旧版は mode を解釈せず、起動時に全体検査するため。新版では off を選べば停止できる。")]
  :laws [(law selected-files-only
           :statement "openFiles の起動と編集では、閉じたファイルの列挙・読取・推論をしない。"
           :counterexamples [(counterexample "全体の索引を作ってから開いたファイルだけを表示する。")])
         (law off-cancels-work
           :statement "off への変更は実行中のプロセスと予約を停止し、未検査を合格として表示しない。"
           :counterexamples [(counterexample "パネルを空にするだけで Jev への要求を続ける。")])
         (law selection-survives-reload
           :statement "選択したモードは再読み込み後も維持され、同じ文章の判定は永続キャッシュから得る。"
           :counterexamples [(counterexample "再読み込みで全体検査に戻る。")])]
  :enforcement [(deftest test-doc-lint-modes-in-vscode [machine-tool]
                  (import os pathlib subprocess)
                  ;; 実物の VS Code が要る検。起動できる VS Code の無い機体(日次の worker)では、根の conftest の
                  ;; machine_tool が「tool-absent」として未実行に名指す — 緑とは数えず、黙って skip にもしない
                  ;; (agora-redesign #3582)。VS Code の在る機体では、ここから先は今までどおり走る。
                  (machine-tool (os.environ.get "DOC_MODES_VSCODE" "code") "--version")
                  (setv root (/ (. (pathlib.Path __file__) parent parent parent) "ide-plugins/vscode/doeff-runner"))
                  (setv result (subprocess.run ["node" "scripts/run-doc-modes-test.mjs"] :cwd root :check False))
                  (assert (= result.returncode 0)))]
  :plans ["実機検証の環境: DOC_MODES_EXTENSION=検証対象の拡張、DOC_MODES_BINARY=doc-linter、DOC_MODES_VSCODE=Code実行ファイル、DOC_MODES_EVIDENCE=新しい結果ファイル。"
          "Jev 通信だけをローカル HTTP に置換する。実 CLI・VS Code・永続キャッシュ・右上ボタンと同じ選択 UI・再読み込みを通す。"
          "条件付き指示: dotfiles agent/jevrules/rules/doc_linter.hy。"])
