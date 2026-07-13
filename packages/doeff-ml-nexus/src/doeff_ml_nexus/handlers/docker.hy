;;; ML-nexus Docker run handler.
;;; DockerBuild and ImagePush handlers are in doeff-docker.
;;; This module provides DockerRun handler with file-based cloudpickle exchange.
;;;
;;; Handler clauses yield ShellRun effects instead of calling subprocess
;;; directly — the production shell-run-handler resolves them at the IO
;;; boundary.

(require doeff_hy.macros [<- defhandler])
(import doeff [do :as _doeff-do])
(import doeff_core_effects [Ask slog])

(import uuid)

(import doeff_docker.effects [DockerRun ShellRun])
(import doeff_docker.handlers.docker [_build-shell-args])


(defn _shell-run-checked [full-args * [stdin-data None]]
  "Build a ShellRun effect from args. Caller must yield and check result."
  (ShellRun :args (tuple full-args) :stdin-data stdin-data))


(defhandler docker-run-handler
  "Handle DockerRun: execute Program[T] in container via file-based cloudpickle.

   1. Write pickled program to temp file on host
   2. Docker run with volume mount, doeff run invokes p_run
   3. Read pickled result from output file
   4. Clean up"
  (DockerRun [image program host gpu mounts env-vars]
    (<- (slog :msg f"docker run: {image} on {host} gpu={gpu}"))
    (<- serializer (Ask "serializer"))

    ;; Temp paths on host
    (setv run-id (cut (. (uuid.uuid4) hex) 0 8))
    (setv tmp-dir f"/tmp/doeff-run/{run-id}")
    (setv container-exchange "/tmp/doeff-exchange")

    ;; Create tmp dir and write pickled program
    (setv mkdir-args (_build-shell-args ["mkdir" "-p" tmp-dir] :host host))
    (<- mkdir-result (ShellRun :args (tuple mkdir-args)))
    (when (!= mkdir-result.returncode 0)
      (raise (RuntimeError
               f"Command failed (rc={mkdir-result.returncode}):\n{mkdir-args}\nstderr: {(.decode mkdir-result.stderr)}")))

    (setv pickled (.dumps serializer program))
    (setv tee-args (_build-shell-args ["tee" f"{tmp-dir}/program.pkl"] :host host))
    (<- tee-result (ShellRun :args (tuple tee-args) :stdin-data pickled))
    (when (!= tee-result.returncode 0)
      (raise (RuntimeError
               f"Command failed (rc={tee-result.returncode}):\n{tee-args}\nstderr: {(.decode tee-result.stderr)}")))

    ;; Docker run args
    (setv parts ["docker" "run" "--rm"])
    (when gpu
      (.extend parts ["--gpus" "all"]))
    (.extend parts ["-v" f"{tmp-dir}:{container-exchange}"])
    (for [m mounts]
      (.extend parts ["-v" m]))
    (for [e env-vars]
      (.extend parts ["-e" e]))
    (.extend parts ["-e" "DOEFF_DISABLE_PROFILE=1"
                    "-e" "DOEFF_DISABLE_RUNBOX=1"
                    "-e" f"DOEFF_INPUT={container-exchange}/program.pkl"
                    "-e" f"DOEFF_OUTPUT={container-exchange}/result.pkl"
                    image
                    "uv" "run" "doeff" "run"
                    "--program" "doeff_ml_nexus.runner.p_run"
                    "--interpreter" "doeff_ml_nexus.runner.runner_interpreter"])

    ;; Execute
    (setv run-args (_build-shell-args parts :host host))
    (<- run-result (ShellRun :args (tuple run-args)))
    (when (!= run-result.returncode 0)
      (raise (RuntimeError
               f"Command failed (rc={run-result.returncode}):\n{run-args}\nstderr: {(.decode run-result.stderr)}")))

    ;; Read result
    (setv cat-args (_build-shell-args ["cat" f"{tmp-dir}/result.pkl"] :host host))
    (<- cat-result (ShellRun :args (tuple cat-args)))
    (when (!= cat-result.returncode 0)
      (raise (RuntimeError
               f"Command failed (rc={cat-result.returncode}):\n{cat-args}\nstderr: {(.decode cat-result.stderr)}")))
    (setv result (.loads serializer cat-result.stdout))

    ;; Cleanup
    (setv rm-args (_build-shell-args ["rm" "-rf" tmp-dir] :host host))
    (<- rm-result (ShellRun :args (tuple rm-args)))
    ;; Cleanup failure is non-fatal — do not raise

    (resume result)))
