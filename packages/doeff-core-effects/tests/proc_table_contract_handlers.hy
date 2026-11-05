;;; The interpreters (composition root) of the contract test of the process table of the machine -- runs the Program of the same contract with only the process table handler swapped.
;;;
;;;   os-proc-table        Real: os-proc-table-handler (/proc and os.kill) + a real sleeping child started inside the test (sleep 30 -- a child of the test process)
;;;   scripted-proc-table  Fake: scripted-proc-table-handler (a scripted table -- two rows, the parent and the sleeping child)
;;;
;;; The world of the contract is prepared in the same shape for each interpreter (the answer of ProcWorld): the pid of the sleeping child and the pid of its parent.
;;; The user is doeff_interpreter of conftest.py (the names of :interpreters of deftest -> INTERPRETERS).
(require doeff-hy.macros [defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import os)
(import subprocess)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [ProcRow])
(import doeff_core_effects [os-proc-table-handler])
(import doeff_core_effects [scripted-proc-table-handler])

(val OS-PROC-TABLE "os-proc-table")
(val SCRIPTED-PROC-TABLE "scripted-proc-table")
;; The pids and start times of the scripted world (of a size that does not mix with real values).
(val SCRIPTED-PARENT 70000)
(val SCRIPTED-SLEEPER 70001)
(val SCRIPTED-TICKS 900000)


(defrecord ProcPids
  "A process of the contract world (sleeper = the pid of the sleeping child, parent = the pid of its parent)."
  (#^ int sleeper)
  (#^ int parent))


(defclass [(dataclass :frozen True)] ProcWorld [EffectBase]
  "An effect that asks for the process of the contract world (ProcPids).")


(defhandler proc-world [#^ ProcPids pids]
  ;; Why it stays as an argument: the pid is a value decided on the assembly side per interpreter (for the real one, a child started on every run).
  (ProcWorld []
    (resume pids)))


(defk under-os-proc-table [program]
  {:pre [(: program Program)] :post [(: % "the answer of the contract Program (the type differs per Program)")] :tags {:context "proc-table-test" :role "foundation"}}
  "Runs the program under the real os-proc-table-handler. Starts a sleeping child (sleep 30) as a child of this test's process, and stops and reaps it after the run."
  (val sleeper (subprocess.Popen ["sleep" "30"]))
  (var answer None)
  (try
    (<- ran (with_handlers [(proc-world (ProcPids :sleeper sleeper.pid :parent (os.getpid))) os-proc-table-handler] program))
    (:= answer ran)
    (finally
      (.kill sleeper)
      (.wait sleeper)))
  answer)


(defk under-scripted-proc-table [program]
  {:pre [(: program Program)] :post [(: % "the answer of the contract Program (the type differs per Program)")] :tags {:context "proc-table-test" :role "foundation"}}
  "Runs the program under the fake scripted-proc-table-handler. Table = two rows, the parent and its child the sleeping child. From the outside in: state, world, table."
  (val rows #((ProcRow :pid SCRIPTED-PARENT :ppid 1 :start-ticks SCRIPTED-TICKS :rss-bytes 8192 :comm "parent")
              (ProcRow :pid SCRIPTED-SLEEPER :ppid SCRIPTED-PARENT :start-ticks (+ SCRIPTED-TICKS 1) :rss-bytes 4096 :comm "sleep")))
  (<- answer (with_handlers [(state) (proc-world (ProcPids :sleeper SCRIPTED-SLEEPER :parent SCRIPTED-PARENT))
                             (scripted-proc-table-handler rows)]
               program))
  answer)


(val INTERPRETERS {OS-PROC-TABLE under-os-proc-table
                   SCRIPTED-PROC-TABLE under-scripted-proc-table})
