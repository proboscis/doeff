;;; os-warm-process-handler: the real handler for the warm-child effects (warm_effects.hy).
;;;
;;;   ForkFromWarm     Send one line of request JSON to the warm child's unix socket (AF_UNIX, SOCK_STREAM) and receive one line of answer JSON. The deadline is
;;;                    WARM-ANSWER-SECONDS for connect, send and receive combined. It does not check whether the socket exists or its mode (the creating side = the warm child restricts it by mode -- there is no branch,
;;;                    roster or passphrase that verifies the peer's identity). Refusal text is built by a function in warm_effects.hy and never includes env values.
;;;   PollWarmChild    Read the state and start-ticks of the process pid with the per-machine reader proc-stat-of (Linux = state [3rd
;;;                    field] and starttime [22nd field] of /proc/<pid>/stat; macOS = proc_bsdinfo of libproc in darwin_proc.hy). If a process with the same start-ticks that has not ended
;;;                    (neither zombie nor being reaped -- outside ENDED-STATES in process_stat.hy) exists, answer WarmRunning;
;;;                    otherwise read the exit file (WarmExited if present, WarmLost if absent).
;;;   SignalWarmChild  Only when a process with the same start-ticks that has not ended exists, send with os.killpg to its group (child A is the head of the setsid group).
;;;                    If the OS refuses the send (EPERM), raise ProcessSignalRefused -- never silently answer WarmSignaled.
;;;   A hold-input request creates a pipe, attaches its read end to the first byte of the request line (SCM_RIGHTS), and holds
;;;   the write end of the receiving child in a table. WriteWarmChildInput writes only to the write end of a child running with the same start-ticks, and closes it when PollWarmChild answers that the child ended.
;;;   When the requester (worker) disappears, the write end closes and child A reads EOF on its input.
;;;   On a machine with no known reader (outside Linux and macOS) or a Linux without /proc, raise WarmProcUnavailable -- never silently turn it into Lost / Gone.
;;;   The warm child (warm_child.py in doeff-cluster) also reads the start-ticks of the child it split off and its own thread count with the same readers (proc-stat-of, own-thread-count)
;;;   -- the requester and the warm child check against the same start-ticks.
;;;
;;; Request and answer shapes (the agreement with the warm child -- the types are the defwire below, and the warm child side reads and writes with the same functions):
;;;   request = WarmRequestWire {"entry", "args": [str], "cwd", "env": [{"name", "value"}], "logPath", "exitPath", "graceSeconds"
;;;          [, "holdInput", "cgroupProcs", "noticeEnv", "orphanSeconds"]} (the last 4 are omittable fields -- not written when at their default values)
;;;   answer = WarmForkedWire {"pid", "startTicks"} or WarmRefusedWire {"detail"} (unknown fields are refused -- this is how the two shapes are told apart)
;;;   Each is one JSON line, utf-8, ending with a newline. warm-request-line (the requester sends), warm-request-of (the warm child reads), warm-answer-line
;;;   (the warm child answers), warm-answer-of (the requester reads).
;;; The contract with the real thing is tests/test_warm_process_contract.hy.
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord defwire])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import json)
(import os)
(import platform)
(import socket)
(import dataclasses [dataclass])
(import doeff_hy._wire [Malformed dump parse-json])
(import doeff_core_effects._process_effects [ProcessSignal ProcessSignalRefused])
(import doeff_core_effects._process_stat [ProcStat ENDED-STATES])
(import doeff_core_effects._darwin_proc [darwin-proc-stat darwin-thread-count])
(import doeff_core_effects._warm_effects [ForkFromWarm PollWarmChild SignalWarmChild WriteWarmChildInput WarmInputWritten WarmForked WarmRefused WarmRunning WarmExited
                                         WarmLost WarmSignaled WarmGone WARM-ANSWER-SECONDS warm-socket-missing warm-socket-refused
                                         warm-answer-late warm-answer-unreadable warm-lost warm-gone warm-exit-answer signal-number-of])

