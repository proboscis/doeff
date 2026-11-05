;;; semgrep hit fixture: doeff-agents-turn-rpc-must-not-touch-substrate
;;; (ADR-DOE-AGENTS-007 R5 -- turn RPC is row lookup + upsert only. The shape
;;; that calls a substrate effect breaks the <=200ms fire-and-forget contract and is forbidden).

(deff db-turn-stamp [conn pane-id]
  ;; BAD: touches the substrate on the turn stamping path (even a probe is forbidden)
  (<- alive (tmux-has-session session-name))
  (<- _ (tmux-capture pane-id 100))
  (<- _ (deliver-message pane-id "stamp"))
  None)
