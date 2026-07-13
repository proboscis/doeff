;;; Rsync handler
;;; Handles RsyncTo effect via ShellRun effects.
;;;
;;; Handler clauses yield ShellRun instead of calling subprocess directly.
;;; The production shell-run-handler resolves them at the IO boundary.

(require doeff_hy.macros [defk <- defhandler])
(import doeff [do :as _doeff-do])
(import doeff_core_effects [slog])

(import pathlib [Path])

(import doeff_docker.effects [ShellRun])
(import doeff_ml_nexus.effects [RsyncTo])


(defk _rsync-args [src host dst-path * [excludes #()] [includes #()]]
  "Build rsync command as list of args."
  {:pre [(: src Path)
         (: host str)
         (: dst-path str)
         (: excludes tuple)
         (: includes tuple)]
   :post [(: % list)]}
  (setv parts ["rsync" "-avz" "--delete"])
  ;; includes must come before excludes in rsync
  (for [i includes]
    (.extend parts ["--include" i]))
  (for [e excludes]
    (.extend parts ["--exclude" e]))
  (setv src-str (str src))
  (when (not (.endswith src-str "/"))
    (setv src-str (+ src-str "/")))
  (if (= host "localhost")
      (.extend parts [src-str dst-path])
      (.extend parts [src-str f"{host}:{dst-path}"]))
  parts)


(defhandler rsync-handler
  "Handle RsyncTo: rsync files to destination."
  (RsyncTo [src host dst-path excludes includes]
    (<- (slog :msg f"rsync: {src} -> {host}:{dst-path}"))
    ;; Ensure destination directory exists
    (when (!= host "localhost")
      (setv mkdir-args ["ssh" host f"mkdir -p {dst-path}"])
      (<- mkdir-result (ShellRun :args (tuple mkdir-args)))
      (when (!= mkdir-result.returncode 0)
        (raise (RuntimeError
                 f"Command failed (rc={mkdir-result.returncode}):\n{mkdir-args}\nstderr: {(.decode mkdir-result.stderr)}"))))
    (<- args (_rsync-args src host dst-path
                          :excludes excludes
                          :includes includes))
    (<- rsync-result (ShellRun :args (tuple args)))
    (when (!= rsync-result.returncode 0)
      (raise (RuntimeError
               f"Command failed (rc={rsync-result.returncode}):\n{args}\nstderr: {(.decode rsync-result.stderr)}")))
    (resume dst-path)))
