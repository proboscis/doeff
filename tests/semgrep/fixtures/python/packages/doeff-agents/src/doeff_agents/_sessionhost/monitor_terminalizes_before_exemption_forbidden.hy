;;; semgrep hit fixture: doeff-agents-interactive-must-not-be-terminalized
;;; (ADR-DOE-AGENTS-007 R3 — 免除判定(reap-exempt)より前に status を
;;; terminal へ書く monitor arm は禁止形。pre-fix policy.hy の形)。

(defk monitor-session-once [row knobs]
  (<- now (clock-now))
  (setv observed-at (iso-format now))
  ;; BAD: 免除判定より前に terminal 化する arm がある
  (when (= row.status "booting")
    (setv row (replace row :status "failed"))
    (return row))
  (when stale
    (setv row (replace row :status "exited"))
    (return row))
  row)
