;;; Semgrep fixture: doeff-agents-idle-shell-vocabulary-is-policy-owned.
;;;
;;; A substrate reports the pane's foreground command (the process group
;;; leader) and leaves the idle-shell judgement to policy.hy. Choosing "the
;;; first process that is not an idle shell" inside the herdr substrate moves
;;; that judgement out of policy (design check 2026-09-26, blind B). The two
;;; lines that name the vocabulary below are the banned shape.

(import doeff_agents.sessionhost.policy [IDLE-SHELL-COMMANDS])

(defk herdr-foreground-command [result]
  (setv commands (herdr-process-commands result))
  (match (lfor command commands :if (not-in command IDLE-SHELL-COMMANDS) command)
    [chosen #* _rest] chosen
    _ (get commands 0)))
