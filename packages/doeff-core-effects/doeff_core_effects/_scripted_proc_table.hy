;;; scripted-proc-table-handler, the I/O-free responder for the machine's process table effects (proc_table_effects.hy).
;;; It answers with a scripted table, without using the real /proc and signals. It knows no business: the table content is passed by the caller (tests, mocks).
;;;
;;;   ReadProcTable  Answers the current table (ascending pid).
;;;   SignalPid      Only when pid and start-ticks match a row of the table, remove that row from the table and return True (both TERM and KILL -- scripted processes end immediately).
;;;                  When they do not match or the process is absent, False (never sends to another user's process -- same as the real one). Children of the removed row stay in the table (same as children whose parent died
;;;                  remain with the real one).
;;; The table is a session value (only within this run -- a state handler is needed outside). The contract with the real one is tests/test_proc_table_contract.hy.
(require doeff-hy.macros [defhandler <- val var])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import doeff_core_effects._proc_table_effects [ProcRow ReadProcTable SignalPid])


(defhandler scripted-proc-table-handler [#^ (get tuple #(ProcRow ...)) rows]
  "Answer ReadProcTable and SignalPid with the scripted table rows (a sequence of ProcRow) (see the header comment)."
  ;; Why it stays an argument: the table is a scripted value decided per test or mock, not a setting read with Ask.
  (session var table (tuple (sorted rows :key (fn [row] row.pid))))
  (ReadProcTable []
    (resume table))
  (SignalPid [pid start-ticks signal]
    (val hit (any (gfor row table (and (= row.pid pid) (= row.start-ticks start-ticks)))))
    (when hit
      (:= table (tuple (gfor row table :if (!= row.pid pid) row))))
    (resume hit)))
