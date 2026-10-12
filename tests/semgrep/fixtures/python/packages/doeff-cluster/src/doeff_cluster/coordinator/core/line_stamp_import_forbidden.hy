;;; 規則 doeff-cluster-coordinator-core-does-not-know-where-its-lines-go の陽性対照(core が行の送りを配線する — 断る)。
(import doeff_cluster.worker.entry.line_stamp [process-lines coordinator-log-plan])
;; 送りに触れない import は当たらない。
(import doeff_core_effects.effects [slog])
