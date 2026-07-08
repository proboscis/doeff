;;; Shell subprocess handler — the IO boundary for ShellRun effects.
;;;
;;; This is the production handler that actually calls subprocess.run.
;;; Handler clauses in docker.hy, rsync.hy, file.hy yield ShellRun effects
;;; instead of calling subprocess directly; this handler sits at the outer
;;; boundary of the handler stack and resolves those effects.

(require doeff_hy.macros [defhandler])
(import doeff [do :as _doeff-do])

(import subprocess)

(import doeff_docker.effects [ShellRun ShellRunResult])


(defhandler shell-run-handler
  "Handle ShellRun: execute subprocess and return ShellRunResult.
   This is the IO boundary — the only place subprocess.run is called."
  (ShellRun [args stdin-data]
    (setv proc (subprocess.run (list args)
                               :capture-output True
                               :input stdin-data))
    (resume (ShellRunResult :returncode proc.returncode
                            :stdout proc.stdout
                            :stderr proc.stderr))))
