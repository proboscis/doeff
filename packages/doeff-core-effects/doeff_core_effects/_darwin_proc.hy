;;; Reading process state on macOS (Darwin) - read the bytes of the structs that libproc's proc_pidinfo (<libproc.h>, <sys/proc_info.h>) writes.
;;; On macOS, which has no /proc, this serves the reading of a warm child's end (proc-stat-of in os_warm_process.hy), the warm child's thread count (own-thread-count)
;;; and the machine's process table (ReadProcTable and SignalPid of os_proc_table.hy) (the Linux readings stay the /proc ones).
;;;
;;;   bsd-info-stat      Read state and start-ticks from struct proc_bsdinfo (PROC_PIDTBSDINFO, 136 bytes). Map status (SIDL 1, SRUN 2,
;;;                      SSLEEP 3, SSTOP 4, SZOMB 5) to the /proc state letters (I, R, S, T, Z), and turn the start time (seconds and microseconds) into a single
;;;                      integer (microseconds) for start-ticks.
;;;   task-info-threads  Read pti_threadnum from struct proc_taskinfo (PROC_PIDTASKINFO, 96 bytes).
;;;   bsd-rusage-row     Turn proc_bsdinfo and struct rusage_info_v4 (proc_pid_rusage RUSAGE_INFO_V4) into one ProcRow of the process table:
;;;                      ppid and comm (pbi_comm, at most 16 bytes) from proc_bsdinfo; cpu-ms = ri_user_time + ri_system_time + ri_child_user_time +
;;;                      ri_child_system_time (the process and its already-waited children -- the same sum as utime + stime + cutime + cstime on Linux)
;;;                      in mach absolute time units, turned into nanoseconds with the machine's mach_timebase_info (1/1 on Intel, 125/3 on Apple
;;;                      silicon) and then into milliseconds; rss-bytes = ri_phys_footprint. macOS has no PSS and no plain rss that counts compressed
;;;                      pages, so the table's rss column carries the footprint (the memory the kernel charges the process -- Activity Monitor's
;;;                      Memory column: resident anonymous + compressed + wired, without shared file pages); readers that sum it over a tree get the
;;;                      memory the tree holds, never a PSS. A zombie (SZOMB) is None (not put in the table, as on Linux).
;;;   darwin-proc-table  Read the whole table once: proc_listallpids for the pids, then proc_pidinfo and proc_pid_rusage per pid. A process that ended
;;;                      while reading, or one this user may not read (EPERM -- another user's process when not root), is not put in the table.
;;;   An answer of the wrong size (partially written, or a version with a different layout) is refused with ValueError - a number from a shifted position is never used as a value. Reading the bytes does not depend on the machine,
;;;   so it is also checked on Linux tests; darwin-proc-stat and darwin-thread-count, which call proc_pidinfo, are called only on macOS.
(require doeff-hy.macros [defk val lazy])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import ctypes)
(import errno)
(import os)
(import struct)
(import doeff_core_effects._process_stat [ProcStat])
(import doeff_core_effects._proc_table_effects [ProcRow])

