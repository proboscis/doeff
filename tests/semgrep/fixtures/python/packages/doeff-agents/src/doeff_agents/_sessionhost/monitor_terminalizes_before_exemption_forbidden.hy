;;; semgrep hit fixture: doeff-agents-interactive-must-not-be-terminalized
;;; (ADR-DOE-AGENTS-007 R3 -- a monitor arm that writes status as
;;; terminal before the exemption check (reap-exempt) is a forbidden form. The shape of the pre-fix policy.hy).

(defk monitor-session-once [row knobs]
  (<- now (clock-now))
  (setv observed-at (iso-format now))
  ;; BAD: an arm that terminalizes before the exemption check
  (when (= row.status "booting")
    (setv row (replace row :status "failed"))
    (return row))
  (when stale
    (setv row (replace row :status "exited"))
    (return row))
  row)
