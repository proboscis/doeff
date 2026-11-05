;;; The choice of the per-machine-type mechanism for waiting for a child process to end and for reading a process's state (so that a worker running directly on a macOS machine
;;; can start children and wait for them).
;;;
;;;   - How to open an fd that becomes readable at the end: Linux = pidfd-of (pidfd_open), macOS (Darwin) = kqueue-fd-of (kqueue's EVFILT_PROC / NOTE_EXIT).
;;;     An unknown machine type is named with ExitWaitUnavailable -- it does not silently fall back to periodic re-asking.
;;;   - The state of a macOS process is read from the answer of libproc's proc_pidinfo (struct proc_bsdinfo, struct proc_taskinfo). The reading of the byte layout
;;;     is a function independent of the machine type, so on Linux too it is checked with bytes built in the layout of <sys/proc_info.h> (the check that calls the real proc_pidinfo
;;;     runs only on macOS -- the behavior check of test_process_exit_wait.hy measures that machine's opening and reading with a real process).
(require doeff-hy.macros [deftest defk <- val])
(import ctypes)
(import os)
(import platform)
(import struct)
(import sys)
(import unittest.mock [Mock])
(import pytest)
(import doeff [run])
(import doeff_core_effects [ExitWaitUnavailable]
        doeff_core_effects._process_exit [exit-fd-opener-for])
(import doeff_core_effects._pidfd_exit [pidfd-of])
(import doeff_core_effects._kqueue_exit [kqueue-fd-of])
(import doeff_core_effects._darwin_proc [bsd-info-stat task-info-threads BSD-INFO-SIZE TASK-INFO-SIZE])
(import doeff_core_effects._os_warm_process [same-child-running])
(import doeff_core_effects._os_warm_process :as os-warm-process-module)
(import doeff_core_effects._process_stat [ProcStat])

;; The layout of struct proc_bsdinfo in <sys/proc_info.h> (136 bytes): uint32 x 5 (flags status xstatus pid ppid), uid / gid x 6, rfu_1,
;; comm[16], name[32], uint32 x 5 (nfiles pgid pjobc e_tdev e_tpgid), int32 nice, uint64 start_tvsec, uint64 start_tvusec.
(val BSD-INFO-LAYOUT "=5I6II16s32s5Ii2Q")
;; The layout of struct proc_taskinfo (96 bytes): uint64 x 6, int32 x 12 (the 10th = pti_threadnum).
(val TASK-INFO-LAYOUT "=6Q12i")


(defn #^ bytes bsd-info [#^ int status #^ int seconds #^ int micros]
  "To build the bytes of a proc_bsdinfo that has only status and the start time (other fields are 0, the name is sleep)."
  (struct.pack BSD-INFO-LAYOUT 0 status 0 4242 1 501 20 501 20 501 20 0 b"sleep" b"sleep" 0 4242 0 0 0 0 seconds micros))


(defk stat-line [pid state start-ticks]
  {:pre [(: pid int) (: state str) (: start-ticks int)] :post [(: % str)]}
  "To build one line of /proc/<pid>/stat (it decides only state and starttime [the 22nd field], other fields are 0 or pid)."
  (.format "{0} (warm job) {1} 1 {0} {0} 0 -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 {2} 0 0\n" pid state start-ticks))


(deftest test-the-exit-fd-opener-follows-the-machine
  ;; Linux is pidfd, macOS is kqueue. Both ways of opening have the same shape of returning None for a missing pid (pid -> fd | None).
  (assert (is (run (exit-fd-opener-for "Linux")) pidfd-of))
  (assert (is (run (exit-fd-opener-for "Darwin")) kqueue-fd-of)))


(deftest test-the-pidfd-opener-does-not-load-libc-per-call [monkeypatch]
  ;; Failure case: pidfd-of does not make a ctypes.CDLL at each call. A CDLL makes one function type (the _FuncPtr class) each time it is made, and under free-threading
  ;; CPython 3.14, if it keeps being made in the await bridge's thread while the main thread's VM runs, memory does not come back even after release. The wait
  ;; between worker rounds calls pidfd-of per tick and per child, so a worker process with one child grew by more than 0.5 GB per day.
  (when (not (and (= sys.platform "linux") (in (platform.machine) #("x86_64" "aarch64"))))
    (pytest.skip "pidfd_open is only on Linux x86_64 and aarch64"))
  (val loads (Mock :wraps ctypes.CDLL))
  (.setattr monkeypatch ctypes "CDLL" loads)
  (for [_ (range 3)]
    (os.close (run (pidfd-of (os.getpid)))))
  (assert (= loads.call-count 0) loads.call-args-list))


(deftest test-a-machine-without-an-exit-wait-is-named
  ;; Failure case: a machine type with no known way of opening gives an ExitWaitUnavailable that carries the machine type's name -- it does not silently fall back to periodic re-asking.
  (with [caught (pytest.raises ExitWaitUnavailable)]
    (run (exit-fd-opener-for "Windows")))
  (assert (in "Windows" (str caught.value)) caught.value))


(deftest test-a-process-being-reaped-is-not-running [tmp-path monkeypatch]
  ;; Failure case: a process the parent is in the middle of reaping (/proc state X -- Linux's EXIT_DEAD. Visible only for the short time before reaping is done and /proc/<pid> disappears)
  ;; is not running, just like a zombie (Z). Before, only Z was read as ended, and a PollWarmChild right after waiting for the end of a child split from the warm child
  ;; answered WarmRunning when it overlapped with the warm child's reaping (the daily packages stage's test_task_venv_on_both_paths).
  ;; Swap the /proc root for the check's own place and measure with the Linux reading.
  (when (!= (platform.system) "Linux")
    (pytest.skip "reading the /proc state is the Linux reading"))
  (.setattr monkeypatch os-warm-process-module "PROC_ROOT" (str tmp-path))
  (for [[pid state running] [[41 "S" True] [42 "Z" False] [43 "X" False]]]
    (<- line str (stat-line pid state 77))
    (.mkdir (/ tmp-path (str pid)))
    (.write-text (/ tmp-path (str pid) "stat") line)
    (<- seen bool (same-child-running pid 77))
    (assert (= seen running) state)))


(deftest test-the-layouts-are-the-sizes-libproc-answers
  ;; The layout size is the same as the number of bytes proc_pidinfo writes (if different, the reading position shifts).
  (assert (= (struct.calcsize BSD-INFO-LAYOUT) BSD-INFO-SIZE 136))
  (assert (= (struct.calcsize TASK-INFO-LAYOUT) TASK-INFO-SIZE 96)))


(deftest test-the-bsd-info-reads-the-state-and-the-start
  ;; Turn the start time (seconds and microseconds) into one integer (microseconds) as start-ticks, and map status to the one character of /proc's state (5 = SZOMB = Z).
  (assert (= (run (bsd-info-stat (bsd-info 3 1760000000 123456))) (ProcStat :state "S" :start-ticks 1760000000123456)))
  (assert (= (run (bsd-info-stat (bsd-info 2 1 0))) (ProcStat :state "R" :start-ticks 1000000)))
  (assert (= (. (run (bsd-info-stat (bsd-info 5 7 8))) state) "Z")))


(deftest test-a-bsd-info-of-the-wrong-size-is-refused
  ;; Failure case: an answer of a different size (proc_pidinfo wrote only partway, a version with a different layout) is refused without reading -- a number at a shifted position is not made start-ticks.
  (with [caught (pytest.raises ValueError)]
    (run (bsd-info-stat (cut (bsd-info 3 1 2) 0 120))))
  (assert (in "136" (str caught.value)) caught.value))


(deftest test-an-unknown-bsd-status-is-refused
  ;; Failure case: an unknown status (outside SIDL 1 to SZOMB 5) is refused without being silently read as a running process.
  (with [caught (pytest.raises ValueError)]
    (run (bsd-info-stat (bsd-info 9 1 2))))
  (assert (in "9" (str caught.value)) caught.value))


(deftest test-the-task-info-reads-the-thread-count
  ;; Read pti_threadnum (the 10th int32) as the number of threads.
  (val raw (struct.pack TASK-INFO-LAYOUT 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 3 1 31))
  (assert (= (run (task-info-threads raw)) 3)))