;; The flavor numbers of proc_pidinfo, and the size and layout of the struct each answer is (<sys/proc_info.h>).
(val PROC-PIDTBSDINFO 3)
(val PROC-PIDTASKINFO 4)
(val BSD-INFO-SIZE 136)
(val TASK-INFO-SIZE 96)
;; Positions of the fields read in proc_bsdinfo: pbi_status (the 2nd uint32), pbi_pid and pbi_ppid (the 4th and 5th uint32), pbi_comm (char[16] after
;; uint32 x 12) and pbi_start_tvsec and pbi_start_tvusec (the last uint64 x 2).
(val BSD-STATUS-OFFSET 4)
(val BSD-PID-OFFSET 12)
(val BSD-PPID-OFFSET 16)
(val BSD-COMM-OFFSET 48)
(val BSD-COMM-SIZE 16)
(val BSD-START-OFFSET 120)
;; The flavor of proc_pid_rusage and the size of struct rusage_info_v4 (<sys/resource.h> -- uint8 ri_uuid[16] and then uint64 x 35, 296 bytes).
;; proc_pid_rusage takes no buffer size and writes the whole struct, so the buffer is larger than the struct (a later macOS that grows the struct
;; never writes past it).
(val RUSAGE-INFO-V4 4)
(val RUSAGE-V4-SIZE 296)
(val RUSAGE-BUFFER-SIZE 1024)
;; Positions of the fields read in rusage_info_v4: ri_user_time 16, ri_system_time 24, ri_child_user_time 96, ri_child_system_time 104 (mach absolute
;; time units) and ri_phys_footprint 72 (bytes).
(val RUSAGE-CPU-OFFSETS #(16 24 96 104))
(val RUSAGE-FOOTPRINT-OFFSET 72)
;; Room for pids that appear between counting them and listing them (proc_listallpids fills at most the buffer).
(val PIDS-SLACK 256)
;; Position of pti_threadnum in proc_taskinfo (the 10th int32 after uint64 x 6).
(val TASK-THREADS-OFFSET 84)
;; macOS status -> /proc state letter.
(val STATUS-STATES {1 "I" 2 "R" 3 "S" 4 "T" 5 "Z"})
(val LIBPROC-PATH "/usr/lib/libproc.dylib")
(val LIBSYSTEM-PATH "/usr/lib/libSystem.B.dylib")


;; libproc and libSystem (for mach_timebase_info), loaded once on first use (not at import -- Linux has neither). Do not create a ctypes.CDLL per
;; call: each creation makes one function type that free-threading CPython does not give back (the note on LIBC in pidfd_exit.hy), and the table
;; reads proc_pidinfo for every process every few seconds.
(lazy val LIBPROC (ctypes.CDLL LIBPROC-PATH :use-errno True))
(lazy val LIBSYSTEM (ctypes.CDLL LIBSYSTEM-PATH :use-errno True))


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
  (val buffer (ctypes.create-string-buffer size))
  (val written (.proc-pidinfo LIBPROC (ctypes.c-int pid) (ctypes.c-int flavor) (ctypes.c-uint64 0) buffer (ctypes.c-int size)))
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


(defk bsd-rusage-row [pid bsd rusage numer denom]
  {:pre [(: pid int) (> pid 0) (: bsd bytes) (: rusage bytes) (: numer int) (> numer 0) (: denom int) (> denom 0)] :post [(: % (| ProcRow None))]
   :tags {:context "process" :role "foundation"}}
  "Turn the bytes of proc_bsdinfo and rusage_info_v4 of the process pid into one row of the table (see the header comment -- rss-bytes is the
   footprint). numer / denom = the machine's mach_timebase_info (mach absolute time units -> nanoseconds). A zombie is None. A proc_bsdinfo of
   the wrong size or of another pid, and a rusage_info_v4 shorter than the struct, are refused with ValueError."
  (<- whole bytes (sized bsd BSD-INFO-SIZE "proc_bsdinfo"))
  (when (< (len rusage) RUSAGE-V4-SIZE)
    (raise (ValueError (.format "rusage_info_v4 is shorter than {} bytes: {} bytes" RUSAGE-V4-SIZE (len rusage)))))
  (val named (get (struct.unpack-from "=I" whole BSD-PID-OFFSET) 0))
  (when (!= named pid)
    (raise (ValueError (.format "proc_bsdinfo of pid {} names pid {}" pid named))))
  (<- seen ProcStat (bsd-info-stat whole))
  (when (= seen.state "Z")
    (return None))
  (val ppid (get (struct.unpack-from "=I" whole BSD-PPID-OFFSET) 0))
  (val comm (.decode (get (.split (cut whole BSD-COMM-OFFSET (+ BSD-COMM-OFFSET BSD-COMM-SIZE)) b"\x00" 1) 0) "utf-8" :errors "surrogateescape"))
  (val ticks (sum (gfor offset RUSAGE-CPU-OFFSETS (get (struct.unpack-from "=Q" rusage offset) 0))))
  (val footprint (get (struct.unpack-from "=Q" rusage RUSAGE-FOOTPRINT-OFFSET) 0))
  (ProcRow :pid pid :ppid ppid :start-ticks seen.start-ticks :rss-bytes footprint :comm comm
           :cpu-ms (// (* ticks numer) (* denom 1000000))))


(defk pid-rusage [pid]
  {:pre [(: pid int) (> pid 0)] :post [(: % (| bytes None))] :tags {:context "process" :role "foundation"}}
  "Call proc_pid_rusage(pid, RUSAGE_INFO_V4, buffer) and return the buffer's bytes (see RUSAGE-BUFFER-SIZE). A pid that does not exist (ESRCH) gives
   None; any other failure is an OSError (EPERM is a PermissionError)."
  (val buffer (ctypes.create-string-buffer RUSAGE-BUFFER-SIZE))
  (val status (.proc-pid-rusage LIBPROC (ctypes.c-int pid) (ctypes.c-int RUSAGE-INFO-V4) buffer))
  (val failure (if (!= status 0) (ctypes.get-errno) 0))
  (cond
    (= status 0) buffer.raw
    (= failure errno.ESRCH) None
    True (raise (OSError failure (.format "proc_pid_rusage({}) refused: {}" pid (os.strerror failure))))))


(defk darwin-timebase []
  {:pre [] :post [(: % tuple)] :tags {:context "process" :role "foundation"}}
  "Read the machine's mach_timebase_info (answer = #(numer denom) -- mach absolute time units x numer / denom = nanoseconds)."
  (val buffer (ctypes.create-string-buffer 8))
  (val status (.mach-timebase-info LIBSYSTEM buffer))
  (when (!= status 0)
    (raise (OSError (.format "mach_timebase_info refused: {}" status))))
  (val read (struct.unpack-from "=2I" buffer.raw 0))
  (val numer (get read 0))
  (val denom (get read 1))
  (when (or (= numer 0) (= denom 0))
    (raise (OSError (.format "mach_timebase_info answered {}/{}" numer denom))))
  #(numer denom))


(defk darwin-pids []
  {:pre [] :post [(: % tuple)] :tags {:context "process" :role "foundation"}}
  "Read the pids of the machine's processes once with proc_listallpids (ascending, pid 0 = the kernel left out)."
  (val counted (.proc-listallpids LIBPROC None (ctypes.c-int 0)))
  (when (< counted 0)
    (raise (OSError (ctypes.get-errno) "proc_listallpids refused to count")))
  (val buffer ((* ctypes.c-int (+ counted PIDS-SLACK))))
  (val listed (.proc-listallpids LIBPROC buffer (ctypes.c-int (ctypes.sizeof buffer))))
  (when (< listed 0)
    (raise (OSError (ctypes.get-errno) "proc_listallpids refused to list")))
  (tuple (sorted (gfor pid (cut buffer 0 (min listed (len buffer))) :if (> pid 0) pid))))


(defk darwin-proc-row [pid timebase]
  {:pre [(: pid int) (> pid 0) (: timebase tuple)] :post [(: % (| ProcRow None))] :tags {:context "process" :role "foundation"}}
  "Read one process of the table on macOS (header comment). A process that ended while reading, a zombie, or one this user may not read = None."
  (try
    (do (<- bsd (| bytes None) (pid-info pid PROC-PIDTBSDINFO BSD-INFO-SIZE))
        (when (is bsd None)
          (return None))
        (<- rusage (| bytes None) (pid-rusage pid))
        (when (is rusage None)
          (return None))
        (<- row (| ProcRow None) (bsd-rusage-row pid bsd rusage (get timebase 0) (get timebase 1)))
        row)
    (except [PermissionError]
      None)))


(defk darwin-proc-table []
  {:pre [] :post [(: % tuple)] :tags {:context "process" :role "foundation"}}
  "Read the machine's process table once on macOS (header comment). Answer = a tuple of ProcRow (ascending pid)."
  (<- timebase tuple (darwin-timebase))
  (<- pids tuple (darwin-pids))
  (var rows [])
  (for [pid pids]
    (<- row (| ProcRow None) (darwin-proc-row pid timebase))
    (when (is-not row None)
      (.append rows row)))
  (tuple rows))
