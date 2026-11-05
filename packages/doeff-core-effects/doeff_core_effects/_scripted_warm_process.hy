;;; scripted-warm-process-handler, the I/O-free handler for the warm-child effects (warm_effects.hy).
;;; Instead of real sockets and processes it answers from a world value, WarmScript. It knows no business logic: the content of the script is passed in by the caller (the test).
;;;
;;;   ForkFromWarm     A socket-path not in WarmScript's sockets = a refusal that the socket does not exist. An existing socket answers according to its answer:
;;;                    ACCEPTS = accept the job (an exception, as a script error, if the script has no WarmRun for the entry) / REFUSES = refuse with the refusal text /
;;;                    SILENT = a refusal as the answer deadline expiring. An accepted child is given a pid counting up from SCRIPTED-WARM-FIRST-PID and start-ticks counting up from
;;;                    SCRIPTED-WARM-FIRST-TICKS.
;;;   PollWarmChild    If the pid and start-ticks match an accepted child that has not ended, answer WarmRunning as many times as the WarmRun's polls count, then it ends.
;;;                    An ended child is WarmExited(exit-code) if writes-exit, otherwise WarmLost (a child that vanished without writing the exit file).
;;;                    For mismatched start-ticks (pid reuse) or an unknown pid: WarmExited if it is a child that ended and wrote the exit file, otherwise
;;;                    WarmLost - the same as the real one reading the exit file when the process is gone or reused.
;;;   WriteWarmChildInput  Only a child whose pid and start-ticks match, which was accepted with hold-input and has not ended, counts as written (delivered in WarmInputWritten).
;;;   SignalWarmChild  Only a child whose pid and start-ticks match and has not ended is turned into an ended one and answered WarmSignaled. Otherwise WarmGone (no signal goes to
;;;                    someone else's process). TERM = child A survives and the entry's ending (-15) is written to the exit file / KILL = A dies along with it and the exit
;;;                    file is not written (a later PollWarmChild is WarmLost) - the same as the real group signal.
;;; The reason text and the reading of the exit file's value use the same functions as the real one (warm_effects.hy). The contract with the real one is tests/test_warm_process_contract.hy.
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord defenum])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import dataclasses [dataclass replace])
(import enum [StrEnum])
(import doeff_core_effects._process_effects [ProcessSignal])
(import doeff_core_effects._warm_effects [ForkFromWarm PollWarmChild SignalWarmChild WriteWarmChildInput WarmInputWritten WarmForked WarmRefused WarmRunning WarmExited
                                         WarmLost WarmSignaled WarmGone warm-socket-missing warm-answer-late warm-lost warm-gone
                                         signal-number-of])

;; Starting values for the pid and start-ticks handed out in the script's world (sized so as not to mix with real values).
(val SCRIPTED-WARM-FIRST-PID 60000)
(val SCRIPTED-WARM-FIRST-TICKS 900000)


;; How the script's socket answers (a closed type): ACCEPTS = accept the job / REFUSES = answer a refusal / SILENT = do not answer until the deadline.
(defenum WarmSocketAnswer ACCEPTS REFUSES SILENT)
(setv WarmSocketAnswer.__doc__ "How the script's socket answers: ACCEPTS = accept the job / REFUSES = answer a refusal / SILENT = do not answer until the deadline.")


