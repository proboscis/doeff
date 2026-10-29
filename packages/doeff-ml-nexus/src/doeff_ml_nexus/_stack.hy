;;; Handler stack composition
;;; Provides a reference handler chain for doeff-ml-nexus programs.

(require doeff_hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff [run])
(import doeff_core_effects [reader state writer slog-handler scheduled])

(import doeff_ml_nexus._serializer [default-serializer])
(import doeff_ml_nexus._handlers.resolve [resolve-handler])
(import doeff_ml_nexus._handlers.docker [docker-run-handler])
(import doeff_ml_nexus._handlers.rsync [rsync-handler])
(import doeff_ml_nexus._handlers.file [write-file-handler])
(import doeff_docker [docker-build-handler image-push-handler])
(import doeff_docker [shell-run-handler])


(defn ml-nexus-interpreter [program * [env None]]  ; doeff: interpreter
  "Reference interpreter for doeff-ml-nexus programs."
  (setv resolved-env
    (if (is env None)
        {"source_root" "~/repos"
         "serializer" default-serializer}
        (| {"source_root" "~/repos"
            "serializer" default-serializer}
           (if (isinstance env dict) env (run env)))))

  (run (scheduled
    ;; slog-handler and writer keep their logs via Get/Put, so state must sit outside them.
    ;; h1(h2(p)): the first handler is the outermost (ADR-DOE-API-001 R16).
    ((state)
     ((reader :env resolved-env)
      (slog-handler
       (writer
        (resolve-handler
         (write-file-handler
          (rsync-handler
           (image-push-handler
            (docker-build-handler
             (docker-run-handler
              (shell-run-handler program))))))))))))))
