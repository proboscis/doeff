;;; Reading process state on macOS (Darwin) - read the bytes of the structs that libproc's proc_pidinfo (<libproc.h>, <sys/proc_info.h>) writes.
;;; On macOS, which has no /proc, this serves the reading of a warm child's end (proc-stat-of in os_warm_process.hy) and the warm child's thread count (own-thread-count)
;;; (the Linux reading stays as the /proc one in os_warm_process.hy).
;;;
;;;   bsd-info-stat      Read state and start-ticks from struct proc_bsdinfo (PROC_PIDTBSDINFO, 136 bytes). Map status (SIDL 1, SRUN 2,
;;;                      SSLEEP 3, SSTOP 4, SZOMB 5) to the /proc state letters (I, R, S, T, Z), and turn the start time (seconds and microseconds) into a single
;;;                      integer (microseconds) for start-ticks.
;;;   task-info-threads  Read pti_threadnum from struct proc_taskinfo (PROC_PIDTASKINFO, 96 bytes).
;;;   An answer of the wrong size (partially written, or a version with a different layout) is refused with ValueError - a number from a shifted position is never used as a value. Reading the bytes does not depend on the machine,
;;;   so it is also checked on Linux tests; darwin-proc-stat and darwin-thread-count, which call proc_pidinfo, are called only on macOS.
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import ctypes)
(import errno)
(import os)
(import struct)
(import doeff_core_effects._process_stat [ProcStat])

;; The flavor numbers of proc_pidinfo, and the size and layout of the struct each answer is (<sys/proc_info.h>).
(val PROC-PIDTBSDINFO 3)
(val PROC-PIDTASKINFO 4)
(val BSD-INFO-SIZE 136)
(val TASK-INFO-SIZE 96)
;; Positions of the fields read in proc_bsdinfo: pbi_status (the 2nd uint32) and pbi_start_tvsec and pbi_start_tvusec (the last uint64 x 2).
(val BSD-STATUS-OFFSET 4)
(val BSD-START-OFFSET 120)
;; Position of pti_threadnum in proc_taskinfo (the 10th int32 after uint64 x 6).
(val TASK-THREADS-OFFSET 84)
;; macOS status -> /proc state letter.
(val STATUS-STATES {1 "I" 2 "R" 3 "S" 4 "T" 5 "Z"})
(val LIBPROC-PATH "/usr/lib/libproc.dylib")


(defk sized [raw size name]
  {:pre [(: raw bytes) (: size int) (: name str)] :post [(: % bytes)] :tags {:context "process" :role "foundation"}}
  "Check that the answer of proc_pidinfo is exactly the size of the struct. If not, a ValueError carrying the size and the struct name."
  (when (!= (len raw) size)
    (raise (ValueError (.format "{} is not {} bytes: {} bytes" name size (len raw)))))
  raw)


(defk bsd-info-stat [raw]
  {:pre [(: raw bytes)] :post [(: % ProcStat)] :tags {:context "process" :role "foundation"}}
  "Read state and start-ticks from the bytes of struct proc_bsdinfo (see the header comment)."
  (<- whole bytes (sized raw BSD-INFO-SIZE "proc_bsdinfo"))
  (val status (get (struct.unpack-from "=I" whole BSD-STATUS-OFFSET) 0))
  (val start (struct.unpack-from "=2Q" whole BSD-START-OFFSET))
  (when (not-in status STATUS-STATES)
    (raise (ValueError (.format "Unknown proc_bsdinfo status {} (outside SIDL 1 to SZOMB 5)" status))))
  (ProcStat :state (get STATUS-STATES status) :start-ticks (+ (* (get start 0) 1000000) (get start 1))))


(defk task-info-threads [raw]
  {:pre [(: raw bytes)] :post [(: % int)] :tags {:context "process" :role "foundation"}}
  "Read the thread count from the bytes of struct proc_taskinfo (see the header comment)."
  (<- whole bytes (sized raw TASK-INFO-SIZE "proc_taskinfo"))
  (get (struct.unpack-from "=i" whole TASK-THREADS-OFFSET) 0))


(defk pid-info [pid flavor size]
  {:pre [(: pid int) (: flavor int) (: size int)] :post [(: % (| bytes None))] :tags {:context "process" :role "foundation"}}
  "Call proc_pidinfo(pid, flavor, 0, buffer, size) and return the bytes written. A pid that does not exist (ESRCH) gives None; any other failure is an OSError.
   libproc is called directly in this one place."
  (val libproc (ctypes.CDLL LIBPROC-PATH :use-errno True))
  (val buffer (ctypes.create-string-buffer size))
  (val written (.proc-pidinfo libproc (ctypes.c-int pid) (ctypes.c-int flavor) (ctypes.c-uint64 0) buffer (ctypes.c-int size)))
  (val failure (if (<= written 0) (ctypes.get-errno) 0))
  (cond
    (> written 0) (bytes (cut buffer.raw 0 written))
    (= failure errno.ESRCH) None
    True (raise (OSError failure (.format "proc_pidinfo({}, {}) refused: {}" pid flavor (os.strerror failure))))))


(defk darwin-proc-stat [pid]
  {:pre [(: pid int)] :post [(: % (| ProcStat None))] :tags {:context "process" :role "foundation"}}
  "Read the state and start-ticks of the process with the given pid on macOS. A nonexistent process gives None."
  (when (<= pid 0)
    (return None))
  (<- raw (| bytes None) (pid-info pid PROC-PIDTBSDINFO BSD-INFO-SIZE))
  (if (is raw None)
      None
      (do (<- seen ProcStat (bsd-info-stat raw))
          seen)))


(defk darwin-thread-count []
  {:pre [] :post [(: % int)] :tags {:context "process" :role "foundation"}}
  "Read the number of OS threads of this process on macOS."
  (<- raw (| bytes None) (pid-info (os.getpid) PROC-PIDTASKINFO TASK-INFO-SIZE))
  (when (is raw None)
    (raise (ProcessLookupError "Cannot read proc_taskinfo of our own process")))
  (<- threads int (task-info-threads raw))
  threads)
