;;; Semgrep fixture: doeff-agents-herdr-label-holders-must-not-be-indexed.
;;;
;;; A herdr label's workspace holders are a set with no distinguished element:
;;; workspace ids are not monotone in creation order (the counter carries from
;;; letters into digits — w3NZ -> w3N0, w3ZZ -> ... -> w303; probe 2026-08-14).
;;; Indexing the set picks an arbitrary workspace, which is how PR #587's first
;;; revision orphaned a live session and killed the wrong workspace. The line
;;; below is the banned shape and must keep firing the rule.

(defk herdr-kill-session-io [socket-path session-name]
  (setv holders (herdr-label-workspace-ids-io socket-path session-name))
  (herdr-call socket-path "workspace.close" {"workspace_id" (get holders 0)}))
;;;
;;; ⚠ The vocabulary (deff / defk) is not the point of this sample -- the rule's pattern is a regex on the holder-set subscript
;;; and does not depend on how the definition is spelled. Some functions in real code stay as deff, but
;;; this sample is listed in the ADR-DOE-HY-004 ledger (which also counts text in comments and strings), so
;;; it is written with defk. Line numbers are pinned by test_vm_failfast_semgrep_rules.py, so
;;; notes must always go at the end of the file (deff and defk are the same 4 characters, so lines do not shift).
