;;; Docker operation handlers
;;; Handles DockerBuild, ImagePush effects via shell commands.
;;; DockerRun is application-specific (depends on runner module) — not included here.
;;;
;;; Handler clauses yield ShellRun effects instead of calling subprocess
;;; directly — the production shell-run-handler (handlers/shell.hy) is the
;;; IO boundary that resolves them.

(require doeff_hy.macros [<- defhandler])
(import doeff [do :as _doeff-do])
(import doeff_core_effects [slog])

(import shlex)

(import doeff_docker.effects [DockerBuild ImagePush ShellRun])


(defn _build-shell-args [args * [host "localhost"]]
  "Build full command args, wrapping in SSH if host is not localhost."
  (if (= host "localhost")
      (list args)
      ["ssh" host (.join " " (lfor a args (shlex.quote (str a))))]))


(defhandler docker-build-handler
  "Handle DockerBuild: build Docker image from Dockerfile string."
  (DockerBuild [dockerfile tag context-path host]
    (<- (slog :msg f"docker build: {tag} on {host}"))
    (setv args ["docker" "build" "-t" tag "-f" "-" (str context-path)])
    (setv full-args (_build-shell-args args :host host))
    (<- result (ShellRun :args (tuple full-args)
                         :stdin-data (.encode dockerfile "utf-8")))
    (when (!= result.returncode 0)
      (raise (RuntimeError
               f"Command failed (rc={result.returncode}):\n{full-args}\nstderr: {(.decode result.stderr)}")))
    (resume tag)))


(defhandler image-push-handler
  "Handle ImagePush: tag and push image to registry."
  (ImagePush [local-tag remote-tag]
    (<- (slog :msg f"docker push: {local-tag} -> {remote-tag}"))
    ;; tag
    (setv tag-args ["docker" "tag" local-tag remote-tag])
    (<- tag-result (ShellRun :args (tuple tag-args)))
    (when (!= tag-result.returncode 0)
      (raise (RuntimeError
               f"Command failed (rc={tag-result.returncode}):\n{tag-args}\nstderr: {(.decode tag-result.stderr)}")))
    ;; push
    (setv push-args ["docker" "push" remote-tag])
    (<- push-result (ShellRun :args (tuple push-args)))
    (when (!= push-result.returncode 0)
      (raise (RuntimeError
               f"Command failed (rc={push-result.returncode}):\n{push-args}\nstderr: {(.decode push-result.stderr)}")))
    (resume remote-tag)))
