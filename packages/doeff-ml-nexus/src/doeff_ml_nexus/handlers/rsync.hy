;;; Rsync handler
;;; Handles RsyncTo effect via rsync shell command.

(require doeff_hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff [Resume Pass])
(import doeff_core_effects [slog])

(import subprocess)

(import doeff_ml_nexus.effects [RsyncTo])


(defn _rsync-args [src host dst-path * [excludes #()] [includes #()]]
  "Build rsync command as list of args."
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


(defk rsync-handler [effect k]
  "Handle RsyncTo: rsync files to destination."
  (if (isinstance effect RsyncTo)
      (do
        (<- (slog :msg f"rsync: {effect.src} -> {effect.host}:{effect.dst-path}"))
        ;; Ensure destination directory exists
        (when (!= effect.host "localhost")
          (subprocess.run ["ssh" effect.host f"mkdir -p {effect.dst-path}"]
                          :check True :capture-output True))
        (setv args (_rsync-args effect.src effect.host effect.dst-path
                                :excludes effect.excludes
                                :includes effect.includes))
        (subprocess.run args :check True :capture-output True)
        (yield (Resume k effect.dst-path)))
      (yield (Pass effect k))))
