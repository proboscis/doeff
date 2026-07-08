;;; File operation handlers
;;; Handles WriteFile effect via ShellRun effects.
;;;
;;; Handler clauses yield ShellRun instead of calling subprocess/Path IO
;;; directly.  The production shell-run-handler resolves them at the IO
;;; boundary.

(require doeff_hy.macros [<- defhandler])
(import doeff [do :as _doeff-do])

(import doeff_docker.effects [ShellRun])
(import doeff_ml_nexus.effects [WriteFile])


(defhandler write-file-handler
  "Handle WriteFile: write content to a file on a host."
  (WriteFile [host path content]
    (setv encoded (.encode content))
    (setv args
      (if (= host "localhost")
          #("tee" path)
          #("ssh" host f"cat > {path}")))
    (<- result (ShellRun :args args :stdin-data encoded))
    (when (!= result.returncode 0)
      (raise (RuntimeError
               f"WriteFile failed (rc={result.returncode}):\n{(list args)}\nstderr: {(.decode result.stderr)}")))
    (resume path)))
