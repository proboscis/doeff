;;; Opens an fd (pidfd) on Linux that becomes readable when a process exits. This is the Linux way to open the fd for waiting on a child process's exit (process-exit-handler in process_exit.hy).
;;; The macOS way is kqueue-fd-of in kqueue_exit.hy; both have the same shape: pid -> fd | None.
;;;
;;; A process's pidfd (pidfd_open, since Linux 5.3) becomes a readable fd when that process exits. A pidfd can also be opened on non-child processes,
;;; so a child detached from the waiting parent (not a child of the worker) can be waited on in the same way.
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import ctypes)
(import errno)
(import os)
(import platform)

;; Syscall number of pidfd_open (per machine type). This Python build (3.14 free-threading) has no os.pidfd_open,
;; so we call the libc syscall directly. x86_64 and aarch64 share the same number (Linux's common table). A machine type not in the table raises PidfdUnavailable.
(val PIDFD-OPEN-SYSCALLS {"x86_64" 434 "aarch64" 434})

;; libc (loaded only once at import time -- do not create a ctypes.CDLL on every call). Each CDLL creation makes one function type (a _FuncPtr class),
;; and on free-threading CPython 3.14, if these keep being created on an await bridge thread while the main thread's VM is running, memory is not
;; returned even after release (about 1.8 KB each time). The wait between worker rounds calls pidfd-of per tick and per child, so a worker process with a single child
;; grew by more than 0.5 GB per day. The function object (.syscall) is cached on the CDLL after the first lookup. errno is mapped by ctypes
;; per thread, so it is safe to share across threads.
(val #^ ctypes.CDLL LIBC (ctypes.CDLL None :use-errno True))


(defclass PidfdUnavailable [RuntimeError]
  "This machine cannot open a pidfd (a machine type whose pidfd_open number is unknown, or a kernel older than Linux 5.3).")


(defk pidfd-of [pid]
  {:pre [(: pid int)] :post [(: % (| int None))] :tags {:context "process" :role "foundation"}}
  "Open the pidfd of the process pid (this is the only place that calls the syscall directly -- see the header note). Answer = fd (the caller closes it); a pid that does not exist gives None.
   How the fd is obtained: libc syscall(SYS_pidfd_open, pid, 0). flags 0 = an fd that becomes readable on exit (since Linux 5.3). A kernel that does not know the syscall (older than 5.3)
   gives ENOSYS, which raises PidfdUnavailable."
  (val number (.get PIDFD-OPEN-SYSCALLS (platform.machine)))
  (when (is number None)
    (raise (PidfdUnavailable (.format "Machine type whose pidfd_open number is unknown: {}" (platform.machine)))))
  (val fd (.syscall LIBC number (ctypes.c-int pid) (ctypes.c-uint 0)))
  (val failure (if (< fd 0) (ctypes.get-errno) 0))
  (cond
    (>= fd 0) fd
    (= failure errno.ESRCH) None
    (= failure errno.ENOSYS) (raise (PidfdUnavailable "This kernel has no pidfd_open (requires Linux 5.3 or later)"))
    True (raise (OSError failure (os.strerror failure)))))
