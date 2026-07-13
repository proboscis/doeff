(require doeff-hy.macros [deftest])

(import pathlib [Path])
(import doeff-agents.adapters.base [InjectionMethod LaunchParams])
(import doeff-agents.adapters.claude [ClaudeAdapter])
(import doeff-agents.adapters.codex [CodexAdapter])


(setv _PROMPT-SENTINEL
  "PROMPT_MUST_BE_SENT_AFTER_LAUNCH_THROUGH_TERMINAL_TRANSPORT")


(defn _assert-prompt-not-in-argv [argv]
  (setv joined (.join " " argv))
  (assert (not (in _PROMPT-SENTINEL argv)))
  (assert (not (in _PROMPT-SENTINEL joined))))


(deftest test-claude-adapter-launches-interactive-terminal-session
  (setv adapter (ClaudeAdapter))
  (setv params
    (LaunchParams
      :work-dir (.cwd Path)
      :prompt _PROMPT-SENTINEL
      :model "opus"))
  (setv argv (.launch-command adapter params))
  (assert (= adapter.injection-method InjectionMethod.TMUX))
  (assert (= argv ["claude"
                   "--ax-screen-reader"
                   "--dangerously-skip-permissions"
                   "--permission-mode"
                   "bypassPermissions"
                   "--model"
                   "opus"]))
  (_assert-prompt-not-in-argv argv)
  (assert (not (in "-p" argv)))
  (assert (not (in "--print" argv))))


(deftest test-codex-adapter-launches-interactive-terminal-session
  (setv adapter (CodexAdapter))
  (setv params
    (LaunchParams
      :work-dir (.cwd Path)
      :prompt _PROMPT-SENTINEL
      :model "gpt-5"
      :effort "xhigh"))
  (setv argv (.launch-command adapter params))
  (assert (= adapter.injection-method InjectionMethod.TMUX))
  (_assert-prompt-not-in-argv argv)
  (assert (not (in _PROMPT-SENTINEL (get argv -1)))))
