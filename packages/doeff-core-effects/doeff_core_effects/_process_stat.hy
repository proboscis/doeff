;;; Type ProcStat for one process snapshot (state and start tick) -- a home so the per-platform readers (Linux = the /proc read in os_warm_process.hy, macOS =
;;; the libproc read in darwin_proc.hy) answer in the same shape (the two readers return the same type without importing each other).
;;;   state        = one character from the 3rd field of /proc/<pid>/stat (R, S, D, T, Z ...). The macOS status is mapped to the same characters (darwin_proc.hy).
;;;   start-ticks  = the start tick that tells apart a reused pid. The value is per platform (Linux = starttime in clock ticks, macOS = start time in microseconds),
;;;                  and is compared only against the same reader on the same platform (the requester and the waiting child use the same reader proc-stat-of).
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "process" :role "type"})
(import dataclasses [dataclass])

;; States of a finished process (all of them no longer run -- neither something to wait for the end of nor something to send a signal to): Z = finished and waiting for the parent to reap it,
;; X = being reaped by the parent (Linux EXIT_DEAD -- visible only for the short time after reaping is done and before /proc/<pid> disappears. Looking only at Z, a read
;; in this interval would say "running"). The macOS status maps only up to Z (darwin_proc.hy).
(val ENDED-STATES (frozenset ["Z" "X"]))


(defrecord ProcStat
  "State (one character) and start-ticks (start tick -- see the header comment) of the process pid."
  (#^ str state)
  (#^ int start-ticks))
