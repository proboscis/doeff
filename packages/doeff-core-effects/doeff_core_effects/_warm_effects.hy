;;; Effects to ask a child that has finished loading in advance and is waiting (a warm child) to do a job, and to read its end.
;;; Foundation vocabulary with no business terms, next to the child process effects (process_effects.hy). The answerer is swapped per mechanism:
;;;   os-warm-process-handler        real -- sends one JSON line to the warm child's unix socket, and reads the end from /proc and the exit file (os_warm_process.hy)
;;;   scripted-warm-process-handler  no I/O -- answers from the socket's presence and a per-job script (scripted_warm_process.hy)
;;;
;;; The warm child (the caller's side -- outside this package) accepts requests on a unix socket in its own work dir, and a child A it forks (setsid to become group leader,
;;; dup2 to the log, sets up env and cwd) runs the entry point in a child B that A forks in turn. Before ending, A writes the exit code to the exit file by replacement
;;; (a negative value when B ended by a signal). A is not a child of the requester, so its end cannot be read by PollProcess -- PollWarmChild below reads it from the /proc
;;; start time (start-ticks) and the exit file. Pids get reused, so a child is named by the pair of pid and start-ticks.
;;;
;;;   ForkFromWarm     Ask the warm child to do one job. Answer = WarmForked (A's pid and start-ticks) or WarmRefused (no socket, connection refused,
;;;                    the warm child answered with a refusal, the answer deadline WARM-ANSWER-SECONDS expired, or the answer is unreadable -- the reason text is built just once in the functions below).
;;;                    env is the child's whole set of environment variables (replacement). Values appear in no answer, refusal text, or log.
;;;                    Three fields for a resident child (when all are default, the request shape is the same as before):
;;;                      hold-input   = the requester makes an input pipe only for child A, attaches its read end to the request (passing the fd over the unix socket) so that it becomes A's standard input,
;;;                                     and holds the write end. When the requester disappears, A reads EOF (the same function as hold-stdin of the container shim) --
;;;                                     the requester writes lines to A with WriteWarmChildInput.
;;;                      cgroup-procs = the cgroup.procs of the cgroup A puts itself into before starting the entry point (None = stay in the warm child's cgroup).
;;;                      notice-env   = the name of the environment variable that passes to the entry point the fd number of the read end of the notice pipe through which A relays input lines (None = no relay).
;;;                      orphan-seconds = the seconds to wait for the entry point to end by itself, treating EOF on A's input (requester gone) as a retreat (None = stop immediately on EOF --
;;;                                     the same function as --orphan-seconds of the container shim).
;;;   WriteWarmChildInput Writes text to the input pipe of a child asked with hold-input, and returns without waiting. Answer = WarmInputWritten (delivered = whether it was written --
;;;                    nothing is written for an ended child, a reused pid, a child whose pipe is not held, a pipe with no reader, or a full pipe).
;;;   PollWarmChild    Asks about the requested child's state exactly once. Answer = WarmRunning (a process with the same start-ticks exists and has not ended),
;;;                    WarmExited (the exit code in the exit file), WarmLost (gone or reused, and no exit file).
;;;   SignalWarmChild  Sends a signal to the requested child's group exactly once. Answer = WarmSignaled, WarmGone (gone, ended, or reused --
;;;                    never sent to someone else's process), or ProcessSignalRefused (process_effects.hy -- the OS refused the send, EPERM. The child keeps running.
;;;                    Example = a machine where AppArmor denies signals between processes inside a container). The end is read with PollWarmChild: TERM is not caught by child A, which writes the entry point's end (-15) to the exit
;;;                    file; KILL takes A down with it, so there is no exit file and the result is WarmLost.
;;;   AwaitWarmChildExit Waits until the requested child ends (#3871 -- consumer = the wait between rounds of the doeff-cluster worker). Answer = ProcessEnded
;;;                    (process_effects.hy -- ended, reused, or gone. The exit code is read later by PollWarmChild). The child is named by the pair of pid and start-ticks.
;;;                    The answerer is process-exit-handler of process_exit.hy (how to open an fd that becomes readable on exit varies per machine -- Linux = pidfd, macOS = kqueue). await-handler and scheduled are needed outside.
;;;
;;; The refusal, lost and not-sent reason texts, and the reading of the exit file's contents, are done by the same functions called from both the real and the no-I/O answerer (they answer in the same shape -- contract test
;;; tests/test_warm_process_contract.hy).
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord])
(import re)
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects._process_effects [EnvEntry ProcessEnded ProcessSignal ProcessSignalRefused])

;; Deadline in seconds until the warm child answers (all of connecting, sending, and receiving the one-line answer).
(val WARM-ANSWER-SECONDS 5.0)
;; Shape of the exit file's contents (a single signed integer).
(val EXIT-CODE-PATTERN "-?[0-9]+")