;; Root for reading process status (Linux procfs).
(val PROC-ROOT "/proc")
;; Upper limit on the length of an answer line (bytes) -- so that the receive stops even if a peer that left the agreed shape keeps streaming without sending a newline.
(val ANSWER-LIMIT 65536)


(defclass WarmProcUnavailable [RuntimeError]
  "This machine has no known reader for process status (outside Linux and macOS, or a Linux without /proc), so the end of a warm child cannot be read and the target of a signal
   cannot be verified.")


(defwire WarmEnvWire
  "One entry of the request env (one environment variable of the child)."
  {:tags {:context "process" :role "type" :reads "json"} :names :camel :unknown :reject}
  (#^ str name)
  (#^ str value))


(defwire WarmRequestWire
  "One request the warm child receives (all fields of ForkFromWarm except socket-path -- see the header note)."
  {:tags {:context "process" :role "type" :reads "json"} :names :camel :unknown :reject}
  (#^ str entry)
  (#^ (get tuple #(str ...)) args)
  (#^ str cwd)
  (#^ (get tuple #(WarmEnvWire ...)) env)
  (#^ str log-path)
  (#^ str exit-path)
  (#^ float grace-seconds)
  ;; The 3 fields for a resident child (header note of warm_effects.hy). Default values are not written (dump omits them), so the shape of a task request stays
  ;; the same as before (a warm child of a version that does not know these fields also accepts the task). A hold-input request attaches the read end of the input pipe by fd passing.
  (setv #^ bool hold-input False)
  (setv #^ (| str None) cgroup-procs None)
  (setv #^ (| str None) notice-env None)
  (setv #^ (| float None) orphan-seconds None))


(defwire WarmForkedWire
  "Warm child answer: accepted the job and started child A."
  {:tags {:context "process" :role "type" :reads "json"} :names :camel :unknown :reject}
  (#^ int pid)
  (#^ int start-ticks))


(defwire WarmRefusedWire
  "Warm child answer: refused the job (detail = reason -- never includes env values)."
  {:tags {:context "process" :role "type" :reads "json"} :names :camel :unknown :reject}
  (#^ str detail))


(defk wire-line [wire]
  {:pre [(: wire (| WarmRequestWire WarmForkedWire WarmRefusedWire))] :post [(: % bytes)] :tags {:context "process" :role "foundation"}}
  "Turn a typed value into one line of the agreed form with the warm child (JSON, utf-8, ending with a newline) so it can be sent."
  (<- raw (dump wire))
  (.encode (+ (json.dumps raw :ensure-ascii False) "\n") "utf-8"))


(defk warm-request-line [request]
  {:pre [(: request ForkFromWarm)] :post [(: % bytes)] :tags {:context "process" :role "foundation"}}
  "Build the line the requester sends to the warm child (the request shape in the header note)."
  (<- line bytes (wire-line (WarmRequestWire :entry request.entry :args request.args :cwd request.cwd
                                             :env (tuple (gfor e request.env (WarmEnvWire :name e.name :value e.value)))
                                             :log-path request.log-path :exit-path request.exit-path
                                             :grace-seconds request.grace-seconds :hold-input request.hold-input
                                             :cgroup-procs request.cgroup-procs :notice-env request.notice-env
                                             :orphan-seconds request.orphan-seconds)))
  line)


(defk warm-request-of [line]
  {:pre [(: line bytes)] :post [(: % (| WarmRequestWire Malformed))] :tags {:context "process" :role "foundation"}}
  "Read a line the warm child received as a request. If it is not in the agreed shape, Malformed (the warm child answers a refusal)."
  (<- request (| WarmRequestWire Malformed) (parse-json WarmRequestWire line))
  request)


(defk warm-answer-line [answer]
  {:pre [(: answer (| WarmForked WarmRefused))] :post [(: % bytes)] :tags {:context "process" :role "foundation"}}
  "Build the line the warm child returns to the requester (the answer shape in the header note)."
  (val wire (match answer
              (WarmForked :pid pid :start-ticks ticks) (WarmForkedWire :pid pid :start-ticks ticks)
              (WarmRefused :detail detail) (WarmRefusedWire :detail detail)))
  (<- line bytes (wire-line wire))
  line)


(defk warm-answer-of [line socket-path]
  {:pre [(: line bytes) (: socket-path str)] :post [(: % (| WarmForked WarmRefused))] :tags {:context "process" :role "foundation"}}
  "Read a line the requester received as an answer. If it is neither of the 2 shapes, an unreadable refusal (warm-answer-unreadable)."
  (<- forked (| WarmForkedWire Malformed) (parse-json WarmForkedWire line))
  (<- refused (| WarmRefusedWire Malformed) (parse-json WarmRefusedWire line))
  (<- unreadable str (warm-answer-unreadable socket-path))
  (match #(forked refused)
    #((WarmForkedWire :pid pid :start-ticks ticks) _) (WarmForked :pid pid :start-ticks ticks)
    #(_ (WarmRefusedWire :detail detail)) (WarmRefused :detail detail)
    _ (WarmRefused :detail unreadable)))


(defk received-line [connection]
  {:pre [(: connection socket.socket)] :post [(: % (| bytes None))] :tags {:context "process" :role "foundation"}}
  "Receive one line from the connection up to the newline (the deadline is the connection timeout). Closed before a newline or over the limit = None."
  (var received b"")
  (var closed False)
  (while (not (or closed (in b"\n" received) (> (len received) ANSWER-LIMIT)))
    (val chunk (.recv connection 4096))
    (if chunk
        (:= received (+ received chunk))
        (:= closed True)))
  (if (in b"\n" received)
      (get (.split received b"\n" 1) 0)
      None))


(defrecord HeldInput
  "Write end of the input pipe of a child requested with hold-input (the requester holds it until the child ends): start-ticks = the child's mark (not keyed by pid, which can be reused), fd = write end
   (a non-blocking end -- does not stop the requester even when the pipe fills because the child does not read)."
  (#^ int start-ticks)
  (#^ int fd))


(defk os-fork-from-warm [request input-fd]
  {:pre [(: request ForkFromWarm) (: input-fd (| int None))] :post [(: % (| WarmForked WarmRefused))] :tags {:context "process" :role "foundation"}}
  "Ask the warm child for one job (header note). No socket, connection refused, deadline expired, or unreadable answer = refusal (the reason text contains no env values).
   input-fd = the read end of the child's input pipe (a hold-input request -- attached to the first byte of the request line and sent by fd passing; None = not attached)."
  (val path request.socket-path)
  (<- missing str (warm-socket-missing path))
  (<- refused str (warm-socket-refused path))
  (<- late str (warm-answer-late path))
  (<- unreadable str (warm-answer-unreadable path))
  (<- line bytes (warm-request-line request))
  (val received (with [connection (socket.socket socket.AF-UNIX socket.SOCK-STREAM)]
                  (.settimeout connection WARM-ANSWER-SECONDS)
                  (try
                    (do (.connect connection path)
                        ;; The fd to attach goes in a 1-byte sendmsg (a sendmsg of a large line may send only part of it -- the rest goes by sendall).
                        (if (is input-fd None)
                            (.sendall connection line)
                            (do (socket.send-fds connection #((cut line 0 1)) #(input-fd))
                                (.sendall connection (cut line 1 None))))
                        (<- answered (| bytes None) (received-line connection))
                        (if (is answered None) unreadable answered))
                    (except [FileNotFoundError] missing)
                    (except [ConnectionRefusedError] refused)
                    (except [TimeoutError] late))))
  (if (isinstance received str)
      (WarmRefused :detail received)
      (do (<- answer (| WarmForked WarmRefused) (warm-answer-of received path))
          answer)))


(defk proc-stat-of [pid]
  {:pre [(: pid int)] :post [(: % (| ProcStat None))] :tags {:context "process" :role "foundation"}}
  "Read the state and start-ticks of the process pid with this machine's reader (header note). Does not exist = None. A machine with no known reader raises WarmProcUnavailable."
  (val system (platform.system))
  (cond
    (= system "Linux") (do (<- seen (| ProcStat None) (linux-proc-stat pid)) seen)
    (= system "Darwin") (do (<- seen (| ProcStat None) (darwin-proc-stat pid)) seen)
    True (raise (WarmProcUnavailable (.format "Machine with no known process status reader: {}" system)))))


(defk own-thread-count []
  {:pre [] :post [(: % int)] :tags {:context "process" :role "foundation"}}
  "Read this process's OS thread count with this machine's reader (the warm child checks that it is 1 before fork -- header note)."
  (val system (platform.system))
  (cond
    (= system "Linux") (len (os.listdir (os.path.join PROC-ROOT "self" "task")))
    (= system "Darwin") (do (<- threads int (darwin-thread-count)) threads)
    True (raise (WarmProcUnavailable (.format "Machine with no known thread count reader: {}" system)))))


(defk own-oom-score-adj []
  {:pre [] :post [(: % (| int None))] :tags {:context "process" :role "foundation"}}
  "Read this process's oom_score_adj (the warm child raises its own while it waits and gives the inherited value back to the child it splits off).
   A machine without it (outside Linux, or a Linux without /proc) = None -- the kernel there picks no OOM victim by this value."
  (val path (os.path.join PROC-ROOT "self" "oom_score_adj"))
  (if (and (= (platform.system) "Linux") (os.path.isfile path))
      (with [f (open path :encoding "ascii")] (int (.strip (.read f))))
      None))


(defk put-own-oom-score-adj [value]
  {:pre [(: value int) (<= -1000 value 1000)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "Write this process's oom_score_adj (Linux: raising it, or giving back a value at or above the one a privileged writer last set, needs no privilege).
   A machine without it = False (nothing written)."
  (val path (os.path.join PROC-ROOT "self" "oom_score_adj"))
  (when (not (and (= (platform.system) "Linux") (os.path.isfile path)))
    (return False))
  (with [f (open path "w" :encoding "ascii")] (.write f (str value)))
  True)


(defk linux-proc-stat [pid]
  {:pre [(: pid int)] :post [(: % (| ProcStat None))] :tags {:context "process" :role "foundation"}}
  "Read the state and start-ticks of the process pid from /proc on Linux. Does not exist = None. Without /proc, WarmProcUnavailable.
   comm (the 2nd field) may contain spaces or parentheses, so split what follows the last ')' on whitespace (the 3rd field comes first; the 22nd field is its 20th)."
  (when (not (os.path.isdir PROC-ROOT))
    (raise (WarmProcUnavailable (.format "On a machine without {}, the end of a warm child cannot be read" PROC-ROOT))))
  (when (<= pid 0)
    (return None))
  (val text (try (with [f (open (os.path.join PROC-ROOT (str pid) "stat") :encoding "utf-8" :errors "surrogateescape")] (.read f))
                 (except [[FileNotFoundError ProcessLookupError]] None)))
  (if (is text None)
      None
      (do (val fields (.split (get (.rsplit text ")" 1) 1)))
          (ProcStat :state (get fields 0) :start-ticks (int (get fields 19))))))


(defk same-child-running [pid start-ticks]
  {:pre [(: pid int) (: start-ticks int)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "Check that pid is still the process with the same start-ticks as when requested and has not ended yet (its state is neither zombie nor being reaped --
   outside ENDED-STATES)."
  (<- seen (| ProcStat None) (proc-stat-of pid))
  (and (is-not seen None) (= seen.start-ticks start-ticks) (not-in seen.state ENDED-STATES)))


(defk os-poll-warm-child [pid start-ticks exit-path]
  {:pre [(: pid int) (: start-ticks int) (: exit-path str)] :post [(: % (| WarmRunning WarmExited WarmLost))]
   :tags {:context "process" :role "foundation"}}
  "Read the requested child's status just once (header note): running = WarmRunning; otherwise answer with the contents of the exit file."
  (<- running bool (same-child-running pid start-ticks))
  (when running
    (return (WarmRunning :pid pid)))
  (val text (try (with [f (open exit-path :encoding "utf-8" :errors "surrogateescape")] (.read f))
                 (except [FileNotFoundError] None)))
  (if (is text None)
      (do (<- lost WarmLost (warm-lost pid exit-path))
          lost)
      (do (<- answer (| WarmExited WarmLost) (warm-exit-answer pid exit-path text))
          answer)))


(defk os-signal-warm-child [pid start-ticks sent]
  {:pre [(: pid int) (: start-ticks int) (: sent ProcessSignal)] :post [(: % (| WarmSignaled ProcessSignalRefused WarmGone))]
   :tags {:context "process" :role "foundation"}}
  "Send a signal to the requested child's group (header note). Send only when it is running with the same start-ticks, never to someone else's process. If the OS refuses
   the send (EPERM), ProcessSignalRefused -- the child keeps running."
  (<- running bool (same-child-running pid start-ticks))
  (<- gone WarmGone (warm-gone pid))
  (<- number int (signal-number-of sent))
  (if (not running)
      gone
      (try
        (do (os.killpg pid number) (WarmSignaled :pid pid))
        (except [ProcessLookupError] gone)
        (except [PermissionError] (ProcessSignalRefused :pid pid)))))


(defk input-written [fd text]
  {:pre [(: fd int) (: text str)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "Write text to the held input pipe. Answer = whether it was written (a pipe with no reader or a full pipe is not written -- a line is shorter than PIPE_BUF, so a write is
   either complete or BlockingIOError)."
  (try
    (do (os.write fd (.encode text "utf-8")) True)
    (except [[BrokenPipeError BlockingIOError]] False)))


(defhandler os-warm-process-handler
  "Answer the warm-child effects (ForkFromWarm, PollWarmChild, WriteWarmChildInput, SignalWarmChild) with the real warm child and the per-machine process status readers."
  ;; The real warm child and the per-machine process status readers (header note).
  ;; Table of write ends of the input pipes of children requested with hold-input (pid -> HeldInput). Closed and removed when the child's end is read.
  ;; The table lives in the session (needs state or a session-store outside -- the production worker places it inside the session-store). The dict is mutated, not replaced.
  (session val inputs ((get dict #(int HeldInput))))
  (ForkFromWarm [socket-path entry args cwd env log-path exit-path grace-seconds hold-input cgroup-procs notice-env orphan-seconds]
    (val request (ForkFromWarm :socket-path socket-path :entry entry :args args :cwd cwd :env env :log-path log-path
                               :exit-path exit-path :grace-seconds grace-seconds :hold-input hold-input
                               :cgroup-procs cgroup-procs :notice-env notice-env
                               :orphan-seconds orphan-seconds))
    (if (not hold-input)
        (do (<- answer (os-fork-from-warm request None))
            (resume answer))
        ;; Input pipe of child A only: the read end is attached to the request and our copy is closed (A is the only reader). Only the write end of the receiving child is held.
        (do (val pipe (os.pipe))
            (<- answer (os-fork-from-warm request (get pipe 0)))
            (os.close (get pipe 0))
            (if (isinstance answer WarmForked)
                (do (os.set-blocking (get pipe 1) False)
                    (setv (get inputs answer.pid) (HeldInput :start-ticks answer.start-ticks :fd (get pipe 1))))
                (os.close (get pipe 1)))
            (resume answer))))
  (PollWarmChild [pid start-ticks exit-path]
    (<- answer (os-poll-warm-child pid start-ticks exit-path))
    ;; Close the write end of the input pipe of an ended child (only the one belonging to a child with the same start-ticks).
    (val held (.get inputs pid))
    (when (and (is-not held None) (= held.start-ticks start-ticks) (not (isinstance answer WarmRunning)))
      (os.close held.fd)
      (.pop inputs pid))
    (resume answer))
  (WriteWarmChildInput [pid start-ticks text]
    (val held (.get inputs pid))
    (<- running bool (same-child-running pid start-ticks))
    (var delivered False)
    (when (and (is-not held None) (= held.start-ticks start-ticks) running)
      (<- wrote bool (input-written held.fd text))
      (:= delivered wrote))
    (resume (WarmInputWritten :pid pid :delivered delivered)))
  (SignalWarmChild [pid start-ticks signal]
    (<- answer (os-signal-warm-child pid start-ticks signal))
    (resume answer)))
