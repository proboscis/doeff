;;; Dockerfile collector handler
;;; Collects Dockerfile instruction effects (From, Run, Copy, etc.)
;;; via WithObserve + Tell, then retrieves via state.

(require doeff_hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff [Resume Pass WithObserve WithHandler])
(import doeff_core_effects [Tell WriterTellEffect])

(import doeff_docker.effects [From Run Copy Workdir SetEnv Expose])


(defk dockerfile-collector-handler [effect k]
  "Handler that converts typed Dockerfile effects (From, Run, etc.)
   into Tell messages, then Resumes with None.
   Pair with WithObserve or Listen to collect the messages."
  (cond
    (isinstance effect From)
    (do (<- (Tell f"FROM {effect.image}"))
        (yield (Resume k None)))

    (isinstance effect Run)
    (do (<- (Tell f"RUN {effect.command}"))
        (yield (Resume k None)))

    (isinstance effect Copy)
    (do (<- (Tell f"COPY {effect.src} {effect.dst}"))
        (yield (Resume k None)))

    (isinstance effect Workdir)
    (do (<- (Tell f"WORKDIR {effect.path}"))
        (yield (Resume k None)))

    (isinstance effect SetEnv)
    (do (<- (Tell f"ENV {effect.key}={effect.value}"))
        (yield (Resume k None)))

    (isinstance effect Expose)
    (do (<- (Tell f"EXPOSE {effect.port}"))
        (yield (Resume k None)))

    True
    (yield (Pass effect k))))


(defn render-dockerfile [messages]
  "Convert collected WriterTellEffect list to Dockerfile string. Pure function."
  (.join "\n" (lfor m messages m.msg)))


(defk collect-dockerfile [image-program]
  "Run an image-definition Program, collect Dockerfile instructions.
   Uses WithObserve to capture Tell effects emitted by dockerfile-collector-handler.
   Returns Dockerfile string."
  (setv collected [])

  (defn _observer [effect]
    (when (isinstance effect WriterTellEffect)
      (.append collected effect)))

  (<- (WithObserve _observer
        (WithHandler dockerfile-collector-handler image-program)))
  (render-dockerfile collected))
