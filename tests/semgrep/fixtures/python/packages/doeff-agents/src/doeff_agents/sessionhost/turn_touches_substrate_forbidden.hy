;;; semgrep hit fixture: doeff-agents-turn-rpc-must-not-touch-substrate
;;; (ADR-DOE-AGENTS-007 R5 — turn RPC は行引き + upsert のみ。substrate
;;; effect を呼ぶ形は ≤200ms fire-and-forget 契約を壊す禁止形)。

(deff db-turn-stamp [conn pane-id]
  ;; BAD: turn 打刻経路で substrate を触る(probe すら禁止)
  (<- alive (tmux-has-session session-name))
  (<- _ (tmux-capture pane-id 100))
  (<- _ (deliver-message pane-id "stamp"))
  None)
