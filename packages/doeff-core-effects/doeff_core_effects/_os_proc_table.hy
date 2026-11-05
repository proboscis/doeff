;;; Real answerer os-proc-table-handler for the machine's process table effects (proc_table_effects.hy).
;;;
;;;   ReadProcTable  Reads stat once for each numeric dir directly under /proc and answers with a list of ProcRow (ascending pid). Processes and
;;;                  zombies that ended while reading are excluded. rss is the page count x this machine's page bytes (os.sysconf SC_PAGE_SIZE).
;;;   SignalPid      Re-reads stat and sends via os.kill to a single pid (never to a group) only when the start-ticks are the same and it is not a zombie.
;;;   On a machine without /proc it raises ProcTableUnavailable -- it does not silently return an empty table.
;;; Only this module touches /proc and os.kill (the script module scripted_proc_table.hy does not). The contract with the real thing is tests/test_proc_table_contract.hy.
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import os)
(import itertools [accumulate repeat takewhile])
(import doeff_core_effects._process_effects [ProcessSignal])
(import doeff_core_effects._proc_table_effects [ProcRow ReadProcTable SignalPid proc-row-of-stat])
(import doeff_core_effects._warm_effects [signal-number-of])

;; Root of the process table store (Linux procfs).
(val PROC-ROOT "/proc")


(defclass ProcTableUnavailable [RuntimeError]
  "This machine has no /proc, so the process table cannot be read and the signal target cannot be verified.")


(defk stat-row [pid page-bytes]
  {:pre [(: pid int) (> pid 0) (: page-bytes int)] :post [(: % (| ProcRow None))] :tags {:context "process" :role "foundation"}}
  "To turn a pid's process into one row of the table. A process that ended while reading, or a zombie = None (not put in the table)."
  (val text (try (with [f (open (os.path.join PROC-ROOT (str pid) "stat") :encoding "utf-8" :errors "surrogateescape")] (.read f))
                 (except [[FileNotFoundError ProcessLookupError]] None)))
  (if (is text None)
      None
      (do (<- row (| ProcRow None) (proc-row-of-stat pid text page-bytes))
          row)))


(defk proc-root-present []
  {:pre [] :post [(: % None)] :tags {:context "process" :role "foundation"}}
  "To refuse by name a machine without /proc (never silently an empty table or a no-send answer)."
  (when (not (os.path.isdir PROC-ROOT))
    (raise (ProcTableUnavailable (.format "On a machine without {}, the process table cannot be read and the signal target cannot be verified" PROC-ROOT))))
  None)


(defk os-read-proc-table []
  {:pre [] :post [(: % tuple)] :tags {:context "process" :role "foundation"}}
  "To read the machine's process table once (header comment)."
  (<- (proc-root-present))
  (val page-bytes (os.sysconf "SC_PAGE_SIZE"))
  (val pids (sorted (gfor name (os.listdir PROC-ROOT) :if (.isdigit name) (int name))))
  ;; Read rows are chained into a #(row previous-pair) pair, and converted to a tuple just once at the end (copying the list so far for every row is quadratic in the row count -- on a machine with over
  ;; 80,000 processes, reading blocked the main scheduler).
  (var chained #())
  (for [pid pids]
    (<- row (| ProcRow None) (stat-row pid page-bytes))
    (when (is-not row None)
      (:= chained #(row chained))))
  (val cells (takewhile bool (accumulate (repeat None) (fn [cell _] (get cell 1)) :initial chained)))
  (tuple (reversed (tuple (gfor cell cells (get cell 0))))))


(defk os-signal-pid [pid start-ticks sent]
  {:pre [(: pid int) (: start-ticks int) (: sent ProcessSignal)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "To send a signal to a single pid's process (header comment). Sends only when it is running with the same start-ticks, and never to someone else's process."
  (<- (proc-root-present))
  (var row None)
  (when (> pid 0)
    (<- seen (| ProcRow None) (stat-row pid 1))
    (:= row seen))
  (<- number int (signal-number-of sent))
  (if (or (is row None) (!= row.start-ticks start-ticks))
      False
      (try
        (do (os.kill pid number) True)
        (except [ProcessLookupError] False))))


(defhandler os-proc-table-handler
  "Answers the process-list effects (ReadProcTable, SignalPid) with the real /proc and os.kill."
  ;; Real /proc and os.kill (header comment).
  (ReadProcTable []
    (<- rows tuple (os-read-proc-table))
    (resume rows))
  (SignalPid [pid start-ticks signal]
    (<- sent bool (os-signal-pid pid start-ticks signal))
    (resume sent)))
