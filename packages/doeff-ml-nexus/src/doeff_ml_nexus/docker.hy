;;; Dockerfile construction helpers
;;; Dockerfile = Program. Each instruction is an effect.

(require doeff_hy.macros [defk defprogram <-])
(import doeff [do :as _doeff-do])

(import pathlib [Path])

(import doeff_docker.effects [From Run Copy Workdir SetEnv Expose])
(import doeff_ml_nexus.effects [Resolve RsyncTo])
(import doeff_ml_nexus.uv-deps [find-local-deps _find-top-level-deps])


;; ===========================================================================
;; Helpers — compose effects for common Dockerfile patterns
;; ===========================================================================

(defk copy-from [src dst build-context-path]
  "Copy a file from outside build context: rsync to context, then COPY."
  (<- (RsyncTo :src src :host "localhost"
               :dst-path (str (/ build-context-path (Path src) .name))))
  (<- (Copy :src (. (Path src) name) :dst dst)))

(defk copy-resolved [source-id dst build-context-path]
  "Resolve a source id to a path, rsync into context, then COPY."
  (<- src-path (Resolve :target source-id :kind Path))
  (copy-from src-path dst build-context-path))

(defk apt-install [#* packages]
  "RUN apt-get install."
  (<- (Run :command
        (+ "apt-get update && apt-get install -y --no-install-recommends "
           (.join " " packages)
           " && rm -rf /var/lib/apt/lists/*"))))

(defk install-uv []
  "Install uv into the image."
  (<- (Run :command "curl -LsSf https://astral.sh/uv/install.sh | sh"))
  (<- (SetEnv :key "PATH" :value "/root/.local/bin:$PATH")))

(defk install-rust []
  "Install Rust toolchain into the image."
  (<- (Run :command "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"))
  (<- (SetEnv :key "PATH" :value "/root/.cargo/bin:$PATH")))


;; ===========================================================================
;; Local dependency handling
;; ===========================================================================

(defk copy-local-deps [deps]
  "COPY top-level local deps into container at /deps/<name>.
   Sub-deps (e.g. doeff-vm inside doeff) are included automatically."
  (setv top-deps (_find-top-level-deps deps))
  (for [dep top-deps]
    (<- (Copy :src f"deps/{dep.name}" :dst f"/deps/{dep.name}"))))

(defn has-rust-extension [deps]
  "Check if any local dep requires Rust build (has Cargo.toml)."
  (any (lfor dep deps (.exists (/ dep.local-path "Cargo.toml")))))


;; ===========================================================================
;; uv project image — with local dependency support
;; ===========================================================================

(defk uv-image-core [base-image project-root * [gpu False]]
  "Core uv project Dockerfile as a Program.

   Build context is expected to be prepared by the interpreter:
     - pyproject.toml: rewritten version (if local deps exist)
     - uv.lock: rewritten version (if local deps exist)
     - deps/<name>/: local dep sources (if any)
     - src/, etc.: project source code

   Produces a well-layered image:
     1. Base + system deps + uv (+ rust if needed, + GPU env if gpu)
     2. Local deps COPY (if any)
     3. Dependency layer (pyproject.toml + uv.lock only)
     4. Full source + project install"
  (<- (From :image base-image))
  (when gpu
    (<- (SetEnv :key "NVIDIA_VISIBLE_DEVICES" :value "all"))
    (<- (SetEnv :key "NVIDIA_DRIVER_CAPABILITIES" :value "compute,utility")))
  (<- (apt-install "curl" "git" "build-essential"))
  (<- (install-uv))

  ;; Check for local deps and rust extensions
  (setv deps (if project-root (find-local-deps project-root) []))
  (when (and deps (has-rust-extension deps))
    (<- (install-rust)))

  ;; Copy local deps if any
  (when deps
    (<- (copy-local-deps deps)))

  ;; Dependency layer
  (<- (Copy :src "pyproject.toml" :dst "/app/"))
  (<- (Copy :src "uv.lock" :dst "/app/"))
  (<- (Workdir :path "/app"))
  (<- (Run :command "uv sync --frozen --no-install-project"))

  ;; Source layer
  (<- (Copy :src "." :dst "/app/"))
  (<- (Run :command "uv sync --frozen")))


(defk uv-image [base-image * [project-root None]]
  "Standard uv project Dockerfile."
  (<- (uv-image-core base-image project-root)))

(defk uv-gpu-image [base-image * [project-root None]]
  "uv project with NVIDIA GPU support."
  (<- (uv-image-core base-image project-root :gpu True)))
