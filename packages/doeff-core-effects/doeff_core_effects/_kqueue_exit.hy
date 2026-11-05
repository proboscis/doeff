;;; Open an fd that becomes readable when a process exits on macOS (Darwin) -- the macOS way of waiting for a child process to exit
;;; (process-exit-handler in process_exit.hy); same shape as pidfd-of in Linux's pidfd_exit.hy: pid -> fd | None, the caller closes it.
;;;
;;; How it opens: create one kqueue and register EVFILT_PROC / NOTE_EXIT for the pid. When the process exits, one event accumulates in the kqueue and the kqueue's
;;; fd becomes a readable fd -- like a pidfd, it can be hooked to asyncio's loop with add_reader. EVFILT_PROC can also be registered for non-child processes
;;; (processes of the same user), so a child split off from the waiting child can be awaited the same way. Registering an exited (including zombie) or nonexistent pid gives ESRCH
;;; (ProcessLookupError), and we answer None (there is nothing to wait for).
;;; fd ownership: a select.kqueue object closes its own fd on GC, so we duplicate the registered kqueue's fd with os.dup, close the object, and
;;; return only the duplicated fd (the kqueue and its registration remain as long as at least one fd refers to it). The caller closes it with os.close, as with Linux's pidfd.
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import os)
(import select)


(defk kqueue-fd-of [pid]
  {:pre [(: pid int)] :post [(: % (| int None))] :tags {:context "process" :role "foundation"}}
  "To open an fd (a kqueue with EVFILT_PROC / NOTE_EXIT registered) that becomes readable when the process of pid exits (see the header comment). None for a nonexistent pid."
  (val queue (select.kqueue))
  (try
    (.control queue [(select.kevent pid :filter select.KQ-FILTER-PROC :flags (| select.KQ-EV-ADD select.KQ-EV-ONESHOT)
                                    :fflags select.KQ-NOTE-EXIT)]
              0 0)
    (os.dup (.fileno queue))
    (except [ProcessLookupError] None)
    (finally (.close queue))))
