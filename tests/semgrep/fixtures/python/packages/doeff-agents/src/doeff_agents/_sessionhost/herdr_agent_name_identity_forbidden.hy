;;; Semgrep fixture: doeff-agents-herdr-session-identity-not-agent-name.
;;;
;;; herdr session identity must anchor on the workspace label (survives real
;;; agent start), not on the herdr agent-name registry: real-agent detection
;;; overwrites the name plate within ~2s (probe 2026-08-01, n=3), so
;;; name-based resolution silently breaks every live session — issue #556 /
;;; substrate-herdr-session-identity-anchor-r2-607f0c. The line below is the
;;; banned shape and must keep firing the rule.

(defk herdr-agent-pane-id-io [socket-path session-name]
  (herdr-call socket-path "agent.get" {"target" session-name}))

;;; The ONE sanctioned shape (must NOT fire): the existence probe for
;;; externally named seats sits directly under the waiver comment
;;; ";; registry-existence-probe:" (ADR-DOE-AGENTS-004 law
;;; herdr-session-identity-is-workspace-label). Only has-session /
;;; session-pane-ids for names WITHOUT a label holder may consume it.

(defk herdr-external-agent-pane-id-io [socket-path name]
  (try
    ;; registry-existence-probe: allowed only for confirming the existence of externally named seats (identity, attribution, and kill go by label)
    (setv result (herdr-call socket-path "agent.get" {"target" name}))
    (except [e HerdrApiError]
      (when (= e.code "agent_not_found")
        (return None))
      (raise)))
  (herdr-registry-agent-pane-id result name))

;;; A waiver comment that is NOT adjacent (more than a few lines above, or on
;;; a different call) does not cover a later kill-path resolution — this
;;; line must keep firing.

(defk herdr-kill-session-io [socket-path session-name]
  (setv result (herdr-call socket-path "agent.get" {"target" session-name}))
  (herdr-call socket-path "pane.close" {"pane_id" (get (get result "agent") "pane_id")}))
;;;
;;; Warning: the vocabulary (deff / defk) is not the point of this fixture -- the rule's pattern is the agent.get
;;; regex and does not depend on the spelling of the definition. Real code still has functions left as deff, but
;;; this fixture is listed in the ledger of ADR-DOE-HY-004 (which also counts the literal text inside comments and strings), so
;;; it is written with defk. Line numbers are pinned by test_vm_failfast_semgrep_rules.py, so
;;; the note must always be placed at the end of the file (deff and defk are the same 4 characters, so lines do not shift).
