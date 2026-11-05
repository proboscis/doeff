;;; semgrep hit fixture: doeff-agents-adopt-must-not-mutate-substrate
;;; (ADR-DOE-AGENTS-007 R2 -- adopt is observation-only. An adopt program that
;;; mutates the substrate is a forbidden shape.)

(defk adopt-program [params]
  ;; BAD: send keys to the seat inside adopt (changes the safety posture)
  (<- _ (tmux-send-keys pane-id "Enter" False False))
  ;; BAD: create/kill a session inside adopt
  (<- _ (tmux-new-session session-name work-dir {}))
  (<- _ (tmux-kill-session session-name))
  ;; BAD: write the FS inside adopt
  (<- _ (fs-write-text-atomic path text ".tmp"))
  row)
