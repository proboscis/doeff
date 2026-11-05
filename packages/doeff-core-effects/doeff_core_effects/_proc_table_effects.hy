;;; Effects for the machine's process table -- read the parent-child relations and memory of running processes at once, and send a signal to one process.
;;; A foundation vocabulary with no business terms.
;;;
;;;   ReadProcTable  Read the machine's process table once. Answer = a sequence of ProcRow (ascending pid). Processes that ended while reading are not included.
;;;   SignalPid      Only when the process with pid is still as it was at start-ticks and has not ended, send a signal to that one process once, and return without waiting.
;;;                  Answer = whether it was sent (bool -- if absent, ended, or the pid was reused, it is not sent and the answer is False. Never sends to another user's process).
;;;                  Does not send to a group (sending to a group is SignalProcess and SignalWarmChild).
;;;
;;; Purpose: when the machine's memory is nearly exhausted, the watcher that counts how much each job's tree uses and stops the largest tree first
;;; (the memory_watch of doeff-cluster's worker) asks for the table without reading /proc directly. In mocks it is replaced by a scripted table.
;;;
;;; Responders: os-proc-table-handler (os_proc_table.hy -- real, Linux /proc) and scripted-proc-table-handler (scripted_proc_table.hy -- scripted table).
;;; The contract with the real one is tests/test_proc_table_contract.hy.
(require doeff-hy.macros [defeffect defk val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; named by the defrecord expansion
(import doeff_core_effects._process_effects [ProcessSignal])
(import doeff_core_effects._process_stat [ENDED-STATES])

;; Positions of the fields of /proc/<pid>/stat (the column number of the list obtained by splitting what follows the last ')' on whitespace -- the 3rd field, state, is 0). ppid = 4th, starttime = 22nd,
;; rss = 24th (a number of pages).
(val STAT-PPID 1)
(val STAT-START-TICKS 19)
(val STAT-RSS-PAGES 21)


(defrecord ProcRow
  "One process of the machine: pid, ppid = the parent's pid, start-ticks = start time (tells apart pid reuse -- on Linux the clock tick of starttime in /proc/<pid>/stat),
   rss-bytes = bytes of the current resident set, comm = command name (the 2nd field of /proc/<pid>/stat -- inside the parentheses, at most 15 characters)."
  {:check [(> pid 0) (>= ppid 0) (>= start-ticks 0) (>= rss-bytes 0)]}
  (#^ int pid)
  (#^ int ppid)
  (#^ int start-ticks)
  (#^ int rss-bytes)
  (#^ str comm))


(defk proc-row-of-stat [pid text page-bytes]
  {:pre [(: pid int) (> pid 0) (: text str) (: page-bytes int) (> page-bytes 0)] :post [(: % (| ProcRow None))]
   :tags {:context "process" :role "judgment"}}
  "To turn the content text of /proc/<pid>/stat into one row of the table (used by the real responder). comm (the 2nd field) may contain spaces or parentheses, so split on whitespace
   what follows the last ')'. A process that has ended and is about to be reaped, or is being reaped (state Z or X -- ENDED-STATES in process_stat.hy), is None (not put in the
   table). Content with too few fields is a reading that does not know the machine's shape, so name it with ValueError (do not silently discard)."
  (val parts (.rsplit text ")" 1))
  (val head (get parts 0))
  (val comm (cut head (+ (.find head "(") 1) None))
  (val fields (if (= (len parts) 2) (.split (get parts 1)) []))
  (when (<= (len fields) STAT-RSS-PAGES)
    (raise (ValueError (.format "too few fields in /proc/{}/stat: {!r}" pid text))))
  (if (in (get fields 0) ENDED-STATES)
      None
      (ProcRow :pid pid :ppid (int (get fields STAT-PPID)) :start-ticks (int (get fields STAT-START-TICKS))
               :rss-bytes (* (max 0 (int (get fields STAT-RSS-PAGES))) page-bytes) :comm comm)))


(defeffect ReadProcTable
  "Read the machine's process table once (see the header comment). Answer = a sequence of ProcRow (ascending pid)."
  {:fields []
   :answer (get tuple #(ProcRow ...))
   :tags {:context "process" :role "foundation"}})


(defeffect SignalPid
  "Send a signal once to one process with pid (one still as it was at start-ticks) (see the header comment). Answer = whether it was sent."
  {:fields [(: pid int) (: start-ticks int) (: signal ProcessSignal)]
   :answer bool
   :tags {:context "process" :role "foundation"}})
