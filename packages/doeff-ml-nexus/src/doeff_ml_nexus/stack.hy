;;; Handler stack composition
;;; Provides a standard handler chain for doeff-ml-nexus programs.

(require doeff_hy.macros [defk <-])
(require doeff_docker.compose [with-handlers])
(import doeff [do :as _doeff-do])
(import doeff [run])
(import doeff_core_effects [reader state writer slog-handler scheduled])

(import doeff_ml_nexus.serializer [default-serializer])
(import doeff_ml_nexus.handlers.resolve [resolve-handler])
(import doeff_ml_nexus.handlers.docker [docker-run-handler])
(import doeff_ml_nexus.handlers.rsync [rsync-handler])
(import doeff_ml_nexus.handlers.file [write-file-handler])
(import doeff_docker.handlers.docker [docker-build-handler image-push-handler])
(import doeff_docker.handlers.shell [shell-run-handler])


(defn ml-nexus-interpreter [program * [env None]]  ; doeff: interpreter
  "Standard interpreter for doeff-ml-nexus programs."
  (setv resolved-env
    (if (is env None)
        {"source_root" "~/repos"
         "serializer" default-serializer}
        (| {"source_root" "~/repos"
            "serializer" default-serializer}
           (if (isinstance env dict) env (run env)))))

  (run (scheduled
    ;; slog-handler and writer keep their logs via Get/Put, so state must sit outside them.
    (with-handlers
      [(state)
       (reader :env resolved-env)
       slog-handler
       writer
       resolve-handler
       write-file-handler
       rsync-handler
       image-push-handler
       docker-build-handler
       docker-run-handler
       shell-run-handler]
      program))))
