;;; Rsync handler
;;; Handles RsyncTo effect via rsync shell command.

(require doeff_hy.macros [defk <- defhandler])
(import doeff [do :as _doeff-do])
(import doeff_core_effects [slog])

(import subprocess)
(import pathlib [Path])

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
      (subprocess.run ["ssh" host f"mkdir -p {dst-path}"]
                      :check True :capture-output True))
    (<- args (_rsync-args src host dst-path
                          :excludes excludes
                          :includes includes))
    (subprocess.run args :check True :capture-output True)
    (resume dst-path)))
