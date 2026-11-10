;;; Tests of the process table of the machine. The contract (the real os-proc-table-handler and the fake scripted-proc-table-handler pass the same deftest)
;;; and the reading of one line of stat. The assembly of the interpreters is in proc_table_contract_handlers.hy.
;;;
;;;   Contract  The table is in ascending pid order with no overlaps, and the row of the sleeping child has the pid of its parent. A SignalPid with a different start time is not sent and gives False (it does not send to
;;;             another process under a reused pid); a matching SignalPid gives True, and the sleeping child disappears from the table afterwards.
;;;   Reading   A stat whose comm contains spaces and parentheses can be read; cpu-ms is the own and waited-children ticks over the ticks per second; a zombie is not
;;;             put in the table; contents with too few fields are refused by name.
(require doeff-hy.macros [deftest defk <- val var])
(import doeff_core_effects [ProcessSignal])
(import doeff_core_effects [ProcRow ReadProcTable SignalPid]
        doeff_core_effects._proc_table_effects [proc-row-of-stat])
(import proc_table_contract_handlers [ProcPids ProcWorld])

;; The upper limit on how many times to re-read the table until the sleeping child disappears from it (a real SIGKILL takes a little while between arriving and finishing -- re-read instead of waiting on time).
(val GONE-READS 10000)


(defk row-of [table pid]
  {:pre [(: table tuple) (: pid int)] :post [(: % (| ProcRow None))] :tags {:context "proc-table-test" :role "judgment"}}
  "Looks up the row of a pid in the table (None if absent)."
  (next (gfor row table :if (= row.pid pid) row) None))


(defk read-until-gone [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "proc-table-test" :role "program"}}
  "Re-reads, up to the limit, to confirm that the sleeping child that was sent a signal has disappeared from the table."
  (var gone False)
  (for [_ (range GONE-READS)]
    (<- table tuple (ReadProcTable))
    (<- seen (| ProcRow None) (row-of table pid))
    (when (is seen None)
      (:= gone True)
      (break)))
  gone)


(deftest test-the-proc-table-names-the-sleeper-and-signals-only-the-same-start
  {:interpreters ["os-proc-table" "scripted-proc-table"]}
  (<- pids ProcPids (ProcWorld))
  (<- table tuple (ReadProcTable))
  (val numbers (lfor row table row.pid))
  (assert (= numbers (sorted (set numbers))) "the table is not in ascending pid order without overlaps")
  (<- sleeper (| ProcRow None) (row-of table pids.sleeper))
  (assert (is-not sleeper None) (.format "sleeping child {} is not in the table" pids.sleeper))
  (assert (= sleeper.ppid pids.parent) sleeper)
  (assert (= sleeper.comm "sleep") sleeper)
  ;; A different start time = another process under a reused pid -- do not send. The sleeping child stays in the table.
  (<- refused bool (SignalPid :pid pids.sleeper :start-ticks (+ sleeper.start-ticks 1) :signal ProcessSignal.KILL))
  (assert (not refused) "sent to a process with a different start time")
  (<- still tuple (ReadProcTable))
  (<- kept (| ProcRow None) (row-of still pids.sleeper))
  (assert (is-not kept None) "the sleeping child disappeared although nothing was sent")
  ;; A matching SignalPid is sent, and the sleeping child disappears from the table.
  (<- sent bool (SignalPid :pid pids.sleeper :start-ticks sleeper.start-ticks :signal ProcessSignal.KILL))
  (assert sent "did not send to the sleeping child with the same start time")
  (<- gone bool (read-until-gone pids.sleeper))
  (assert gone "the sleeping child that was sent to does not disappear from the table"))


(deftest test-a-stat-line-with-spaces-and-parens-in-comm-is-read
  (val text "4242 (a) b (c) S 7 4242 4242 0 -1 4194560 10 0 0 0 1 2 0 0 20 0 1 0 123456 1000000 300 18446744073709551615 0\n")
  (<- row (| ProcRow None) (proc-row-of-stat 4242 text 4096))
  (assert (= row (ProcRow :pid 4242 :ppid 7 :start-ticks 123456 :rss-bytes (* 300 4096) :comm "a) b (c" :cpu-ms 30)) row))


(deftest test-cpu-ms-counts-own-and-waited-children-ticks
  ;; utime 1 + stime 2 + cutime 30 + cstime 7 = 40 ticks: 400 ms at 100 ticks a second, 160 ms at 250.
  (val text "77 (job) S 1 77 77 0 -1 4194560 10 0 0 0 1 2 30 7 20 0 1 0 5000 1000000 300 18446744073709551615 0\n")
  (<- at-100 (| ProcRow None) (proc-row-of-stat 77 text 4096))
  (assert (= at-100.cpu-ms 400) at-100)
  (<- at-250 (| ProcRow None) (proc-row-of-stat 77 text 4096 250))
  (assert (= at-250.cpu-ms 160) at-250))


(deftest test-a-zombie-is-not-a-row-and-a-short-stat-is-refused
  (val zombie "9 (gone) Z 1 9 9 0 -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 77 0 0 18446744073709551615 0\n")
  (<- dead (| ProcRow None) (proc-row-of-stat 9 zombie 4096))
  (assert (is dead None) dead)
  ;; A process whose parent is in the middle of reaping it (state X -- visible only for the short time before reaping finishes and /proc/<pid> disappears) is also not put in the table as a finished process.
  (<- reaping (| ProcRow None) (proc-row-of-stat 9 (.replace zombie " Z " " X ") 4096))
  (assert (is reaping None) reaping)
  (var refused None)
  (try
    (<- (proc-row-of-stat 9 "9 (short) S 1 9\n" 4096))
    (except [error ValueError]
      (:= refused error)))
  (assert (is-not refused None) "did not refuse a stat with too few fields"))
