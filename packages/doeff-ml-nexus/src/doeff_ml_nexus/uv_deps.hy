;;; Parse pyproject.toml for local path dependencies (tool.uv.sources).
;;;
;;; Detects dependencies like:
;;;   [tool.uv.sources]
;;;   doeff = { path = "../doeff", editable = true }
;;;   doeff-vm = { path = "../doeff/packages/doeff-vm", editable = true }

(import dataclasses [dataclass])
(import pathlib [Path])
(import re)

(try
  (import tomllib)
  (except [ImportError]
    (import tomli :as tomllib)))


(defclass [(dataclass :frozen True)] LocalDep []
  "A local path dependency found in pyproject.toml."
  #^ str name
  #^ Path local-path
  #^ str relative-path)


(defn find-local-deps [project-root]
  "Parse pyproject.toml and find all [tool.uv.sources] with path deps."
  (setv pyproject-path (/ project-root "pyproject.toml"))
  (when (not (.exists pyproject-path))
    (return []))
  (with [f (open pyproject-path "rb")]
    (setv data (tomllib.load f)))
  (setv sources (.get (.get (.get data "tool" {}) "uv" {}) "sources" {}))
  (lfor #(name spec) (.items sources)
    :if (and (isinstance spec dict) (in "path" spec))
    (let [rel-path (get spec "path")
          abs-path (.resolve (/ project-root rel-path))]
      (LocalDep :name name :local-path abs-path :relative-path rel-path))))


(defn _find-top-level-deps [deps]
  "Find deps that are not subdirectories of other deps."
  (lfor dep deps
    :if (not (any (gfor other deps
                    :if (!= dep.local-path other.local-path)
                    (_is-subpath dep.local-path other.local-path))))
    dep))


(defn _is-subpath [child parent]
  "Check if child is a subpath of parent."
  (try
    (.relative-to child parent)
    True
    (except [ValueError] False)))


(defn _map-to-container-path [dep top-level-deps container-root]
  "Map a dep's local path to its container path via top-level dep."
  (for [top top-level-deps]
    (when (= dep.local-path top.local-path)
      (return f"{container-root}/{top.name}"))
    (when (_is-subpath dep.local-path top.local-path)
      (setv sub (.relative-to dep.local-path top.local-path))
      (return f"{container-root}/{top.name}/{sub}")))
  f"{container-root}/{dep.name}")


(defn rewrite-pyproject-for-container [project-root [container-sources-root "/deps"]]
  "Rewrite pyproject.toml replacing local path deps with container paths."
  (setv content (.read-text (/ project-root "pyproject.toml")))
  (setv deps (find-local-deps project-root))
  (setv top-level (_find-top-level-deps deps))
  (for [dep deps]
    (setv container-path (_map-to-container-path dep top-level container-sources-root))
    (setv content (.replace content
                    f"path = \"{dep.relative-path}\""
                    f"path = \"{container-path}\"")))
  content)


(defn rewrite-uv-lock-for-container [project-root [container-sources-root "/deps"]]
  "Rewrite uv.lock replacing all local editable paths with container paths."
  (setv content (.read-text (/ project-root "uv.lock")))
  (setv deps (find-local-deps project-root))
  (setv top-level (_find-top-level-deps deps))

  (defn replace-editable [match]
    (setv rel-path (.group match 1))
    (if (or (.startswith rel-path "..") (.startswith rel-path "./"))
        (do
          (setv resolved (.resolve (/ project-root rel-path)))
          (for [dep top-level]
            (try
              (setv sub (.relative-to resolved dep.local-path))
              (setv container-path f"{container-sources-root}/{dep.name}")
              (when (!= (str sub) ".")
                (setv container-path f"{container-path}/{sub}"))
              (return f"editable = \"{container-path}\"")
              (except [ValueError] None)))
          (setv name (. (Path rel-path) name))
          f"editable = \"{container-sources-root}/{name}\"")
        (.group match 0)))

  (re.sub "editable = \"([^\"]+)\"" replace-editable content))
