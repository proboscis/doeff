;;; Build context preparation
;;; Prepares a clean Docker build context directory on the target host.
;;;
;;; The context directory contains:
;;;   pyproject.toml  — rewritten with container paths (if local deps exist)
;;;   uv.lock         — rewritten with container paths (if local deps exist)
;;;   deps/<name>/    — local dependency sources (only needed subdirs)
;;;   src/, ...       — project source code

(require doeff_hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff_core_effects [slog])

(import pathlib [Path])

(import doeff_ml_nexus.effects [RsyncTo WriteFile])
(import doeff_ml_nexus.uv-deps [
  find-local-deps _find-top-level-deps _is-subpath
  rewrite-pyproject-for-container rewrite-uv-lock-for-container])


(setv _SOURCE_EXCLUDES
  #(".venv" "__pycache__" ".git" ".ruff_cache" ".pytest_cache"
    "*.egg-info" "target" ".orch" ".idea" ".vscode" ".claude"
    "dist" ".DS_Store" "*.pyc"))


(setv _DEP_EXCLUDES
  #("target" ".venv" "__pycache__" ".git" ".ruff_cache"
    ".pytest_cache" "*.egg-info" ".orch" "ide-plugins" ".idea"
    "dist" ".DS_Store" "*.pyc" "*.dSYM"
    ;; doeff monorepo: skip non-essential top-level dirs
    "python" "tmp" "notes" "examples" "Untitled" "None"
    "*.log" "*.db" "VAULT" "docs" "tests" "benchmarks"
    "publications" "specs" "scripts" "tools"
    ".sisyphus" ".serena" ".claude" ".vscode" ".github"
    ".pre-commit*" ".semgrep*" ".pinjected*"
    "durable_workflow*" "conftest.py" "Makefile"
    "LICENSE" "CHANGELOG*" "*.yaml" "*.yml"
    "AGENTS.md" "ORCH_CONTROL_PROMPT.md"
    ".pr87*" "pr87*"))


(defk prepare-build-context [project-root host context-path]
  "Prepare a clean Docker build context on the target host.

   1. Rsync project source to context-path
   2. Detect local deps, rsync only needed subdirs to context-path/deps/<name>/
   3. If local deps exist, overwrite pyproject.toml and uv.lock
      in context with rewritten versions (container paths)."
  (<- (slog :msg f"Preparing build context at {host}:{context-path}"))

  ;; 1. Rsync project source
  (<- (RsyncTo :src project-root :host host
               :dst-path context-path :excludes _SOURCE_EXCLUDES))

  ;; 2. Detect and rsync local deps (only needed subdirs)
  (setv deps (find-local-deps project-root))
  (when deps
    (setv top-deps (_find-top-level-deps deps))
    (for [dep top-deps]
      (<- (slog :msg f"Syncing local dep: {dep.name}"))
      (<- (RsyncTo :src dep.local-path :host host
                   :dst-path f"{context-path}/deps/{dep.name}"
                   :excludes _DEP_EXCLUDES)))

    ;; 3. Overwrite pyproject.toml and uv.lock with rewritten versions
    (setv rewritten-pyproject (rewrite-pyproject-for-container project-root "/deps"))
    (setv rewritten-lock (rewrite-uv-lock-for-container project-root "/deps"))

    (<- (WriteFile :host host :path f"{context-path}/pyproject.toml"
                   :content rewritten-pyproject))
    (<- (WriteFile :host host :path f"{context-path}/uv.lock"
                   :content rewritten-lock)))

  deps)