(defrecord WarmForked
  "The warm child accepted the job and started child A (the pair of pid and start-ticks = the mark that names the child in PollWarmChild and SignalWarmChild)."
  (#^ int pid)
  (#^ int start-ticks))


(defrecord WarmRefused
  "The job could not be requested (detail = reason text -- does not include env values)."
  (#^ str detail))


(defrecord WarmRunning
  "The child is still running."
  (#^ int pid))


(defrecord WarmExited
  "The child ended (exit-code = the exit file's value -- a negative value = the entry point ended by a signal)."
  (#^ int pid)
  (#^ int exit-code))


(defrecord WarmLost
  "The child's end cannot be read (detail = reason -- gone or reused, and no exit file / the exit file is unreadable)."
  (#^ int pid)
  (#^ str detail))


(defrecord WarmSignaled
  "Sent a signal to the child's group."
  (#^ int pid))


(defrecord WarmInputWritten
  "Answer of WriteWarmChildInput: delivered = wrote to the child's input pipe (True) / did not write (False -- header comment)."
  (#^ int pid)
  (#^ bool delivered))


(defrecord WarmGone
  "Did not send a signal (detail = reason -- gone, ended, or reused)."
  (#^ int pid)
  (#^ str detail))


(defclass [(dataclass :frozen True :kw-only True)] ForkFromWarm [(get EffectBase (| WarmForked WarmRefused))]
  "Ask the warm child to do one job (header comment). Answer = WarmForked or WarmRefused."
  #^ str socket-path
  #^ str entry
  #^ (get tuple #(str ...)) args
  (setv args #())
  #^ str cwd
  #^ (get tuple #(EnvEntry ...)) env
  (setv env #())
  #^ str log-path
  #^ str exit-path
  #^ float grace-seconds
  (setv grace-seconds 10.0)
  #^ bool hold-input
  (setv hold-input False)
  #^ (| str None) cgroup-procs
  (setv cgroup-procs None)
  #^ (| str None) notice-env
  (setv notice-env None)
  #^ (| float None) orphan-seconds
  (setv orphan-seconds None))


(defclass [(dataclass :frozen True :kw-only True)] WriteWarmChildInput [(get EffectBase WarmInputWritten)]
  "Writes text to the input pipe of a child asked with hold-input, and returns without waiting (header comment). Answer = WarmInputWritten."
  #^ int pid
  #^ int start-ticks
  #^ str text)


(defclass [(dataclass :frozen True :kw-only True)] PollWarmChild [(get EffectBase (| WarmRunning WarmExited WarmLost))]
  "Asks about the requested child's state exactly once, without waiting (header comment). Answer = WarmRunning, WarmExited or WarmLost."
  #^ int pid
  #^ int start-ticks
  #^ str exit-path)


(defclass [(dataclass :frozen True :kw-only True)] AwaitWarmChildExit [(get EffectBase ProcessEnded)]
  "Waits until the requested child ends (header comment). Answer = ProcessEnded."
  #^ int pid
  #^ int start-ticks)


(defclass [(dataclass :frozen True :kw-only True)] SignalWarmChild [(get EffectBase (| WarmSignaled ProcessSignalRefused WarmGone))]
  "Sends a signal to the requested child's group exactly once and returns without waiting (header comment). Answer = WarmSignaled, ProcessSignalRefused or WarmGone."
  #^ int pid
  #^ int start-ticks
  #^ ProcessSignal signal)


(defk warm-socket-missing [socket-path]
  {:pre [(: socket-path str)] :post [(: % str)] :tags {:context "process" :role "judgment"}}
  "To build the refusal text for when there is no socket."
  (.format "The warm child's socket does not exist: {}" socket-path))


(defk warm-socket-refused [socket-path]
  {:pre [(: socket-path str)] :post [(: % str)] :tags {:context "process" :role "judgment"}}
  "To build the refusal text for when the socket exists but the connection is refused (no warm child is there)."
  (.format "The warm child does not accept connections: {}" socket-path))


(defk warm-answer-late [socket-path]
  {:pre [(: socket-path str)] :post [(: % str)] :tags {:context "process" :role "judgment"}}
  "To build the refusal text for when the answer deadline expired."
  (.format "The warm child did not answer within {} seconds: {}" WARM-ANSWER-SECONDS socket-path))


(defk warm-answer-unreadable [socket-path]
  {:pre [(: socket-path str)] :post [(: % str)] :tags {:context "process" :role "judgment"}}
  "To build the refusal text for when the warm child's answer is not in the promised shape (one JSON line)."
  (.format "The warm child's answer is unreadable: {}" socket-path))


(defk warm-lost [pid exit-path]
  {:pre [(: pid int) (: exit-path str)] :post [(: % WarmLost)] :tags {:context "process" :role "judgment"}}
  "To build the answer for a child that is gone or reused and also has no exit file."
  (WarmLost :pid pid :detail (.format "The child of pid {} is gone (or reused by another process), yet there is no exit file: {}" pid exit-path)))


(defk warm-gone [pid]
  {:pre [(: pid int)] :post [(: % WarmGone)] :tags {:context "process" :role "judgment"}}
  "To build the answer for not sending a signal to a child that is gone, ended, or reused."
  (WarmGone :pid pid :detail (.format "The child of pid {} is gone or has ended (or was reused by another process), so nothing is sent" pid)))


(defk warm-exit-answer [pid exit-path text]
  {:pre [(: pid int) (: exit-path str) (: text str)] :post [(: % (| WarmExited WarmLost))] :tags {:context "process" :role "judgment"}}
  "To read the exit file's contents text. A single integer (surrounding whitespace skipped) gives WarmExited; anything else gives WarmLost."
  (val word (.strip text))
  (if (re.fullmatch EXIT-CODE-PATTERN word)
      (WarmExited :pid pid :exit-code (int word))
      (WarmLost :pid pid :detail (.format "The exit file of the child of pid {} is not a single integer: {}" pid exit-path))))


(defk signal-number-of [signal]
  {:pre [(: signal ProcessSignal)] :post [(: % int)] :tags {:context "process" :role "judgment"}}
  "Closed-type signal to number (TERM = 15, KILL = 9 -- the exit code when a child ends by a signal is the negative of this number)."
  (match signal ProcessSignal.TERM 15 ProcessSignal.KILL 9))
