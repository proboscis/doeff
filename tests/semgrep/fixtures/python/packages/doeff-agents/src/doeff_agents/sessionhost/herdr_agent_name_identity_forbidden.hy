;;; Semgrep fixture: doeff-agents-herdr-session-identity-not-agent-name.
;;;
;;; herdr session identity must anchor on the workspace label (survives real
;;; agent start), not on the herdr agent-name registry: real-agent detection
;;; overwrites the name plate within ~2s (probe 2026-08-01, n=3), so
;;; name-based resolution silently breaks every live session — issue #556 /
;;; substrate-herdr-session-identity-anchor-r2-607f0c. The line below is the
;;; banned shape and must keep firing the rule.

(deff herdr-agent-pane-id-io [socket-path session-name]
  (herdr-call socket-path "agent.get" {"target" session-name}))

;;; The ONE sanctioned shape (must NOT fire): the existence probe for
;;; externally named seats sits directly under the waiver comment
;;; ";; registry-existence-probe:" (ADR-DOE-AGENTS-004 law
;;; herdr-session-identity-is-workspace-label). Only has-session /
;;; session-pane-ids for names WITHOUT a label holder may consume it.

(deff herdr-external-agent-pane-id-io [socket-path name]
  (try
    ;; registry-existence-probe: 外部命名席の実在確認だけに許す(同一性・帰属・kill は label)
    (setv result (herdr-call socket-path "agent.get" {"target" name}))
    (except [e HerdrApiError]
      (when (= e.code "agent_not_found")
        (return None))
      (raise)))
  (herdr-registry-agent-pane-id result name))

;;; A waiver comment that is NOT adjacent (more than a few lines above, or on
;;; a different call) does not cover a later kill-path resolution — this
;;; line must keep firing.

(deff herdr-kill-session-io [socket-path session-name]
  (setv result (herdr-call socket-path "agent.get" {"target" session-name}))
  (herdr-call socket-path "pane.close" {"pane_id" (get (get result "agent") "pane_id")}))
