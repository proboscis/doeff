(require doeff-adr.macros [defadr deftest rule law])
(import doeff-adr.macros [fact interpretation counterexample])

(defadr ADR-DOC-LINTER-SELECTABLE-SCOPE
  :title "Select the check scope from three choices at the top right of the prose/terms panel"
  :status "accepted"
  :scope ["ide-plugins/vscode/doeff-runner/src/lint" "ide-plugins/vscode/doeff-runner/package.json"]
  :problem [(fact "The lint scope is selectable among three options: 1. only the opened files, 2. the whole project's files, 3. off.")
            (fact "The selector is a button in the row of icons at the top right of the panel.")]
  :context [(interpretation "Even while detection accuracy is being evaluated on a small number of real examples, the user must be able to choose a whole-project check or a full stop when needed.")]
  :decision [(rule single-setting "docLint.mode with openFiles / workspace / off is the source of truth for the setting, chosen from the filter button at the top right. The choice is saved per workspace.")
             (rule default-open-files "The default is open files only. A document that is not in a tab is not checked even if an internal API loads it. Term definitions and references are also limited to the selected scope.")
             (rule changes-and-stop "After the first run in workspace mode, changed files are checked incrementally. A scope change aborts the old check; off clears the scheduled work. The persistent cache is not deleted.")
             (rule rollback "Before rolling back to the previous version, set docLint.enabled=false first, because the old version does not interpret mode and runs a whole-project check at startup. In the new version, choosing off stops it.")]
  :laws [(law selected-files-only
           :statement "On openFiles startup and edits, closed files are not enumerated, read, or inferred over."
           :counterexamples [(counterexample "Build the whole-project index, then show only the open files.")])
         (law off-cancels-work
           :statement "Switching to off stops the running process and the scheduled work, and does not display unchecked items as passed."
           :counterexamples [(counterexample "Only empty the panel while continuing to send requests to Jev.")])
         (law selection-survives-reload
           :statement "The selected mode persists across reloads, and the verdict for the same text is obtained from the persistent cache."
           :counterexamples [(counterexample "A reload returns to the whole-project check.")])]
  :enforcement [(deftest test-doc-lint-modes-in-vscode [machine-tool]
                  (import os pathlib subprocess)
                  ;; A check that needs a real VS Code. On a machine without a launchable VS Code (the daily-verification worker),
                  ;; the root conftest's machine_tool names it as not run with "tool-absent" — not counted as green, and not silently skipped.
                  ;; On a machine with VS Code, everything from here on runs as before.
                  (machine-tool (os.environ.get "DOC_MODES_VSCODE" "code") "--version")
                  (setv root (/ (. (pathlib.Path __file__) parent parent parent) "ide-plugins/vscode/doeff-runner"))
                  (setv result (subprocess.run ["node" "scripts/run-doc-modes-test.mjs"] :cwd root :check False))
                  (assert (= result.returncode 0)))]
  :plans ["Environment for real-machine verification: DOC_MODES_EXTENSION=the extension under test, DOC_MODES_BINARY=doc-linter, DOC_MODES_VSCODE=the Code executable, DOC_MODES_EVIDENCE=a new result file."
          "Replace only the Jev communication with local HTTP. Exercise the real CLI, VS Code, the persistent cache, the same selection UI as the top-right button, and reload."
          "Conditional instruction: dotfiles agent/jevrules/rules/doc_linter.hy."])
