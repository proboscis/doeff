;;; Dockerfile collector handler
;;; Collects Dockerfile instruction effects (From, Run, Copy, etc.)
;;; via WithObserve + Tell, then retrieves via state.

(require doeff_hy.macros [defk <- defhandler])
(import doeff [do :as _doeff-do])
(import doeff [Program WithObserve])
(import doeff_core_effects [Tell WriterTellEffect])

(import doeff_docker.effects [From Run Copy Workdir SetEnv Expose])


(defhandler dockerfile-collector-handler
  "Handler that converts typed Dockerfile effects (From, Run, etc.)
   into Tell messages, then Resumes with None.
   Pair with WithObserve or Listen to collect the messages."
  (From [image]
    (<- (Tell f"FROM {image}"))
    (resume None))

  (Run [command]
    (<- (Tell f"RUN {command}"))
    (resume None))

  (Copy [src dst]
    (<- (Tell f"COPY {src} {dst}"))
    (resume None))

  (Workdir [path]
    (<- (Tell f"WORKDIR {path}"))
    (resume None))

  (SetEnv [key value]
    (<- (Tell f"ENV {key}={value}"))
    (resume None))

  (Expose [port]
    (<- (Tell f"EXPOSE {port}"))
    (resume None)))


(defk render-dockerfile [messages]
  "Convert collected WriterTellEffect list to Dockerfile string. Pure function."
  {:pre [(: messages list)]
   :post [(: % str)]}
  (.join "\n" (lfor m messages m.msg)))


(defk collect-dockerfile [image-program]
  "Run an image-definition Program, collect Dockerfile instructions.
   Uses WithObserve to capture Tell effects emitted by dockerfile-collector-handler.
   Returns Dockerfile string."
  {:pre [(: image-program Program)]
   :post [(: % str)]}
  (setv collected [])

  (defn _observer [effect]
    (when (isinstance effect WriterTellEffect)
      (.append collected effect)))

  (<- (WithObserve _observer
        (dockerfile-collector-handler image-program)))
  (<- dockerfile (render-dockerfile collected))
  dockerfile)
