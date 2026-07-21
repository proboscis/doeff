;;; semgrep hit fixture: doeff-agents-adopt-must-not-mutate-substrate
;;; (ADR-DOE-AGENTS-007 R2 — adopt は observation-only。substrate を
;;; 変異させる adopt program は禁止形)。

(defk adopt-program [params]
  ;; BAD: adopt の中で席へキーを送る(安全姿勢の変更)
  (<- _ (tmux-send-keys pane-id "Enter" False False))
  ;; BAD: adopt の中で session を作る/殺す
  (<- _ (tmux-new-session session-name work-dir {}))
  (<- _ (tmux-kill-session session-name))
  ;; BAD: adopt の中で FS を書く
  (<- _ (fs-write-text-atomic path text ".tmp"))
  row)