(defrecord WarmSocket
  "One warm-child socket in the script's world (path, answer, and the refusal text refusal used when REFUSES)."
  (#^ str path)
  (setv #^ WarmSocketAnswer answer WarmSocketAnswer.ACCEPTS)
  (setv #^ str refusal ""))


(defrecord WarmRun
  "How one scripted job ends (entry = the name of the request entry / polls = how many times WarmRunning is answered before ending / exit-code = the value of the exit
   file when it ends / writes-exit = if False, vanishes without writing the exit file)."
  {:check [(>= polls 0)]}
  (#^ str entry)
  (setv #^ int polls 0)
  (setv #^ int exit-code 0)
  (setv #^ bool writes-exit True))


(defrecord WarmScript
  "The world passed to scripted-warm-process-handler (sockets = the existing warm-child sockets / runs = how each entry name ends)."
  (setv #^ (get tuple #(WarmSocket ...)) sockets #())
  (setv #^ (get tuple #(WarmRun ...)) runs #()))


(defrecord WarmChildState
  "The current state of one accepted child (start-ticks / remaining WarmRunning count / whether it ended / the value at the end and whether the exit file exists / whether it holds the input pipe)."
  (#^ int start-ticks)
  (#^ int polls-left)
  (#^ bool ended)
  (#^ int exit-code)
  (#^ bool writes-exit)
  (setv #^ bool holds-input False))


(defk scripted-fork [script request pid ticks]
  {:pre [(: script WarmScript) (: request ForkFromWarm) (: pid int) (: ticks int)]
   :post [(: % (| WarmChildState WarmRefused))] :tags {:context "process" :role "judgment"}}
  "Decide the answer to a job request in the script's world: a refusal (WarmRefused) or the initial state of the accepted child (WarmChildState)."
  (val found (lfor s script.sockets :if (= s.path request.socket-path) s))
  (<- missing str (warm-socket-missing request.socket-path))
  (<- late str (warm-answer-late request.socket-path))
  (when (not found)
    (return (WarmRefused :detail missing)))
  (val sock (get found 0))
  (match sock.answer
    WarmSocketAnswer.REFUSES (WarmRefused :detail sock.refusal)
    WarmSocketAnswer.SILENT (WarmRefused :detail late)
    WarmSocketAnswer.ACCEPTS
    (do (val runs (lfor r script.runs :if (= r.entry request.entry) r))
        (when (not runs)
          (raise (ValueError (.format "The script has no WarmRun for entry {} (an error in the test's world)" request.entry))))
        (val run (get runs 0))
        (WarmChildState :start-ticks ticks :polls-left run.polls :ended False :exit-code run.exit-code :writes-exit run.writes-exit
                        :holds-input request.hold-input))))


(defk ended-answer [pid exit-path state]
  {:pre [(: pid int) (: exit-path str) (: state WarmChildState)] :post [(: % (| WarmExited WarmLost))]
   :tags {:context "process" :role "judgment"}}
  "Decide the answer for an ended child: a child that wrote the exit file is WarmExited, one that vanished without writing it is WarmLost."
  (<- lost WarmLost (warm-lost pid exit-path))
  (if state.writes-exit (WarmExited :pid pid :exit-code state.exit-code) lost))


(defhandler scripted-warm-process-handler [#^ WarmScript script]
  "A mock handler that answers the warm-child effects (ForkFromWarm, PollWarmChild, WriteWarmChildInput, SignalWarmChild) from the script SCRIPT (sockets and jobs)."
  ;; Why it stays an argument: the script of sockets and jobs is a different value per scenario (not a setting but the mock world itself).
  ;; Table of accepted children (pid -> WarmChildState), and the next number to hand out.
  (session val children ((get dict #(int WarmChildState))))
  (session var forks 0)
  (ForkFromWarm [socket-path entry args cwd env log-path exit-path grace-seconds hold-input cgroup-procs notice-env orphan-seconds]
    (val pid (+ SCRIPTED-WARM-FIRST-PID forks))
    (val ticks (+ SCRIPTED-WARM-FIRST-TICKS forks))
    (<- decided (| WarmChildState WarmRefused)
        (scripted-fork script (ForkFromWarm :socket-path socket-path :entry entry :args args :cwd cwd :env env
                                            :log-path log-path :exit-path exit-path :grace-seconds grace-seconds
                                            :hold-input hold-input :cgroup-procs cgroup-procs :notice-env notice-env
                                            :orphan-seconds orphan-seconds)
                       pid ticks))
    (if (isinstance decided WarmRefused)
        (resume decided)
        (do (:= forks (+ forks 1))
            (setv (get children pid) decided)
            (resume (WarmForked :pid pid :start-ticks ticks)))))
  (PollWarmChild [pid start-ticks exit-path]
    (val state (.get children pid))
    (<- lost WarmLost (warm-lost pid exit-path))
    (cond
      (is state None) (resume lost)
      ;; A reused pid (different start-ticks) is answered from the exit file alone, the same as the real one.
      (!= state.start-ticks start-ticks) (resume (if (and state.ended state.writes-exit)
                                                      (WarmExited :pid pid :exit-code state.exit-code)
                                                      lost))
      (and (not state.ended) (> state.polls-left 0))
      (do (setv (get children pid) (replace state :polls-left (- state.polls-left 1)))
          (resume (WarmRunning :pid pid)))
      True
      (do (val ended (replace state :ended True))
          (setv (get children pid) ended)
          (<- answer (| WarmExited WarmLost) (ended-answer pid exit-path ended))
          (resume answer))))
  (WriteWarmChildInput [pid start-ticks text]
    (val state (.get children pid))
    (resume (WarmInputWritten :pid pid :delivered (and (is-not state None) (= state.start-ticks start-ticks) (not state.ended)
                                                       state.holds-input))))
  (SignalWarmChild [pid start-ticks signal]
    (val state (.get children pid))
    (<- gone WarmGone (warm-gone pid))
    (<- number int (signal-number-of signal))
    (if (or (is state None) (!= state.start-ticks start-ticks) state.ended)
        (resume gone)
        (do (setv (get children pid) (replace state :ended True :exit-code (- number) :writes-exit (= signal ProcessSignal.TERM)))
            (resume (WarmSignaled :pid pid))))))
