;;; Semgrep fixture: doeff-agents-herdr-label-holders-must-not-be-indexed.
;;;
;;; A herdr label's workspace holders are a set with no distinguished element:
;;; workspace ids are not monotone in creation order (the counter carries from
;;; letters into digits — w3NZ -> w3N0, w3ZZ -> ... -> w303; probe 2026-08-14).
;;; Indexing the set picks an arbitrary workspace, which is how PR #587's first
;;; revision orphaned a live session and killed the wrong workspace. The line
;;; below is the banned shape and must keep firing the rule.

(deff herdr-kill-session-io [socket-path session-name]
  (setv holders (herdr-label-workspace-ids-io socket-path session-name))
  (herdr-call socket-path "workspace.close" {"workspace_id" (get holders 0)}))
