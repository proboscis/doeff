;;; Interpreter factories
;;; Each interpreter is a KleisliProgram: Program[T] -> Program[T]
;;; that composes effects to build+execute in a target environment.

(require doeff_hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff_core_effects [slog])

(import pathlib [Path])

(import doeff_docker.effects [DockerBuild DockerRun])
(import doeff_docker.handlers.dockerfile [collect-dockerfile])
(import doeff_ml_nexus.effects [Resolve RsyncTo])
(import doeff_ml_nexus.docker [uv-image uv-gpu-image])
(import doeff_ml_nexus.uv-deps [
  find-local-deps _find-top-level-deps
  rewrite-pyproject-for-container rewrite-uv-lock-for-container])
(import doeff_ml_nexus.context [prepare-build-context])


;; ===========================================================================
;; Remote uv interpreter
;; ===========================================================================

(defk make-remote-uv-interpreter [source-id host base-image gpu]
  "Create an interpreter that runs Program[T] on a remote Docker host.
   Automatically detects and bundles local path dependencies.
   Returns a function: Program[T] -> Program[T]"

  (setv tag f"{source-id}:latest")

  (defk _interpreter [program]
    (<- (slog :msg f"doeff-ml-nexus: {source-id} on {host} (gpu={gpu})"))

    ;; 1. Resolve source path
    (<- src-path (Resolve :target source-id :kind Path))

    ;; 2. Prepare build context on remote host
    ;;    - rsync source (excluding .venv, __pycache__, .git, etc.)
    ;;    - rsync local deps (if any)
    ;;    - replace pyproject.toml/uv.lock with rewritten versions in context
    (setv context-path (Path f"/tmp/doeff-build-context/{source-id}"))
    (<- (prepare-build-context src-path host (str context-path)))

    ;; 3. Build Dockerfile via effect collection
    (setv image-prog (if gpu
                         (uv-gpu-image base-image :project-root src-path)
                         (uv-image base-image :project-root src-path)))
    (<- dockerfile (collect-dockerfile image-prog))
    (<- (slog :msg f"doeff-ml-nexus: Dockerfile ({(len (.splitlines dockerfile))} lines)"))

    ;; 4. Docker build on remote
    (<- (DockerBuild :dockerfile dockerfile :tag tag
                     :context-path context-path :host host))

    ;; 5. Execute program via cloudpickle
    (<- result (DockerRun :image tag :program program
                          :host host :gpu gpu))
    result)

  _interpreter)


;; ===========================================================================
;; Local uv interpreter (for testing)
;; ===========================================================================

(defk make-local-uv-interpreter [source-id base-image]
  "Create an interpreter that runs Program[T] in a local Docker container."

  (setv tag f"{source-id}:local")

  (defk _interpreter [program]
    (<- src-path (Resolve :target source-id :kind Path))
    (<- (prepare-build-context src-path "localhost" (str src-path)))
    (setv image-prog (uv-image base-image :project-root src-path))
    (<- dockerfile (collect-dockerfile image-prog))
    (<- (DockerBuild :dockerfile dockerfile :tag tag
                     :context-path src-path :host "localhost"))
    (<- result (DockerRun :image tag :program program
                          :host "localhost"))
    result)

  _interpreter)
