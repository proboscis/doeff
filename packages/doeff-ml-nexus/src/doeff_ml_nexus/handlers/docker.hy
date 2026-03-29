;;; ML-nexus Docker run handler.
;;; DockerBuild and ImagePush handlers are in doeff-docker.
;;; This module provides DockerRun handler with file-based cloudpickle exchange.

(require doeff_hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff [Resume Pass])
(import doeff_core_effects [Ask slog])

(import uuid)

(import doeff_docker.effects [DockerRun])
(import doeff_docker.handlers.docker [run-cmd])


(defk docker-run-handler [effect k]
  "Handle DockerRun: execute Program[T] in container via file-based cloudpickle.

   1. Write pickled program to temp file on host
   2. Docker run with volume mount, doeff run invokes p_run
   3. Read pickled result from output file
   4. Clean up"
  (if (isinstance effect DockerRun)
      (do
        (<- (slog :msg f"docker run: {effect.image} on {effect.host} gpu={effect.gpu}"))
        (<- serializer (Ask "serializer"))

        ;; Temp paths on host
        (setv run-id (cut (. (uuid.uuid4) hex) 0 8))
        (setv tmp-dir f"/tmp/doeff-run/{run-id}")
        (setv container-exchange "/tmp/doeff-exchange")

        ;; Create tmp dir and write pickled program
        (run-cmd ["mkdir" "-p" tmp-dir] :host effect.host)
        (setv pickled (.dumps serializer effect.program))
        (run-cmd ["tee" f"{tmp-dir}/program.pkl"] :host effect.host :stdin-data pickled)

        ;; Docker run args
        (setv parts ["docker" "run" "--rm"])
        (when effect.gpu
          (.extend parts ["--gpus" "all"]))
        (.extend parts ["-v" f"{tmp-dir}:{container-exchange}"])
        (for [m effect.mounts]
          (.extend parts ["-v" m]))
        (for [e effect.env-vars]
          (.extend parts ["-e" e]))
        (.extend parts ["-e" "DOEFF_DISABLE_PROFILE=1"
                        "-e" "DOEFF_DISABLE_RUNBOX=1"
                        "-e" f"DOEFF_INPUT={container-exchange}/program.pkl"
                        "-e" f"DOEFF_OUTPUT={container-exchange}/result.pkl"
                        effect.image
                        "uv" "run" "doeff" "run"
                        "--program" "doeff_ml_nexus.runner.p_run"
                        "--interpreter" "doeff_ml_nexus.runner.runner_interpreter"])

        ;; Execute
        (run-cmd parts :host effect.host)

        ;; Read result
        (setv proc (run-cmd ["cat" f"{tmp-dir}/result.pkl"] :host effect.host))
        (setv result (.loads serializer proc.stdout))

        ;; Cleanup
        (run-cmd ["rm" "-rf" tmp-dir] :host effect.host)

        (yield (Resume k result)))
      (yield (Pass effect k))))
