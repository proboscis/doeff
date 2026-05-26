;;; Docker operation handlers
;;; Handles DockerBuild, ImagePush effects via shell commands.
;;; DockerRun is application-specific (depends on runner module) — not included here.

(require doeff_hy.macros [<- defhandler])
(import doeff [do :as _doeff-do])
(import doeff_core_effects [slog])

(import subprocess)
(import shlex)
(import pathlib [Path])

(import doeff_docker.effects [DockerBuild ImagePush])


;; Plain callable: shared synchronous subprocess boundary used by handler clauses.
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


(defhandler docker-build-handler
  "Handle DockerBuild: build Docker image from Dockerfile string."
  (DockerBuild [dockerfile tag context-path host]
    (<- (slog :msg f"docker build: {tag} on {host}"))
    (setv args ["docker" "build" "-t" tag "-f" "-" (str context-path)])
    (run-cmd args :host host
             :stdin-data (.encode dockerfile "utf-8"))
    (resume tag)))


(defhandler image-push-handler
  "Handle ImagePush: tag and push image to registry."
  (ImagePush [local-tag remote-tag]
    (<- (slog :msg f"docker push: {local-tag} -> {remote-tag}"))
    (run-cmd ["docker" "tag" local-tag remote-tag])
    (run-cmd ["docker" "push" remote-tag])
    (resume remote-tag)))
