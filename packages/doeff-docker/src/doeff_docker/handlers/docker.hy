;;; Docker operation handlers
;;; Handles DockerBuild, ImagePush effects via shell commands.
;;; DockerRun is application-specific (depends on runner module) — not included here.

(require doeff_hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff [Resume Pass])
(import doeff_core_effects [slog])

(import subprocess)
(import shlex)
(import pathlib [Path])

(import doeff_docker.effects [DockerBuild ImagePush])


(defn run-cmd [args * [host "localhost"] [stdin-data None]]
  "Run a command as list of args, optionally on a remote host via SSH.
   Returns CompletedProcess."
  (setv full-args
    (if (= host "localhost")
        args
        ["ssh" host (.join " " (lfor a args (shlex.quote (str a))))]))
  (setv proc (subprocess.run full-args :capture-output True :input stdin-data))
  (when (!= proc.returncode 0)
    (raise (RuntimeError f"Command failed (rc={proc.returncode}):\n{full-args}\nstderr: {(.decode proc.stderr)}")))
  proc)


(defk docker-build-handler [effect k]
  "Handle DockerBuild: build Docker image from Dockerfile string."
  (if (isinstance effect DockerBuild)
      (do
        (<- (slog :msg f"docker build: {effect.tag} on {effect.host}"))
        (setv args ["docker" "build" "-t" effect.tag "-f" "-" (str effect.context-path)])
        (run-cmd args :host effect.host
                 :stdin-data (.encode effect.dockerfile "utf-8"))
        (yield (Resume k effect.tag)))
      (yield (Pass effect k))))


(defk image-push-handler [effect k]
  "Handle ImagePush: tag and push image to registry."
  (if (isinstance effect ImagePush)
      (do
        (<- (slog :msg f"docker push: {effect.local-tag} -> {effect.remote-tag}"))
        (run-cmd ["docker" "tag" effect.local-tag effect.remote-tag])
        (run-cmd ["docker" "push" effect.remote-tag])
        (yield (Resume k effect.remote-tag)))
      (yield (Pass effect k))))
