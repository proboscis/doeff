;;; Positive control for the rule doeff-cluster-coordinator-core-does-not-know-where-its-lines-go (core wires up line forwarding -- rejected).
(import doeff_cluster._worker.entry.line_stamp [process-lines coordinator-log-plan])
;; Imports that do not touch forwarding do not fire.
(import doeff_core_effects [slog])
