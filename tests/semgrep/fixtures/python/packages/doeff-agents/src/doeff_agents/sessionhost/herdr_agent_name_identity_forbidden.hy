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
